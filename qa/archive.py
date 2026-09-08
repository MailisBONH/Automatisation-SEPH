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

La meme regle KEEP_REPORTS s'applique aussi aux rapports "bruts" generes
directement a la racine de OUTPUT_DIR, un par comparaison, avant tout
archivage (voir list_root_reports/prune_root_reports) - sans cette purge,
ils s'accumuleraient indefiniment puisque chaque clic sur Comparer en cree
un nouveau, archive ou non.
"""
import glob
import os
import re
import shutil
import threading
from datetime import datetime

from qa.config import KEEP_REPORTS, OUTPUT_DIR, UNCLASSIFIED_LABEL

# Streamlit sert chaque session utilisateur dans un thread du meme process
# (voir qa.history._LOCK, meme raisonnement) : sans verrou, deux sessions qui
# archivent la meme (country, trigger, sequence, mode) dans la meme seconde
# pourraient toutes les deux passer la boucle de collision ci-dessous avant
# que l'une des deux n'ait ecrit son fichier, et la seconde ecraserait alors
# silencieusement le rapport de la premiere.
_LOCK = threading.RLock()


def safe_folder_name(name: str) -> str:
    name = (name or "").strip() or UNCLASSIFIED_LABEL
    name = re.sub(r'[<>:"/\\|?*]', "_", name)

    # Un segment compose uniquement de points (".", "..", "...") est un
    # composant special pour os.path.join/os.path.normpath (repertoire
    # courant/parent) : aucun des caracteres neutralises ci-dessus ne le
    # couvre, donc un pays/trigger/sequence valant exactement ".." (ex. un
    # onglet Excel litteralement nomme ainsi, ou une saisie libre dans le
    # formulaire de renommage de la page Historique) traverserait sinon
    # l'arborescence hors de OUTPUT_DIR une fois passe a sequence_dir().
    if re.fullmatch(r"\.+", name):
        return UNCLASSIFIED_LABEL

    return name


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
    retourne tous les rapports du dossier quel que soit leur type.

    Trie sur le nom de fichier (qui embarque le timestamp d'archivage,
    "QA_<mode>_<sequence>_YYYYMMDD_HHMMSS.xlsx", voir archive_report),
    jamais sur le mtime disque : archive_report copie le rapport avec
    shutil.copy2, qui preserve le mtime du FICHIER SOURCE plutot que de
    dater la copie a l'instant de l'archivage - deux rapports sources
    generes a quelques millisecondes d'intervalle (cas courant en test,
    possible aussi en usage reel avec des rapports pre-generes) peuvent
    alors se retrouver avec un mtime identique ou inverse une fois copies,
    et l'ordre que renverrait glob.glob sur une egalite de mtime n'est de
    toute facon pas garanti par l'OS. Pour un (mode, sequence) fixe - le cas
    de chaque appel ici, le mode et la sequence etant constants sur tous les
    fichiers renvoyes par le glob - le nom de fichier trie alphabetiquement
    equivaut a un tri chronologique correct, insensible a la resolution/au
    comportement de copie du mtime."""
    dest_dir = sequence_dir(country, trigger, sequence)
    pattern = f"QA_{mode}_*.xlsx" if mode else "*.xlsx"
    return sorted(
        glob.glob(os.path.join(dest_dir, pattern)),
        key=os.path.basename,
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
    base_name = f"QA_{mode}_{safe_folder_name(sequence)}_{timestamp}"
    dest_path = os.path.join(dest_dir, f"{base_name}.xlsx")
    # La resolution du timestamp est a la seconde : plusieurs archive_report()
    # pour le meme (country, trigger, sequence, mode) dans la meme seconde
    # (ex. le mode "Wording + URL" qui archive plusieurs onglets a la suite
    # dans une boucle serree) produiraient sinon un dest_path identique, et
    # le shutil.copy2 suivant ecraserait silencieusement le rapport
    # precedent. On suffixe par un compteur incrementel tant que le chemin
    # est deja pris - le pattern de glob utilise par list_reports/la rotation
    # ("QA_<mode>_*.xlsx") continue de matcher ces fichiers suffixes, et le
    # suffixe "_N" trie apres le "." de l'extension du fichier non suffixe
    # (donc apres lui dans le tri alphabetique inverse de list_reports), ce
    # qui le classe correctement comme "plus recent". Verification puis copie
    # se font sous _LOCK : sans lui, deux sessions Streamlit (deux threads du
    # meme process) pourraient toutes les deux passer la boucle de collision
    # avant que l'une n'ait ecrit son fichier, et la seconde ecraserait alors
    # silencieusement le rapport de la premiere.
    with _LOCK:
        counter = 2
        while os.path.exists(dest_path):
            dest_path = os.path.join(dest_dir, f"{base_name}_{counter}.xlsx")
            counter += 1
        shutil.copy2(report_path, dest_path)

        for old_file in list_reports(country, trigger, sequence, mode=mode)[KEEP_REPORTS:]:
            try:
                os.remove(old_file)
            except OSError:
                pass

    return dest_path


def list_root_reports() -> list:
    """Rapports "bruts" generes a la racine de OUTPUT_DIR (un par clic sur
    Comparer, voir qa.report.save_report/save_consolidated_report), avant
    tout archivage/classification - jamais dans un sous-dossier
    Pays/Trigger/Sequence (voir sequence_dir). Du plus recent au plus ancien."""
    return sorted(
        glob.glob(os.path.join(OUTPUT_DIR, "*.xlsx")),
        key=os.path.getmtime,
        reverse=True
    )


def prune_root_reports(keep: int = KEEP_REPORTS) -> None:
    """
    Ne garde que les `keep` rapports les plus recents a la racine de
    OUTPUT_DIR. Ces fichiers sont generes a chaque comparaison (voir
    qa.report.save_report/save_consolidated_report, qui appellent cette
    fonction juste apres chaque ecriture) et s'accumuleraient sinon
    indefiniment, contrairement aux rapports archives dans un dossier de
    sequence (deja soumis a la meme regle KEEP_REPORTS via archive_report).
    Purge inconditionnelle, meme si le run correspondant n'a jamais ete
    archive : le rapport reste telechargeable depuis l'app tant qu'il n'a
    pas ete supprime, comme n'importe quel fichier de temp/ (voir
    qa.cleanup). Ne touche jamais aux sous-dossiers Pays/Trigger/Sequence.
    """
    for old_file in list_root_reports()[keep:]:
        try:
            os.remove(old_file)
        except OSError:
            pass


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

    # Sur un systeme de fichiers insensible a la casse (Windows, cas de cette
    # appli), un (country, trigger, sequence) qui ne differe du precedent que
    # par la casse (ex. "seq 1" -> "SEQ 1", ou un pays/trigger dont seule la
    # casse change) resout src_dir et dst_dir vers le MEME dossier physique.
    # C'est alors un no-op : rien a deplacer. Sans ce garde-fou, la boucle de
    # collision ci-dessous trouverait chaque fichier "deja present" a
    # destination (puisque c'est le fichier source lui-meme) et le
    # suffixerait a tort en "_fusionne", corrompant les noms de fichiers pour
    # ce qui devrait etre une simple correction de casse. os.path.exists()
    # avant samefile() protege le cas (le cas normal, non case-only) ou
    # dst_dir n'existe pas encore.
    if os.path.exists(dst_dir) and os.path.samefile(src_dir, dst_dir):
        return

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
