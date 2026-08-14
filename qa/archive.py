"""
Classement des rapports QA dans output/<Pays>/<Trigger>/<Sequence>/, avec
rotation : seuls les 2 rapports les plus recents de chaque TYPE (wording,
url, combined - voir qa.combined_report) sont conserves sur disque dans un
meme dossier de sequence (les plus anciens sont supprimes). L'historique des
scores (qa.history), lui, n'est jamais purge : il garde la trace de tous les
runs, y compris ceux dont le fichier Excel a ete supprime par la rotation.

Wording et url d'une meme sequence partagent le meme dossier (voir
qa.classification.normalize_sequence_name pour la reconciliation des noms de
sequence entre les deux onglets) ; le type de rapport est distingue par le
prefixe du nom de fichier (QA_wording_/QA_url_/QA_combined_), pas par le
dossier.
"""
import glob
import os
import re
import shutil
from datetime import datetime

from qa.config import OUTPUT_DIR, UNCLASSIFIED_LABEL

KEEP_REPORTS = 2


def safe_folder_name(name: str) -> str:
    name = (name or "").strip() or UNCLASSIFIED_LABEL
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def sequence_dir(country: str, trigger: str, sequence: str) -> str:
    return os.path.join(
        OUTPUT_DIR,
        safe_folder_name(country),
        safe_folder_name(trigger),
        safe_folder_name(sequence),
    )


def list_reports(country: str, trigger: str, sequence: str, mode: str = None) -> list:
    """Rapports existants pour cette sequence, du plus recent au plus ancien.
    mode (wording/url/combined) restreint aux rapports de ce type ; None
    retourne tous les rapports du dossier quel que soit leur type."""
    dest_dir = sequence_dir(country, trigger, sequence)
    pattern = f"QA_{mode}_*.xlsx" if mode else "*.xlsx"
    return sorted(
        glob.glob(os.path.join(dest_dir, pattern)),
        key=os.path.getmtime,
        reverse=True
    )


def archive_report(report_path: str, country: str, trigger: str, sequence: str, mode: str) -> str:
    """
    Copie report_path dans output/<Pays>/<Trigger>/<Sequence>/ sous un nom
    prefixe par son type (QA_<mode>_...), puis ne garde que les
    KEEP_REPORTS rapports les plus recents de ce type dans ce dossier
    (n'affecte pas les rapports des autres types partageant le dossier).
    """
    dest_dir = sequence_dir(country, trigger, sequence)
    os.makedirs(dest_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_path = os.path.join(dest_dir, f"QA_{mode}_{safe_folder_name(sequence)}_{timestamp}.xlsx")
    shutil.copy2(report_path, dest_path)

    for old_file in list_reports(country, trigger, sequence, mode=mode)[KEEP_REPORTS:]:
        try:
            os.remove(old_file)
        except OSError:
            pass

    return dest_path


def delete_sequence_dir(country: str, trigger: str, sequence: str) -> None:
    """Supprime le dossier de rapports d'une sequence (fichiers Excel
    archives). Ne touche pas a l'historique des scores (voir
    qa.history.delete_classification) : les deux sont a appeler ensemble
    pour supprimer une sequence entierement depuis la page Historique."""
    dest_dir = sequence_dir(country, trigger, sequence)
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir, ignore_errors=True)


def move_sequence_dir(old_country: str, old_trigger: str, old_sequence: str,
                       new_country: str, new_trigger: str, new_sequence: str) -> None:
    """Deplace le dossier de rapports d'une sequence vers un nouveau
    (Pays, Trigger, Sequence) - renommage, ou fusion si le dossier de
    destination existe deja (auquel cas un fichier de meme nom est suffixe
    plutot qu'ecrase). Reapplique la rotation par type apres coup, au cas ou
    la fusion depasserait KEEP_REPORTS pour un type donne."""
    src_dir = sequence_dir(old_country, old_trigger, old_sequence)
    if not os.path.isdir(src_dir):
        return

    dst_dir = sequence_dir(new_country, new_trigger, new_sequence)
    os.makedirs(dst_dir, exist_ok=True)

    for fname in os.listdir(src_dir):
        src_file = os.path.join(src_dir, fname)
        dst_file = os.path.join(dst_dir, fname)
        if os.path.exists(dst_file):
            base, ext = os.path.splitext(fname)
            dst_file = os.path.join(dst_dir, f"{base}_fusionne{ext}")
        shutil.move(src_file, dst_file)

    try:
        os.rmdir(src_dir)
    except OSError:
        pass

    for mode in ("wording", "url", "combined"):
        for old_file in list_reports(new_country, new_trigger, new_sequence, mode=mode)[KEEP_REPORTS:]:
            try:
                os.remove(old_file)
            except OSError:
                pass
