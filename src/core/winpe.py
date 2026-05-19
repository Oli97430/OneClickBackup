"""WinPE bootable-disk creation mixin.

Provides :class:`WinPEMixin` which is mixed into
:class:`~src.core.backup.BackupManager` to supply ``create_winpe_disk``
and related helpers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from src.utils.helpers import run_diskpart, run_powershell

from src.core.backup import (
    BackupError,
    _require_admin,
)


class WinPEMixin:
    """Mixin that adds WinPE bootable-disk creation to BackupManager.

    Expects the host class to provide ``_log``, ``_report_progress``,
    ``_check_cancelled``, and ``_cancel_event``.
    """

    # ======================================================================
    # WinPE Operations
    # ======================================================================

    def create_winpe_disk(self, target_drive_letter: str) -> bool:
        """Create a WinPE bootable USB disk.

        Attempts to use the Windows ADK ``copype`` + ``MakeWinPEMedia``
        pipeline.  If ADK is not installed, falls back to a basic
        bootable USB created with diskpart + ``bootsect``.

        Args:
            target_drive_letter: Drive letter of the USB drive (e.g. ``"E"``).

        Returns:
            *True* on success.
        """
        _require_admin("WinPE disk creation")
        self._cancel_event.clear()

        target_drive_letter = target_drive_letter.upper().rstrip(":")
        if not target_drive_letter.isalpha() or len(target_drive_letter) != 1:
            raise BackupError(f"Invalid drive letter: {target_drive_letter}")

        prereqs = self.check_winpe_prerequisites()

        if prereqs["adk_installed"] and prereqs["winpe_installed"]:
            return self._create_winpe_adk(target_drive_letter, prereqs["adk_path"])

        self._log.info(
            "ADK/WinPE addon not found.  Using basic bootable USB method."
        )
        return self._create_winpe_basic(target_drive_letter)

    def check_winpe_prerequisites(self) -> dict:
        """Check whether the Windows ADK and WinPE add-on are installed.

        Returns:
            A dict with keys ``adk_installed`` (bool), ``winpe_installed``
            (bool), and ``adk_path`` (str or empty).
        """
        result: dict = {
            "adk_installed": False,
            "winpe_installed": False,
            "adk_path": "",
        }

        # Check registry for ADK install path
        cmd = (
            "Get-ItemProperty -Path "
            "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows Kits\\Installed Roots' "
            "-Name KitsRoot10 -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty KitsRoot10"
        )
        stdout, _, rc = run_powershell(cmd)
        if rc == 0 and stdout.strip():
            adk_root = stdout.strip()
            result["adk_path"] = adk_root
            # ADK is installed if the Assessment and Deployment Kit folder exists
            if os.path.isdir(os.path.join(adk_root, "Assessment and Deployment Kit")):
                result["adk_installed"] = True
            # WinPE addon lives under Windows Preinstallation Environment
            winpe_dir = os.path.join(
                adk_root,
                "Assessment and Deployment Kit",
                "Windows Preinstallation Environment",
            )
            if os.path.isdir(winpe_dir):
                result["winpe_installed"] = True

        return result

    def _create_winpe_adk(
        self, target_letter: str, adk_path: str
    ) -> bool:
        """Build WinPE using the full ADK toolchain."""
        self._report_progress("Building WinPE image from ADK...", 5.0)

        work_dir = os.path.join(
            tempfile.gettempdir(), "OneClickBackup_WinPE"
        )
        if os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

        # Find the Deployment Tools environment batch
        deploy_env = os.path.join(
            adk_path,
            "Assessment and Deployment Kit",
            "Deployment Tools",
            "DandISetEnv.bat",
        )

        # copype creates a working WinPE directory -- it requires the ADK
        # environment so we write a temp .bat that sources it first.
        arch = "amd64"
        self._report_progress("Running copype...", 15.0)
        copype_bat = self._write_adk_bat(deploy_env, f'copype {arch} "{work_dir}"')
        try:
            result = subprocess.run(
                ["cmd", "/c", copype_bat],
                capture_output=True, text=True, timeout=300,
            )
        finally:
            self._safe_unlink(copype_bat)
        if result.returncode != 0:
            raise BackupError(f"copype failed: {result.stderr}")

        # Optionally mount boot.wim and inject custom scripts
        wim_path = os.path.join(work_dir, "media", "sources", "boot.wim")
        mount_dir = os.path.join(work_dir, "mount")

        if os.path.isfile(wim_path):
            self._report_progress("Mounting boot.wim...", 35.0)
            subprocess.run(
                ["dism", "/Mount-Wim", f"/WimFile:{wim_path}",
                 "/Index:1", f"/MountDir:{mount_dir}"],
                capture_output=True, text=True, timeout=300,
            )

            # Copy our tool into the WinPE image
            tool_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            tool_dst = os.path.join(mount_dir, "OneClickBackup")
            if os.path.isdir(tool_src):
                shutil.copytree(tool_src, tool_dst, dirs_exist_ok=True)

            # Create autorun startnet.cmd addition
            startnet = os.path.join(
                mount_dir, "Windows", "System32", "startnet.cmd"
            )
            if os.path.isfile(startnet):
                with open(startnet, "a") as f:
                    f.write("\r\necho OneClickBackup WinPE Environment Ready\r\n")

            self._report_progress("Unmounting boot.wim...", 55.0)
            subprocess.run(
                ["dism", "/Unmount-Wim", f"/MountDir:{mount_dir}", "/Commit"],
                capture_output=True, text=True, timeout=300,
            )

        # Write to USB using MakeWinPEMedia (requires ADK environment)
        self._report_progress("Writing to USB...", 70.0)
        media_bat = self._write_adk_bat(
            deploy_env, f'MakeWinPEMedia /UFD "{work_dir}" {target_letter}:',
        )
        try:
            result = subprocess.run(
                ["cmd", "/c", media_bat],
                capture_output=True, text=True, timeout=600,
            )
        finally:
            self._safe_unlink(media_bat)
        if result.returncode != 0:
            raise BackupError(f"MakeWinPEMedia failed: {result.stderr}")

        # Cleanup
        shutil.rmtree(work_dir, ignore_errors=True)
        self._report_progress("WinPE USB created successfully.", 100.0)
        return True

    def _create_winpe_basic(self, target_letter: str) -> bool:
        """Create a minimal bootable USB without ADK.

        Formats the drive and copies the Windows Recovery Environment
        (WinRE) if available.
        """
        self._report_progress("Formatting USB drive...", 10.0)

        # Find the target disk number via PowerShell
        cmd = (
            f"(Get-Partition -DriveLetter {target_letter}).DiskNumber"
        )
        stdout, stderr, rc = run_powershell(cmd)
        if rc != 0 or not stdout.strip().isdigit():
            raise BackupError(
                f"Cannot determine disk number for drive {target_letter}: "
                f"{stderr}"
            )
        usb_disk = int(stdout.strip())

        # Format with diskpart
        dp_script = [
            f"select disk {usb_disk}",
            "clean",
            "create partition primary",
            "select partition 1",
            "active",
            "format fs=fat32 quick label=WINPE",
            f"assign letter={target_letter}",
        ]
        dp_out, dp_err, dp_rc = run_diskpart(dp_script)
        if dp_rc != 0:
            raise BackupError(f"diskpart failed: {dp_err}")

        self._report_progress("Copying boot files...", 40.0)

        # Try to use bootsect to write MBR/PBR
        bootsect_paths = [
            r"C:\Windows\System32\bootsect.exe",
            r"C:\Windows\Boot\bootsect.exe",
        ]
        for bp in bootsect_paths:
            if os.path.isfile(bp):
                subprocess.run(
                    [bp, "/nt60", f"{target_letter}:", "/mbr"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                break

        # Copy WinRE.wim if it exists
        winre_source = r"C:\Windows\System32\Recovery\Winre.wim"
        target_root = f"{target_letter}:\\"

        if os.path.isfile(winre_source):
            self._report_progress("Copying WinRE image...", 55.0)
            sources_dir = os.path.join(target_root, "sources")
            os.makedirs(sources_dir, exist_ok=True)
            boot_wim = os.path.join(sources_dir, "boot.wim")
            shutil.copy2(winre_source, boot_wim)

        # Copy essential boot files from the Windows installation
        self._report_progress("Copying boot manager...", 75.0)
        boot_files = [
            (r"C:\Windows\Boot\EFI\bootmgfw.efi", os.path.join(target_root, "EFI", "Boot", "bootx64.efi")),
            (r"C:\Windows\Boot\PCAT\bootmgr", os.path.join(target_root, "bootmgr")),
        ]
        for src_file, dst_file in boot_files:
            if os.path.isfile(src_file):
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)

        # Create a minimal BCD store with bcdboot (best effort)
        subprocess.run(
            ["bcdboot", r"C:\Windows", "/s", f"{target_letter}:", "/f", "ALL"],
            capture_output=True, text=True, timeout=60,
        )

        self._report_progress("Basic bootable USB created.", 100.0)
        self._log.info(
            "Basic bootable USB written to %s:.", target_letter
        )
        return True

    # ------------------------------------------------------------------
    # Private helpers used by WinPE creation
    # ------------------------------------------------------------------

    @staticmethod
    def _write_adk_bat(deploy_env: str, command: str) -> str:
        """Write a temp .bat that sources the ADK environment then runs *command*.

        Returns the path to the temp file (caller must delete after use).
        """
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="ocb_adk_")
        with os.fdopen(fd, "w") as f:
            f.write(f'@call "{deploy_env}"\r\n')
            f.write(f"{command}\r\n")
        return bat_path

    @staticmethod
    def _safe_unlink(path: str) -> None:
        """Delete *path* ignoring errors (cleanup helper)."""
        try:
            os.unlink(path)
        except OSError:
            pass
