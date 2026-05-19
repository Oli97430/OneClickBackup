"""Disk and partition cloning / OS migration mixin.

Provides :class:`CloneMixin` which is mixed into
:class:`~src.core.backup.BackupManager` to supply ``clone_disk``,
``clone_partition``, and ``migrate_os``.
"""

import json
import os
import subprocess
from typing import Optional

from src.utils.helpers import format_bytes, run_diskpart, run_powershell

from src.core.backup import (
    BackupError,
    _require_admin,
    _disk_exists,
    _get_disk_partitions,
    _get_disk_size,
    _get_disk_style,
    _get_partition_drive_letter,
    _is_system_disk,
    _clean_and_initialize_disk,
    _create_partition,
)


class CloneMixin:
    """Mixin that adds clone and OS-migration methods to BackupManager.

    Expects the host class to provide ``_log``, ``_report_progress``,
    ``_check_cancelled``, ``_cancel_event``, ``_run_robocopy``, and
    ``_run_cancellable``.
    """

    # ======================================================================
    # Clone Operations
    # ======================================================================

    def clone_disk(
        self,
        source_disk: int,
        target_disk: int,
        resize_to_fit: bool = True,
    ) -> bool:
        """Clone every partition from *source_disk* to *target_disk*.

        This is a destructive operation: the target disk is wiped and
        re-initialised to match the source partition style.

        Steps:
            1. Validate both disks exist and target is large enough.
            2. Clean and initialise target.
            3. Re-create partitions on target.
            4. Copy data for each mounted partition.
            5. If the source is the system disk, fix the BCD on target.

        Args:
            source_disk: Physical disk number to clone from.
            target_disk: Physical disk number to clone to.
            resize_to_fit: When *True* and target is larger, expand the
                last data partition to fill remaining space.

        Returns:
            *True* on success.
        """
        _require_admin("Disk clone")
        self._cancel_event.clear()

        if source_disk == target_disk:
            raise BackupError("Source and target disk must be different.")

        if not _disk_exists(source_disk):
            raise BackupError(f"Source disk {source_disk} does not exist.")
        if not _disk_exists(target_disk):
            raise BackupError(f"Target disk {target_disk} does not exist.")

        source_size = _get_disk_size(source_disk)
        target_size = _get_disk_size(target_disk)

        src_partitions = _get_disk_partitions(source_disk)
        if not src_partitions:
            raise BackupError(f"No partitions on source disk {source_disk}.")

        # Total required space (sum of partition sizes)
        required = sum(p["Size"] for p in src_partitions)
        if target_size < required and not resize_to_fit:
            raise BackupError(
                f"Target disk ({format_bytes(target_size)}) is too small for "
                f"source data ({format_bytes(required)})."
            )

        style = _get_disk_style(source_disk)
        is_system = _is_system_disk(source_disk)

        # 1. Clean target
        self._report_progress("Preparing target disk...", 5.0)
        self._check_cancelled()
        _clean_and_initialize_disk(target_disk, style)

        # 2. Recreate partitions and copy data
        total_parts = len(src_partitions)
        windows_letter_on_target: Optional[str] = None
        efi_letter_on_target: Optional[str] = None

        for idx, part in enumerate(src_partitions):
            self._check_cancelled()
            pnum = part["PartitionNumber"]
            ptype = part["Type"].lower()
            gpt_type = part["GptType"].lower() if part["GptType"] else ""
            letter = part["DriveLetter"]
            size = part["Size"]

            pct = 10.0 + (80.0 * idx / total_parts)
            self._report_progress(
                f"Cloning partition {pnum} ({ptype})...", pct
            )

            # Determine partition characteristics
            is_efi = "efi" in ptype or "c12a7328" in gpt_type
            is_msr = "msr" in ptype or "reserved" in ptype or "e3c9e316" in gpt_type
            is_recovery = "recovery" in ptype

            # Last partition: use remaining space if resize_to_fit
            use_max = resize_to_fit and (idx == total_parts - 1) and not is_efi and not is_msr

            fs = "FAT32" if is_efi else "NTFS"

            try:
                assigned = _create_partition(
                    disk_index=target_disk,
                    size_bytes=0 if use_max else size,
                    fs=fs,
                    is_efi=is_efi,
                    is_msr=is_msr,
                    use_maximum=use_max,
                )
            except BackupError as exc:
                self._log.error(
                    "Failed to create partition %d on target: %s", pnum, exc
                )
                continue

            # Copy data if source has a drive letter and target got one
            if letter and assigned:
                src_root = f"{letter}:\\"
                dst_root = f"{assigned}:\\"
                if os.path.isdir(src_root):
                    self._run_robocopy(
                        src_root,
                        dst_root,
                        options=[
                            "/E", "/COPYALL", "/R:1", "/W:1",
                            "/XD", "System Volume Information", "$Recycle.Bin",
                            "/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
                            "/NFL", "/NDL", "/NP",
                        ],
                    )

                # Track system and EFI letters for boot fixup
                if letter.upper() == "C":
                    windows_letter_on_target = assigned
                if is_efi:
                    efi_letter_on_target = assigned

        # 3. Fix boot config if cloning the system disk
        if is_system and windows_letter_on_target:
            self._report_progress("Fixing boot configuration...", 92.0)
            self._fix_boot_config(
                windows_letter_on_target, efi_letter_on_target
            )

        self._report_progress("Disk clone complete.", 100.0)
        self._log.info(
            "Cloned disk %d -> %d successfully.", source_disk, target_disk
        )
        return True

    # ------------------------------------------------------------------

    def clone_partition(
        self,
        source_disk: int,
        source_partition: int,
        target_disk: int,
        target_partition: int,
    ) -> bool:
        """Clone a single partition's files to another existing partition.

        Both partitions must have assigned drive letters.

        Returns *True* on success.
        """
        _require_admin("Partition clone")
        self._cancel_event.clear()

        src_letter = _get_partition_drive_letter(source_disk, source_partition)
        dst_letter = _get_partition_drive_letter(target_disk, target_partition)

        if not src_letter:
            raise BackupError(
                f"Source partition {source_partition} on disk {source_disk} "
                "has no drive letter."
            )
        if not dst_letter:
            raise BackupError(
                f"Target partition {target_partition} on disk {target_disk} "
                "has no drive letter."
            )

        src_root = f"{src_letter}:\\"
        dst_root = f"{dst_letter}:\\"

        self._report_progress(
            f"Cloning {src_root} -> {dst_root}...", 5.0
        )
        self._check_cancelled()

        ok = self._run_robocopy(
            src_root,
            dst_root,
            options=[
                "/MIR", "/COPYALL", "/R:1", "/W:1",
                "/XD", "System Volume Information", "$Recycle.Bin",
                "/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
                "/NFL", "/NDL", "/NP",
            ],
        )

        self._report_progress("Partition clone complete.", 100.0)
        return ok

    # ------------------------------------------------------------------

    def migrate_os(self, target_disk: int) -> bool:
        """Migrate the running Windows installation to *target_disk*.

        Creates the standard UEFI partition layout (EFI + MSR + Windows)
        on the target, copies the C:\\ volume, and fixes the BCD.

        For MBR/legacy BIOS systems the layout is simplified to a single
        active NTFS boot partition.

        Args:
            target_disk: Physical disk number that will receive the OS.

        Returns:
            *True* on success.
        """
        _require_admin("OS migration")
        self._cancel_event.clear()

        if not _disk_exists(target_disk):
            raise BackupError(f"Target disk {target_disk} does not exist.")

        # Find the current system disk (the one containing C:)
        system_disk = self._find_system_disk()
        if system_disk is None:
            raise BackupError("Could not determine the current system disk.")

        if system_disk == target_disk:
            raise BackupError(
                "Target disk is the current system disk.  "
                "Choose a different disk for migration."
            )

        style = _get_disk_style(system_disk)
        is_uefi = style == "GPT"

        # 1. Clean and initialise target
        self._report_progress("Preparing target disk...", 5.0)
        self._check_cancelled()
        _clean_and_initialize_disk(target_disk, style)

        efi_letter: Optional[str] = None
        windows_letter: Optional[str] = None

        if is_uefi:
            # 2a. UEFI layout: EFI (100 MB) + MSR (16 MB) + Windows (rest)
            self._report_progress("Creating EFI partition...", 10.0)
            efi_letter = _create_partition(
                target_disk,
                size_bytes=100 * 1024 * 1024,
                is_efi=True,
            )

            self._report_progress("Creating MSR partition...", 15.0)
            _create_partition(
                target_disk,
                size_bytes=16 * 1024 * 1024,
                is_msr=True,
            )

            self._report_progress("Creating Windows partition...", 20.0)
            windows_letter = _create_partition(
                target_disk,
                fs="NTFS",
                use_maximum=True,
            )
        else:
            # 2b. MBR layout: single active NTFS partition
            self._report_progress("Creating boot partition...", 10.0)
            windows_letter = _create_partition(
                target_disk,
                fs="NTFS",
                use_maximum=True,
            )
            # Mark as active via diskpart
            if windows_letter:
                run_diskpart([
                    f"select disk {target_disk}",
                    "select partition 1",
                    "active",
                ])

        if not windows_letter:
            raise BackupError(
                "Failed to create Windows partition on target disk."
            )

        # 3. Copy Windows volume
        self._report_progress("Copying Windows files...", 25.0)
        self._check_cancelled()

        src = "C:\\"
        dst = f"{windows_letter}:\\"
        ok = self._run_robocopy(
            src,
            dst,
            options=[
                "/E", "/COPYALL", "/DCOPY:DAT", "/R:1", "/W:1",
                "/XD", "System Volume Information", "$Recycle.Bin",
                "/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
                "/NFL", "/NDL", "/NP",
            ],
        )
        if not ok:
            self._log.warning("robocopy reported errors during OS copy.")

        # 4. Copy EFI files if UEFI
        if is_uefi and efi_letter:
            self._report_progress("Copying EFI boot files...", 85.0)
            self._copy_efi_partition(efi_letter)

        # 5. Fix BCD
        self._report_progress("Fixing boot configuration...", 90.0)
        self._fix_boot_config(windows_letter, efi_letter)

        self._report_progress("OS migration complete.", 100.0)
        self._log.info("OS migrated to disk %d.", target_disk)
        return True

    # ------------------------------------------------------------------
    # Private helpers used by clone/migration
    # ------------------------------------------------------------------

    def _find_system_disk(self) -> Optional[int]:
        """Return the disk number that holds the C:\\ partition."""
        cmd = (
            "Get-Partition -DriveLetter C -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty DiskNumber"
        )
        stdout, _, rc = run_powershell(cmd)
        if rc == 0 and stdout.strip().isdigit():
            return int(stdout.strip())
        return None

    def _fix_boot_config(
        self,
        windows_letter: str,
        efi_letter: Optional[str] = None,
    ) -> None:
        """Run ``bcdboot`` to install boot files on the target disk.

        For UEFI systems with an EFI partition, ``/f UEFI`` is used.
        For MBR systems or when no EFI letter is provided, ``/f BIOS``
        or ``/f ALL`` is used.

        Args:
            windows_letter: Drive letter of the copied Windows partition.
            efi_letter: Drive letter of the EFI system partition (if any).
        """
        windows_dir = f"{windows_letter}:\\Windows"
        if not os.path.isdir(windows_dir):
            self._log.warning(
                "Windows directory not found at %s; skipping BCD fix.",
                windows_dir,
            )
            return

        if efi_letter:
            cmd = ["bcdboot", windows_dir, "/s", f"{efi_letter}:", "/f", "UEFI"]
        else:
            cmd = ["bcdboot", windows_dir, "/f", "ALL"]

        self._log.info("Fixing boot config: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            self._log.error(
                "bcdboot failed (exit %d): %s",
                result.returncode,
                result.stderr,
            )
            raise BackupError(f"bcdboot failed: {result.stderr}")

        self._log.info("Boot configuration updated successfully.")

    def _copy_efi_partition(self, target_efi_letter: str) -> None:
        """Copy the current system EFI partition contents to the target.

        Temporarily mounts the source EFI partition to a free letter,
        copies via robocopy, then removes the letter.
        """
        # Find the source EFI partition
        cmd = (
            "Get-Partition | Where-Object { $_.GptType -eq "
            "'{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' -and $_.DriveLetter -eq '' } | "
            "Select-Object DiskNumber, PartitionNumber | "
            "ConvertTo-Json -Compress"
        )
        stdout, _, rc = run_powershell(cmd)
        if rc != 0 or not stdout.strip():
            self._log.info("No unmounted EFI partition found; using bcdboot only.")
            return

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return

        if isinstance(data, list):
            data = data[0]  # Use the first EFI partition

        src_disk = data.get("DiskNumber")
        src_part = data.get("PartitionNumber")
        if src_disk is None or src_part is None:
            return

        # Pick a temporary letter
        temp_letter = self._find_free_drive_letter()
        if not temp_letter:
            self._log.warning("No free drive letter for EFI mount.")
            return

        # Assign letter
        assign_cmd = (
            f"Get-Partition -DiskNumber {src_disk} -PartitionNumber {src_part} | "
            f"Set-Partition -NewDriveLetter {temp_letter}"
        )
        run_powershell(assign_cmd)

        try:
            src_root = f"{temp_letter}:\\"
            dst_root = f"{target_efi_letter}:\\"
            if os.path.isdir(src_root):
                self._run_robocopy(
                    src_root,
                    dst_root,
                    options=["/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NP"],
                )
        finally:
            # Remove the temporary letter
            remove_cmd = (
                f"Get-Partition -DiskNumber {src_disk} -PartitionNumber {src_part} | "
                f"Remove-PartitionAccessPath -AccessPath '{temp_letter}:\\'"
            )
            run_powershell(remove_cmd)

    def _find_free_drive_letter(self) -> Optional[str]:
        """Return an unused drive letter (Z down to D)."""
        cmd = "Get-PSDrive -PSProvider FileSystem | Select-Object -ExpandProperty Name"
        stdout, _, rc = run_powershell(cmd)
        used = set()
        if rc == 0:
            for line in stdout.splitlines():
                ch = line.strip().upper()
                if len(ch) == 1 and ch.isalpha():
                    used.add(ch)

        for code in range(ord("Z"), ord("D") - 1, -1):
            letter = chr(code)
            if letter not in used:
                return letter

        return None
