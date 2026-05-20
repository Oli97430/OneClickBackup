"""Internationalization (i18n) module for OneClick Backup & Disk Manager.

Provides a simple dictionary-based translation system supporting 6 languages:
    en (English), fr (French), es (Spanish), de (German), ar (Arabic), zh (Chinese Simplified).

Usage:
    from src.utils.i18n import t, set_language, get_languages
    set_language("fr")
    label = t("sidebar.dashboard")  # -> "Tableau de bord"
"""

from __future__ import annotations

import json
import locale
import logging
import os
import threading
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_current_lang: str = "en"
_lang_lock = threading.Lock()
_SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".oneclickbackup_lang.json")


def _detect_system_locale() -> str:
    """Return the best matching language code from the system locale.

    Maps Windows locale codes (e.g. ``'fr_FR'`` -> ``'fr'``) to the
    supported ``LANGUAGES`` keys.  Returns ``'en'`` if no match is found.
    """
    try:
        # locale.getlocale() is the non-deprecated replacement for
        # locale.getdefaultlocale() (deprecated since Python 3.11).
        loc, _ = locale.getlocale()
        if loc:
            lang_prefix = loc.split("_")[0].lower()
            if lang_prefix in LANGUAGES:
                return lang_prefix
    except Exception:
        pass
    # Fallback: try the default locale (Windows-specific)
    try:
        loc = locale.getdefaultlocale()[0]  # type: ignore[deprecated]
        if loc:
            lang_prefix = loc.split("_")[0].lower()
            if lang_prefix in LANGUAGES:
                return lang_prefix
    except Exception:
        pass
    return "en"

# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------

LANGUAGES = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "de": "Deutsch",
    "ar": "العربية",
    "zh": "中文",
}

_T: dict[str, dict[str, str]] = {}

# ── English ────────────────────────────────────────────────────────────────
_T["en"] = {
    # App
    "app.title": "OneClick Backup & Disk Manager",
    "app.logo": "OneClick Backup",
    "app.logo_sub": "Disk Manager Pro",

    # Sidebar
    "sidebar.dashboard": "Dashboard",
    "sidebar.clone": "Clone / Migrate",
    "sidebar.partitions": "Partitions",
    "sidebar.backup": "Backup & Restore",
    "sidebar.convert": "Conversions",
    "sidebar.recovery": "Recovery",
    "sidebar.scheduler": "Scheduler",
    "sidebar.history": "History",
    "sidebar.advanced": "Advanced Tools",

    # Pages
    "page.scheduler": "Backup Scheduler",
    "page.history": "Operation History",

    # Admin
    "admin.ok": "✅ Administrator",
    "admin.limited": "⚠️ Limited Mode",
    "admin.elevate": "Run as Admin",

    # Status bar
    "status.ready": "Ready",
    "status.pending_one": "1 pending",
    "status.pending_n": "{n} pending",

    # Operation queue
    "queue.title": "Pending Operations",
    "queue.empty": "No pending operations",
    "queue.apply": "Apply All",
    "queue.clear": "Clear",

    # Page titles (status bar)
    "page.dashboard": "Dashboard – Disk Overview",
    "page.clone": "Clone & OS Migration",
    "page.partitions": "Partition Management",
    "page.backup": "Backup & Restore",
    "page.convert": "Disk & Partition Conversion",
    "page.recovery": "Partition Recovery",
    "page.advanced": "Advanced Tools",

    # Pending ops confirm
    "confirm.pending_exit": "You have {n} pending operation(s) that haven't been applied.\n\nAre you sure you want to exit?",
    "confirm.pending_title": "Pending Operations",

    # Dashboard
    "dash.total_disks": "Total Disks",
    "dash.total_storage": "Total Storage",
    "dash.ssds": "SSDs",
    "dash.hdds": "HDDs",
    "dash.overview": "Disk Overview",
    "dash.select_partition": "Select a partition\nto view details",
    "dash.no_disks": "No disks found. Run as Administrator for full access.",
    "dash.refresh": "⟳ Refresh",
    "dash.loading": "Loading...",

    # Partition detail
    "detail.letter": "Letter",
    "detail.label": "Label",
    "detail.fs": "File System",
    "detail.size": "Size",
    "detail.used": "Used",
    "detail.free": "Free",
    "detail.type": "Type",
    "detail.active": "Active",
    "detail.boot": "Boot",
    "detail.system": "System",

    # Clone page
    "clone.title": "\U0001f4bf  Clone & OS Migration",
    "clone.desc": "Clone your entire disk or migrate just the OS to a new drive.",
    "clone.source": "SOURCE DISK",
    "clone.target": "TARGET DISK",
    "clone.options": "OPTIONS",
    "clone.full": "Full Disk Clone",
    "clone.os_only": "OS Migration Only",
    "clone.resize": "Resize partitions to fit target",
    "clone.verify": "Verify after clone",
    "clone.boot": "Set target as boot disk",
    "clone.warning": "⚠ All data on the target disk will be overwritten. Make sure you have backups.",
    "clone.start": "▶  Start Clone",
    "clone.same_disk": "Source and target disk cannot be the same.",
    "clone.same_disk_title": "Same Disk",
    "clone.confirm_title": "Confirm Clone",
    "clone.confirm_msg": "Clone {src}\n  →  {tgt}\n\nAll data on the target disk will be DESTROYED.",
    "clone.started": "Clone operation queued. (Full implementation requires admin privileges.)",

    # Partition page
    "part.title": "\U0001f527  Partition Management",
    "part.select_disk": "Select a disk to see partitions",
    "part.col_index": "#",
    "part.col_letter": "Letter",
    "part.col_label": "Label",
    "part.col_type": "Type",
    "part.col_fs": "FS",
    "part.col_size": "Size",
    "part.col_used": "Used",
    "part.col_free": "Free",
    "part.col_status": "Status",
    "part.active": "Active",
    "part.create": "Create",
    "part.resize": "Resize / Move",
    "part.merge": "Merge",
    "part.format": "Format",
    "part.delete": "Delete",
    "part.change_letter": "Change Letter",
    "part.set_active": "Set Active",
    "part.select_first": "Select a partition first.",
    "part.create_prompt": "Size in MB for new partition:",
    "part.create_title": "Create Partition",
    "part.resize_prompt": "New size in MB:",
    "part.resize_title": "Resize Partition",
    "part.letter_prompt": "New drive letter (e.g. E):",
    "part.letter_title": "Change Letter",
    "part.queued": "Operation queued.",
    "part.format_confirm": "All data on partition {letter}: will be lost!",
    "part.format_title": "Format Partition",
    "part.delete_confirm": "Permanently delete partition {letter}:?",
    "part.delete_title": "Delete Partition",
    "part.merge_info": "Select two adjacent partitions to merge.\n(Full implementation pending.)",

    # Conversion page
    "conv.title": "\U0001f504  Conversion Tools",
    "conv.disk_style": "Disk Partition Style",
    "conv.disk_style_desc": "Convert between MBR and GPT without data loss",
    "conv.current": "Current style:",
    "conv.to_gpt": "Convert to GPT",
    "conv.to_mbr": "Convert to MBR",
    "conv.mbr_warn": "⚠ MBR→GPT uses mbr2gpt.exe (non-destructive).\nGPT→MBR requires empty disk.",
    "conv.convert": "Convert",
    "conv.fs_title": "File System Conversion",
    "conv.fs_desc": "Change file system type without formatting",
    "conv.fs_target": "Target file system:",
    "conv.fs_note": "Note: Only NTFS↔FAT32 via convert.exe is lossless.\nOther conversions may require formatting.",
    "conv.primary_logical": "Primary ↔ Logical (MBR disks only)",
    "conv.primary_logical_desc": "Convert primary partitions to logical or vice-versa on MBR disks to manage partition count limits.",
    "conv.wizard": "Open Conversion Wizard",
    "conv.wizard_info": "Select a partition on an MBR disk to convert.\n(Wizard coming soon.)",
    "conv.confirm_style": "This will convert the disk partition style.\nEnsure you have a backup!",
    "conv.confirm_style_title": "Convert Partition Style",
    "conv.confirm_fs": "Convert file system type. Some conversions may cause data loss.",
    "conv.confirm_fs_title": "Convert File System",
    "conv.queued": "Conversion operation queued.",

    # Backup page
    "bak.title": "\U0001f4be  Backup & Restore",
    "bak.tab_create": "Create Backup",
    "bak.tab_restore": "Restore Backup",
    "bak.tab_settings": "Settings",
    "bak.type": "Backup Type",
    "bak.full_disk": "Full Disk",
    "bak.partition": "Partition",
    "bak.system": "System",
    "bak.source": "Source",
    "bak.dest": "Destination Folder",
    "bak.browse": "Browse",
    "bak.name": "Backup Name",
    "bak.name_placeholder": "My Backup",
    "bak.verify": "Verify after backup",
    "bak.create_btn": "▶  Create Backup",
    "bak.no_dest": "Please choose a destination folder.",
    "bak.started": "Creating {type} backup '{name}' to {dest}...\n\n(Runs in background)",
    "bak.available": "Available Backups",
    "bak.none": "No backups found.",
    "bak.restore_btn": "Restore",
    "bak.verify_btn": "Verify",
    "bak.delete_btn": "Delete",
    "bak.restore_info": "Select a backup above, then choose the target disk/partition.\n(Full implementation pending.)",
    "bak.verify_info": "Backup verification will check integrity using checksums.",
    "bak.delete_info": "Select a backup to delete.",
    "bak.settings_dir": "Default Backup Directory",
    "bak.auto_verify": "Auto-verify after backup",
    "bak.dest_title": "Select backup destination",

    # Recovery page
    "rec.title": "\U0001f50d  Partition Recovery",
    "rec.step1": "Step 1 of 4 — Select Disk",
    "rec.step2": "Step 2 of 4 — Scan Type",
    "rec.step3": "Step 3 of 4 — Scanning...",
    "rec.step4": "Step 4 of 4 — Results",
    "rec.select_disk": "Select the disk to scan for lost partitions:",
    "rec.scan_type": "Choose scan type:",
    "rec.quick": "Quick Scan",
    "rec.deep": "Deep Scan",
    "rec.scan_desc": "Quick Scan: Checks partition table for recently deleted entries.\nDeep Scan: Reads disk sectors to find orphaned file systems (slower).",
    "rec.scanning": "Scanning disk for lost partitions...",
    "rec.scan_pct": "{pct}% — Scanning sectors...",
    "rec.scan_done": "Scan complete!",
    "rec.found": "Found partitions:",
    "rec.not_found": "No lost partitions found on this disk.",
    "rec.recover": "Recover",
    "rec.recover_started": "Recovery of partition {idx} initiated.",
    "rec.done": "Done",
    "rec.back": "◀ Back",
    "rec.next": "Next ▶",
    "rec.recoverable": "Recoverable",
    "rec.partial": "Partially recoverable",

    # Advanced page
    "adv.title": "⚙️  Advanced Tools",
    "adv.align_title": "⚡ 4K Alignment",
    "adv.align_desc": "Properly align SSD partitions for maximum read/write performance.",
    "adv.align_check": "Check Alignment",
    "adv.align_select": "Select a disk to check alignment",
    "adv.align_ok": "✅ All partitions are 4K aligned.",
    "adv.align_bad": "⚠ Misaligned partitions detected!",
    "adv.align_unknown": "Could not determine alignment. Run as admin.",
    "adv.pe_title": "\U0001f4bf WinPE Bootable Disk",
    "adv.pe_desc": "Create an emergency boot USB to manage partitions\nor fix boot issues when Windows won't start.",
    "adv.pe_status": "Status: Not checked",
    "adv.pe_check": "Check Prerequisites",
    "adv.pe_usb": "Select USB drive...",
    "adv.pe_create": "Create Bootable USB",
    "adv.pe_confirm": "This will FORMAT the selected USB drive.\nAll data on it will be lost!",
    "adv.pe_confirm_title": "Create WinPE USB",
    "adv.pe_started": "WinPE creation started. (Requires Windows ADK.)",
    "adv.pe_unavailable": "Backup manager not available",
    "adv.no_usb": "No USB drives detected",
    "adv.health_title": "❤ Disk Health",
    "adv.health_desc": "View S.M.A.R.T. status and health indicators for your disks.",
    "adv.wipe_title": "\U0001f5d1 Secure Disk Wipe",
    "adv.wipe_desc": "Securely erase all data on a disk. This cannot be undone!",
    "adv.wipe_quick": "Quick (Zero Fill)",
    "adv.wipe_secure": "Secure (3-Pass)",
    "adv.wipe_btn": "⚠ Wipe Disk",
    "adv.wipe_confirm": "ALL DATA on the selected disk will be permanently destroyed!\nThis action CANNOT be undone.",
    "adv.wipe_confirm_title": "Secure Wipe",
    "adv.wipe_started": "Secure wipe operation started.",
    "adv.select_disk": "Select disk...",
    "part.create_queued": "Partition creation queued ({size} MB).",
    "part.resize_queued": "Partition resize queued.",
    "part.format_queued": "Format operation queued.",
    "part.delete_queued": "Partition deletion queued.",
    "part.letter_queued": "Drive letter changed to {letter}: queued.",
    "part.active_queued": "Set active partition queued.",
    "part.merge_msg": "Select two adjacent partitions to merge.\n(Full implementation pending.)",
    "part.error": "Error",
    "part.partitions_label": "Partitions",
    "conv.style_warning": "⚠ MBR→GPT uses mbr2gpt.exe (non-destructive).\nGPT→MBR requires empty disk.",
    "conv.fs_warning": "Note: Only NTFS↔FAT32 via convert.exe is lossless.\nOther conversions may require formatting.",
    "conv.pl_title": "Primary ↔ Logical (MBR disks only)",
    "conv.pl_desc": "Convert primary partitions to logical or vice-versa on MBR disks to manage partition count limits.",
    "conv.pl_wizard": "Open Conversion Wizard",
    "conv.pl_msg": "Select a partition on an MBR disk to convert.\n(Wizard coming soon.)",
    "conv.no_partitions": "No partitions found",
    "conv.confirm_style_msg": "This will convert the disk partition style.\nEnsure you have a backup!",
    "conv.confirm_fs_msg": "Convert file system type. Some conversions may cause data loss.",
    "conv.style_queued": "Disk style conversion queued.",
    "conv.fs_queued": "File system conversion queued.",
    "bak.dest_placeholder": "Choose destination...",
    "bak.started_title": "Backup Started",
    "bak.error": "Backup Error",
    "rec.partition": "Partition",

    # Common
    "common.no_disks": "No disks found",
    "common.no_parts": "No partitions",
    "common.loading": "Loading...",
    "common.error": "Error",
    "common.queued": "Queued",
    "common.yes": "Yes",
    "common.no": "No",
    "common.ok": "OK",
    "common.cancel": "Cancel",
    "common.apply": "Apply",
    "common.select": "Select",
    "common.language": "Language",
    "common.disk": "Disk",
}

# ── French ─────────────────────────────────────────────────────────────────
_T["fr"] = {
    "app.title": "OneClick Backup & Gestionnaire de disques",
    "app.logo": "OneClick Backup",
    "app.logo_sub": "Gestionnaire Pro",
    "sidebar.dashboard": "Tableau de bord",
    "sidebar.clone": "Cloner / Migrer",
    "sidebar.partitions": "Partitions",
    "sidebar.backup": "Sauvegarde",
    "sidebar.convert": "Conversions",
    "sidebar.recovery": "Récupération",
    "sidebar.scheduler": "Planificateur",
    "sidebar.history": "Historique",
    "sidebar.advanced": "Outils avancés",
    "page.scheduler": "Planificateur de sauvegardes",
    "page.history": "Historique des opérations",
    "admin.ok": "✅ Administrateur",
    "admin.limited": "⚠️ Mode limité",
    "admin.elevate": "Lancer en admin",
    "status.ready": "Prêt",
    "status.pending_one": "1 en attente",
    "status.pending_n": "{n} en attente",
    "queue.title": "Opérations en attente",
    "queue.empty": "Aucune opération en attente",
    "queue.apply": "Tout appliquer",
    "queue.clear": "Effacer",
    "page.dashboard": "Tableau de bord – Aperçu des disques",
    "page.clone": "Clonage & Migration OS",
    "page.partitions": "Gestion des partitions",
    "page.backup": "Sauvegarde & Restauration",
    "page.convert": "Conversion disques & partitions",
    "page.recovery": "Récupération de partitions",
    "page.advanced": "Outils avancés",
    "confirm.pending_exit": "Vous avez {n} opération(s) en attente non appliquée(s).\n\nÊtes-vous sûr de vouloir quitter ?",
    "confirm.pending_title": "Opérations en attente",
    "dash.total_disks": "Total disques",
    "dash.total_storage": "Stockage total",
    "dash.ssds": "SSDs",
    "dash.hdds": "HDDs",
    "dash.overview": "Aperçu des disques",
    "dash.select_partition": "Sélectionnez une partition\npour voir les détails",
    "dash.no_disks": "Aucun disque trouvé. Lancez en Administrateur pour un accès complet.",
    "dash.refresh": "⟳ Actualiser",
    "dash.loading": "Chargement...",
    "detail.letter": "Lettre",
    "detail.label": "Étiquette",
    "detail.fs": "Système de fichiers",
    "detail.size": "Taille",
    "detail.used": "Utilisé",
    "detail.free": "Libre",
    "detail.type": "Type",
    "detail.active": "Active",
    "detail.boot": "Démarrage",
    "detail.system": "Système",
    "clone.title": "\U0001f4bf  Clonage & Migration OS",
    "clone.desc": "Clonez votre disque entier ou migrez uniquement l'OS vers un nouveau disque.",
    "clone.source": "DISQUE SOURCE",
    "clone.target": "DISQUE CIBLE",
    "clone.options": "OPTIONS",
    "clone.full": "Clone disque complet",
    "clone.os_only": "Migration OS uniquement",
    "clone.resize": "Redimensionner les partitions pour la cible",
    "clone.verify": "Vérifier après le clonage",
    "clone.boot": "Définir la cible comme disque de démarrage",
    "clone.warning": "⚠ Toutes les données du disque cible seront écrasées. Assurez-vous d'avoir des sauvegardes.",
    "clone.start": "▶  Démarrer le clonage",
    "clone.same_disk": "Le disque source et cible ne peuvent pas être identiques.",
    "clone.same_disk_title": "Même disque",
    "clone.confirm_title": "Confirmer le clonage",
    "clone.confirm_msg": "Cloner {src}\n  →  {tgt}\n\nToutes les données du disque cible seront DÉTRUITES.",
    "clone.started": "Opération de clonage mise en file. (Nécessite les droits administrateur.)",
    "part.title": "\U0001f527  Gestion des partitions",
    "part.select_disk": "Sélectionnez un disque pour voir les partitions",
    "part.col_index": "#",
    "part.col_letter": "Lettre",
    "part.col_label": "Étiquette",
    "part.col_type": "Type",
    "part.col_fs": "SF",
    "part.col_size": "Taille",
    "part.col_used": "Utilisé",
    "part.col_free": "Libre",
    "part.col_status": "Statut",
    "part.active": "Active",
    "part.create": "Créer",
    "part.resize": "Redimensionner",
    "part.merge": "Fusionner",
    "part.format": "Formater",
    "part.delete": "Supprimer",
    "part.change_letter": "Changer lettre",
    "part.set_active": "Activer",
    "part.select_first": "Sélectionnez d'abord une partition.",
    "part.create_prompt": "Taille en Mo pour la nouvelle partition :",
    "part.create_title": "Créer une partition",
    "part.resize_prompt": "Nouvelle taille en Mo :",
    "part.resize_title": "Redimensionner la partition",
    "part.letter_prompt": "Nouvelle lettre de lecteur (ex: E) :",
    "part.letter_title": "Changer la lettre",
    "part.queued": "Opération mise en file.",
    "part.format_confirm": "Toutes les données de la partition {letter}: seront perdues !",
    "part.format_title": "Formater la partition",
    "part.delete_confirm": "Supprimer définitivement la partition {letter}: ?",
    "part.delete_title": "Supprimer la partition",
    "part.merge_info": "Sélectionnez deux partitions adjacentes à fusionner.\n(Implémentation complète en cours.)",
    "conv.title": "\U0001f504  Outils de conversion",
    "conv.disk_style": "Style de partition du disque",
    "conv.disk_style_desc": "Convertir entre MBR et GPT sans perte de données",
    "conv.current": "Style actuel :",
    "conv.to_gpt": "Convertir en GPT",
    "conv.to_mbr": "Convertir en MBR",
    "conv.mbr_warn": "⚠ MBR→GPT utilise mbr2gpt.exe (non destructif).\nGPT→MBR nécessite un disque vide.",
    "conv.convert": "Convertir",
    "conv.fs_title": "Conversion du système de fichiers",
    "conv.fs_desc": "Changer le type de système de fichiers sans formater",
    "conv.fs_target": "Système de fichiers cible :",
    "conv.fs_note": "Note : Seule la conversion NTFS↔FAT32 via convert.exe est sans perte.\nLes autres conversions peuvent nécessiter un formatage.",
    "conv.primary_logical": "Primaire ↔ Logique (disques MBR uniquement)",
    "conv.primary_logical_desc": "Convertir les partitions primaires en logiques ou inversement sur les disques MBR.",
    "conv.wizard": "Ouvrir l'assistant",
    "conv.wizard_info": "Sélectionnez une partition sur un disque MBR.\n(Assistant bientôt disponible.)",
    "conv.confirm_style": "Ceci va convertir le style de partition du disque.\nAssurez-vous d'avoir une sauvegarde !",
    "conv.confirm_style_title": "Convertir le style de partition",
    "conv.confirm_fs": "Convertir le système de fichiers. Certaines conversions peuvent entraîner une perte de données.",
    "conv.confirm_fs_title": "Convertir le système de fichiers",
    "conv.queued": "Opération de conversion mise en file.",
    "bak.title": "\U0001f4be  Sauvegarde & Restauration",
    "bak.tab_create": "Créer une sauvegarde",
    "bak.tab_restore": "Restaurer",
    "bak.tab_settings": "Paramètres",
    "bak.type": "Type de sauvegarde",
    "bak.full_disk": "Disque complet",
    "bak.partition": "Partition",
    "bak.system": "Système",
    "bak.source": "Source",
    "bak.dest": "Dossier de destination",
    "bak.browse": "Parcourir",
    "bak.name": "Nom de la sauvegarde",
    "bak.name_placeholder": "Ma Sauvegarde",
    "bak.verify": "Vérifier après la sauvegarde",
    "bak.create_btn": "▶  Créer la sauvegarde",
    "bak.no_dest": "Veuillez choisir un dossier de destination.",
    "bak.started": "Création de la sauvegarde {type} '{name}' vers {dest}...\n\n(Exécution en arrière-plan)",
    "bak.available": "Sauvegardes disponibles",
    "bak.none": "Aucune sauvegarde trouvée.",
    "bak.restore_btn": "Restaurer",
    "bak.verify_btn": "Vérifier",
    "bak.delete_btn": "Supprimer",
    "bak.restore_info": "Sélectionnez une sauvegarde ci-dessus, puis choisissez la cible.\n(Implémentation complète en cours.)",
    "bak.verify_info": "La vérification contrôlera l'intégrité via les sommes de contrôle.",
    "bak.delete_info": "Sélectionnez une sauvegarde à supprimer.",
    "bak.settings_dir": "Répertoire de sauvegarde par défaut",
    "bak.auto_verify": "Vérification auto après sauvegarde",
    "bak.dest_title": "Sélectionner le dossier de destination",
    "rec.title": "\U0001f50d  Récupération de partitions",
    "rec.step1": "Étape 1/4 — Sélection du disque",
    "rec.step2": "Étape 2/4 — Type d'analyse",
    "rec.step3": "Étape 3/4 — Analyse en cours...",
    "rec.step4": "Étape 4/4 — Résultats",
    "rec.select_disk": "Sélectionnez le disque à analyser :",
    "rec.scan_type": "Choisissez le type d'analyse :",
    "rec.quick": "Analyse rapide",
    "rec.deep": "Analyse approfondie",
    "rec.scan_desc": "Analyse rapide : Vérifie la table de partition.\nAnalyse approfondie : Lit les secteurs du disque (plus lent).",
    "rec.scanning": "Analyse du disque en cours...",
    "rec.scan_pct": "{pct}% — Analyse des secteurs...",
    "rec.scan_done": "Analyse terminée !",
    "rec.found": "Partitions trouvées :",
    "rec.not_found": "Aucune partition perdue trouvée sur ce disque.",
    "rec.recover": "Récupérer",
    "rec.recover_started": "Récupération de la partition {idx} lancée.",
    "rec.done": "Terminé",
    "rec.back": "◀ Retour",
    "rec.next": "Suivant ▶",
    "rec.recoverable": "Récupérable",
    "rec.partial": "Partiellement récupérable",
    "adv.title": "⚙️  Outils avancés",
    "adv.align_title": "⚡ Alignement 4K",
    "adv.align_desc": "Aligner correctement les partitions SSD pour des performances maximales.",
    "adv.align_check": "Vérifier l'alignement",
    "adv.align_select": "Sélectionnez un disque",
    "adv.align_ok": "✅ Toutes les partitions sont alignées en 4K.",
    "adv.align_bad": "⚠ Partitions mal alignées détectées !",
    "adv.align_unknown": "Impossible de déterminer l'alignement. Lancez en admin.",
    "adv.pe_title": "\U0001f4bf Disque WinPE bootable",
    "adv.pe_desc": "Créer une clé USB de secours pour gérer les partitions\nou réparer le démarrage.",
    "adv.pe_status": "Statut : Non vérifié",
    "adv.pe_check": "Vérifier les prérequis",
    "adv.pe_usb": "Sélectionner la clé USB...",
    "adv.pe_create": "Créer la clé USB bootable",
    "adv.pe_confirm": "Ceci va FORMATER la clé USB sélectionnée.\nToutes les données seront perdues !",
    "adv.pe_confirm_title": "Créer WinPE USB",
    "adv.pe_started": "Création WinPE lancée. (Nécessite Windows ADK.)",
    "adv.pe_unavailable": "Gestionnaire de sauvegarde non disponible",
    "adv.no_usb": "Aucune clé USB détectée",
    "adv.health_title": "❤ Santé des disques",
    "adv.health_desc": "Voir le statut S.M.A.R.T. et les indicateurs de santé.",
    "adv.wipe_title": "\U0001f5d1 Effacement sécurisé",
    "adv.wipe_desc": "Effacer définitivement toutes les données d'un disque. Irréversible !",
    "adv.wipe_quick": "Rapide (remplissage zéro)",
    "adv.wipe_secure": "Sécurisé (3 passes)",
    "adv.wipe_btn": "⚠ Effacer le disque",
    "adv.wipe_confirm": "TOUTES les données du disque seront définitivement détruites !\nCette action est IRRÉVERSIBLE.",
    "adv.wipe_confirm_title": "Effacement sécurisé",
    "adv.wipe_started": "Effacement sécurisé lancé.",
    "adv.select_disk": "Sélectionner un disque...",
    "part.create_queued": "Création de partition mise en file ({size} Mo).",
    "part.resize_queued": "Redimensionnement mis en file.",
    "part.format_queued": "Formatage mis en file.",
    "part.delete_queued": "Suppression de partition mise en file.",
    "part.letter_queued": "Changement de lettre vers {letter}: mis en file.",
    "part.active_queued": "Partition active mise en file.",
    "part.merge_msg": "Sélectionnez deux partitions adjacentes à fusionner.\n(Implémentation complète en cours.)",
    "part.error": "Erreur",
    "part.partitions_label": "Partitions",
    "conv.style_warning": "⚠ MBR→GPT utilise mbr2gpt.exe (non destructif).\nGPT→MBR nécessite un disque vide.",
    "conv.fs_warning": "Note : Seule la conversion NTFS↔FAT32 est sans perte.\nLes autres peuvent nécessiter un formatage.",
    "conv.pl_title": "Primaire ↔ Logique (disques MBR uniquement)",
    "conv.pl_desc": "Convertir les partitions primaires en logiques ou inversement sur disques MBR.",
    "conv.pl_wizard": "Ouvrir l'assistant de conversion",
    "conv.pl_msg": "Sélectionnez une partition sur un disque MBR.\n(Assistant bientôt disponible.)",
    "conv.no_partitions": "Aucune partition trouvée",
    "conv.confirm_style_msg": "Ceci va convertir le style de partition.\nAssurez-vous d'avoir une sauvegarde !",
    "conv.confirm_fs_msg": "Convertir le système de fichiers. Risque de perte de données.",
    "conv.style_queued": "Conversion de style mise en file.",
    "conv.fs_queued": "Conversion du système de fichiers mise en file.",
    "bak.dest_placeholder": "Choisir le dossier de destination...",
    "bak.started_title": "Sauvegarde lancée",
    "bak.error": "Erreur de sauvegarde",
    "rec.partition": "Partition",
    "common.no_disks": "Aucun disque trouvé",
    "common.no_parts": "Aucune partition",
    "common.loading": "Chargement...",
    "common.error": "Erreur",
    "common.queued": "Mis en file",
    "common.yes": "Oui",
    "common.no": "Non",
    "common.ok": "OK",
    "common.cancel": "Annuler",
    "common.apply": "Appliquer",
    "common.select": "Sélectionner",
    "common.language": "Langue",
    "common.disk": "Disque",
}

# ── Spanish ────────────────────────────────────────────────────────────────
_T["es"] = {
    "app.title": "OneClick Backup & Gestor de Discos",
    "app.logo": "OneClick Backup",
    "app.logo_sub": "Gestor de Discos Pro",
    "sidebar.dashboard": "Panel",
    "sidebar.clone": "Clonar / Migrar",
    "sidebar.partitions": "Particiones",
    "sidebar.backup": "Respaldo",
    "sidebar.convert": "Conversiones",
    "sidebar.recovery": "Recuperación",
    "sidebar.scheduler": "Programador",
    "sidebar.history": "Historial",
    "sidebar.advanced": "Herram. avanzadas",
    "page.scheduler": "Programador de respaldos",
    "page.history": "Historial de operaciones",
    "admin.ok": "✅ Administrador",
    "admin.limited": "⚠️ Modo limitado",
    "admin.elevate": "Ejecutar como admin",
    "status.ready": "Listo",
    "status.pending_one": "1 pendiente",
    "status.pending_n": "{n} pendientes",
    "queue.title": "Operaciones pendientes",
    "queue.empty": "Sin operaciones pendientes",
    "queue.apply": "Aplicar todo",
    "queue.clear": "Limpiar",
    "page.dashboard": "Panel – Vista de discos",
    "page.clone": "Clonación & Migración de SO",
    "page.partitions": "Gestión de particiones",
    "page.backup": "Respaldo & Restauración",
    "page.convert": "Conversión de discos",
    "page.recovery": "Recuperación de particiones",
    "page.advanced": "Herramientas avanzadas",
    "confirm.pending_exit": "Tiene {n} operación(es) pendiente(s).\n\n¿Seguro que desea salir?",
    "confirm.pending_title": "Operaciones pendientes",
    "dash.total_disks": "Total discos",
    "dash.total_storage": "Almacenamiento",
    "dash.ssds": "SSDs",
    "dash.hdds": "HDDs",
    "dash.overview": "Vista de discos",
    "dash.select_partition": "Seleccione una partición\npara ver detalles",
    "dash.no_disks": "No se encontraron discos. Ejecute como Administrador.",
    "dash.refresh": "⟳ Actualizar",
    "dash.loading": "Cargando...",
    "clone.title": "\U0001f4bf  Clonar & Migrar SO",
    "clone.desc": "Clone su disco completo o migre solo el SO a un nuevo disco.",
    "clone.source": "DISCO ORIGEN",
    "clone.target": "DISCO DESTINO",
    "clone.options": "OPCIONES",
    "clone.full": "Clon completo",
    "clone.os_only": "Solo migración de SO",
    "clone.resize": "Redimensionar particiones al destino",
    "clone.verify": "Verificar después de clonar",
    "clone.boot": "Establecer destino como disco de arranque",
    "clone.warning": "⚠ Todos los datos del disco destino serán borrados.",
    "clone.start": "▶  Iniciar clonación",
    "part.title": "\U0001f527  Gestión de particiones",
    "part.create": "Crear",
    "part.resize": "Redimensionar",
    "part.merge": "Fusionar",
    "part.format": "Formatear",
    "part.delete": "Eliminar",
    "part.change_letter": "Cambiar letra",
    "part.set_active": "Activar",
    "conv.title": "\U0001f504  Herramientas de conversión",
    "conv.convert": "Convertir",
    "bak.title": "\U0001f4be  Respaldo & Restauración",
    "bak.tab_create": "Crear respaldo",
    "bak.tab_restore": "Restaurar",
    "bak.tab_settings": "Ajustes",
    "bak.create_btn": "▶  Crear respaldo",
    "bak.browse": "Explorar",
    "rec.title": "\U0001f50d  Recuperación de particiones",
    "rec.back": "◀ Atrás",
    "rec.next": "Siguiente ▶",
    "rec.recover": "Recuperar",
    "rec.quick": "Análisis rápido",
    "rec.deep": "Análisis profundo",
    "adv.title": "⚙️  Herramientas avanzadas",
    "common.no_disks": "No se encontraron discos",
    "common.loading": "Cargando...",
    "common.error": "Error",
    "common.language": "Idioma",
    "common.disk": "Disco",
}

# ── German ─────────────────────────────────────────────────────────────────
_T["de"] = {
    "app.title": "OneClick Backup & Datenträgerverwaltung",
    "app.logo": "OneClick Backup",
    "app.logo_sub": "Datenträgerverwaltung",
    "sidebar.dashboard": "Übersicht",
    "sidebar.clone": "Klonen / Migrieren",
    "sidebar.partitions": "Partitionen",
    "sidebar.backup": "Sicherung",
    "sidebar.convert": "Konvertierung",
    "sidebar.recovery": "Wiederherstellung",
    "sidebar.scheduler": "Zeitplaner",
    "sidebar.history": "Verlauf",
    "sidebar.advanced": "Erweiterte Tools",
    "page.scheduler": "Sicherungsplaner",
    "page.history": "Betriebsverlauf",
    "admin.ok": "✅ Administrator",
    "admin.limited": "⚠️ Eingeschränkt",
    "admin.elevate": "Als Admin starten",
    "status.ready": "Bereit",
    "status.pending_one": "1 ausstehend",
    "status.pending_n": "{n} ausstehend",
    "queue.title": "Ausstehende Operationen",
    "queue.empty": "Keine ausstehenden Operationen",
    "queue.apply": "Alle anwenden",
    "queue.clear": "Löschen",
    "page.dashboard": "Übersicht – Datenträger",
    "page.clone": "Klonen & OS-Migration",
    "page.partitions": "Partitionsverwaltung",
    "page.backup": "Sicherung & Wiederherstellung",
    "page.convert": "Datenträgerkonvertierung",
    "page.recovery": "Partitionswiederherstellung",
    "page.advanced": "Erweiterte Tools",
    "dash.total_disks": "Datenträger",
    "dash.total_storage": "Speicher gesamt",
    "dash.ssds": "SSDs",
    "dash.hdds": "HDDs",
    "dash.overview": "Datenträgerübersicht",
    "dash.select_partition": "Partition auswählen\nfür Details",
    "dash.no_disks": "Keine Datenträger gefunden. Als Administrator starten.",
    "dash.refresh": "⟳ Aktualisieren",
    "dash.loading": "Laden...",
    "clone.title": "\U0001f4bf  Klonen & OS-Migration",
    "clone.desc": "Gesamten Datenträger klonen oder nur das OS migrieren.",
    "clone.source": "QUELLE",
    "clone.target": "ZIEL",
    "clone.start": "▶  Klonen starten",
    "part.title": "\U0001f527  Partitionsverwaltung",
    "part.create": "Erstellen",
    "part.resize": "Größe ändern",
    "part.merge": "Zusammenführen",
    "part.format": "Formatieren",
    "part.delete": "Löschen",
    "part.change_letter": "Buchstabe ändern",
    "part.set_active": "Aktivieren",
    "conv.title": "\U0001f504  Konvertierungstools",
    "conv.convert": "Konvertieren",
    "bak.title": "\U0001f4be  Sicherung & Wiederherstellung",
    "bak.tab_create": "Sicherung erstellen",
    "bak.tab_restore": "Wiederherstellen",
    "bak.tab_settings": "Einstellungen",
    "bak.create_btn": "▶  Sicherung erstellen",
    "bak.browse": "Durchsuchen",
    "rec.title": "\U0001f50d  Partitionswiederherstellung",
    "rec.back": "◀ Zurück",
    "rec.next": "Weiter ▶",
    "rec.recover": "Wiederherstellen",
    "rec.quick": "Schnellscan",
    "rec.deep": "Tiefenscan",
    "adv.title": "⚙️  Erweiterte Tools",
    "common.no_disks": "Keine Datenträger",
    "common.loading": "Laden...",
    "common.error": "Fehler",
    "common.language": "Sprache",
    "common.disk": "Datenträger",
}

# ── Arabic ─────────────────────────────────────────────────────────────────
_T["ar"] = {
    "app.title": "وان كليك باك آب – مدير الأقراص",
    "app.logo": "OneClick Backup",
    "app.logo_sub": "مدير الأقراص",
    "sidebar.dashboard": "لوحة التحكم",
    "sidebar.clone": "استنساخ / ترحيل",
    "sidebar.partitions": "الأقسام",
    "sidebar.backup": "النسخ الاحتياطي",
    "sidebar.convert": "التحويلات",
    "sidebar.recovery": "الاسترداد",
    "sidebar.scheduler": "المجدول",
    "sidebar.history": "السجل",
    "sidebar.advanced": "أدوات متقدمة",
    "page.scheduler": "جدولة النسخ الاحتياطي",
    "page.history": "سجل العمليات",
    "admin.ok": "✅ مسؤول",
    "admin.limited": "⚠️ وضع محدود",
    "admin.elevate": "تشغيل كمسؤول",
    "status.ready": "جاهز",
    "dash.total_disks": "إجمالي الأقراص",
    "dash.total_storage": "التخزين الكلي",
    "dash.ssds": "SSD",
    "dash.hdds": "HDD",
    "dash.overview": "نظرة عامة على الأقراص",
    "dash.refresh": "⟳ تحديث",
    "dash.loading": "جارٍ التحميل...",
    "clone.title": "\U0001f4bf  استنساخ وترحيل النظام",
    "clone.source": "القرص المصدر",
    "clone.target": "القرص الهدف",
    "clone.start": "▶  بدء الاستنساخ",
    "part.title": "\U0001f527  إدارة الأقسام",
    "part.create": "إنشاء",
    "part.resize": "تغيير الحجم",
    "part.merge": "دمج",
    "part.format": "تهيئة",
    "part.delete": "حذف",
    "conv.title": "\U0001f504  أدوات التحويل",
    "conv.convert": "تحويل",
    "bak.title": "\U0001f4be  النسخ الاحتياطي والاستعادة",
    "bak.tab_create": "إنشاء نسخة",
    "bak.tab_restore": "استعادة",
    "bak.tab_settings": "الإعدادات",
    "bak.create_btn": "▶  إنشاء النسخة الاحتياطية",
    "bak.browse": "تصفح",
    "rec.title": "\U0001f50d  استرداد الأقسام",
    "rec.back": "◀ رجوع",
    "rec.next": "التالي ▶",
    "rec.recover": "استرداد",
    "rec.quick": "فحص سريع",
    "rec.deep": "فحص عميق",
    "adv.title": "⚙️  أدوات متقدمة",
    "common.no_disks": "لم يتم العثور على أقراص",
    "common.loading": "جارٍ التحميل...",
    "common.error": "خطأ",
    "common.language": "اللغة",
    "common.disk": "قرص",
}

# ── Chinese Simplified ─────────────────────────────────────────────────────
_T["zh"] = {
    "app.title": "OneClick Backup 磁盘管理器",
    "app.logo": "OneClick Backup",
    "app.logo_sub": "磁盘管理专业版",
    "sidebar.dashboard": "仪表盘",
    "sidebar.clone": "克隆 / 迁移",
    "sidebar.partitions": "分区管理",
    "sidebar.backup": "备份与恢复",
    "sidebar.convert": "转换工具",
    "sidebar.recovery": "分区恢复",
    "sidebar.advanced": "高级工具",
    "admin.ok": "✅ 管理员",
    "admin.limited": "⚠️ 受限模式",
    "admin.elevate": "以管理员运行",
    "status.ready": "就绪",
    "dash.total_disks": "磁盘总数",
    "dash.total_storage": "总存储",
    "dash.ssds": "固态硬盘",
    "dash.hdds": "机械硬盘",
    "dash.overview": "磁盘概览",
    "dash.refresh": "⟳ 刷新",
    "dash.loading": "加载中...",
    "clone.title": "\U0001f4bf  克隆与系统迁移",
    "clone.source": "源磁盘",
    "clone.target": "目标磁盘",
    "clone.start": "▶  开始克隆",
    "part.title": "\U0001f527  分区管理",
    "part.create": "创建",
    "part.resize": "调整大小",
    "part.merge": "合并",
    "part.format": "格式化",
    "part.delete": "删除",
    "conv.title": "\U0001f504  转换工具",
    "conv.convert": "转换",
    "bak.title": "\U0001f4be  备份与恢复",
    "bak.tab_create": "创建备份",
    "bak.tab_restore": "恢复",
    "bak.tab_settings": "设置",
    "bak.create_btn": "▶  创建备份",
    "bak.browse": "浏览",
    "rec.title": "\U0001f50d  分区恢复",
    "rec.back": "◀ 返回",
    "rec.next": "下一步 ▶",
    "rec.recover": "恢复",
    "rec.quick": "快速扫描",
    "rec.deep": "深度扫描",
    "adv.title": "⚙️  高级工具",
    "common.no_disks": "未找到磁盘",
    "common.loading": "加载中...",
    "common.error": "错误",
    "common.language": "语言",
    "common.disk": "磁盘",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def t(key: str, **kwargs) -> str:
    """Translate *key* in the current language.

    Supports ``{placeholder}`` substitution via keyword arguments.
    Falls back to English, then returns the key itself.
    """
    with _lang_lock:
        lang = _current_lang
    text = _T.get(lang, {}).get(key)
    if text is None:
        text = _T["en"].get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def set_language(lang: str) -> None:
    """Switch the active language (e.g. ``'fr'``, ``'en'``)."""
    global _current_lang
    if lang in LANGUAGES:
        with _lang_lock:
            _current_lang = lang
        _save_preference(lang)


def get_language() -> str:
    """Return the current language code."""
    with _lang_lock:
        return _current_lang


def get_languages() -> dict[str, str]:
    """Return ``{code: display_name}`` for every available language."""
    return dict(LANGUAGES)


def load_preference() -> None:
    """Load the saved language preference from disk.

    Priority: saved preference file > system locale detection > ``'en'``.
    """
    global _current_lang
    try:
        if os.path.isfile(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            lang = data.get("lang")
            if lang and lang in LANGUAGES:
                with _lang_lock:
                    _current_lang = lang
                return
    except Exception as exc:
        _log.debug("Could not load language preference from %s: %s", _SETTINGS_FILE, exc)
    # No saved preference -- fall back to system locale detection
    detected = _detect_system_locale()
    with _lang_lock:
        _current_lang = detected


def _save_preference(lang: str) -> None:
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump({"lang": lang}, fh)
    except Exception:
        pass


def validate_translations(*, reference_lang: str = "en") -> dict[str, list[str]]:
    """Return a dict mapping each language code to its list of missing keys.

    Compares every language against *reference_lang* (default English).
    Only logs warnings when a logger is active; always returns the result.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    ref_keys = set(_T.get(reference_lang, {}).keys())
    missing: dict[str, list[str]] = {}
    for lang_code in LANGUAGES:
        if lang_code == reference_lang:
            continue
        lang_keys = set(_T.get(lang_code, {}).keys())
        diff = sorted(ref_keys - lang_keys)
        if diff:
            missing[lang_code] = diff
            _logger.warning(
                "i18n: language '%s' is missing %d keys: %s",
                lang_code, len(diff), ", ".join(diff[:10])
                + (f" ... (+{len(diff) - 10} more)" if len(diff) > 10 else ""),
            )
    return missing


# Auto-load preference on import
load_preference()
