import streamlit as st
import pandas as pd
import functools
import getpass
import html
import os
import io
import re
import threading
import uuid
import contextlib
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from qa.archive import archive_report
from qa.classification import detect_classification, normalize_sequence_name, resolve_sequence_alias
from qa.cleanup import cleanup_old_temp_files_throttled
from qa.combined_report import build_combined_report
from qa.diagnostics import (
    find_duplicate_components,
    find_empty_wordings,
    find_unbalanced_aem_blocks,
    find_unused_excel_classes,
    find_whitespace_anomalies,
    find_wrong_language_wordings,
)
from qa.engine import QA_RUN_READ_ERRORS, run_qa, run_qa_for_sequence
from qa.example_workbook import build_example_workbook_bytes
from qa.excel_loader import (
    EXCEL_READ_ERRORS,
    _open_excel_file,
    list_url_sequence_choices,
    list_wording_sheet_choices,
    load_excel_urls,
    load_excel_wordings,
    load_generic_wordings,
)
from qa.history import find_regression, migrate_legacy_jsonl_history, record_run
from qa.html_extraction import load_and_extract_html_texts
from qa.notifications import escape_teams_markdown, send_teams_notification
from qa.report import URL_HEADER_LABELS, save_consolidated_report, save_report, sort_results_by_html_order

from datetime import datetime

import plotly.express as px
from st_aggrid import AgGrid
from st_aggrid import GridOptionsBuilder
from st_aggrid.shared import JsCode

import difflib


def highlight_differences(excel_text, html_text, char_level=False):
    """Diff en gras rouge entre 2 textes. Par mots par defaut (wording) ;
    char_level=True decoupe caractere par caractere (mode URL, ou une URL
    n'a pas d'espaces - un decoupage par mots surlignerait l'URL entiere en
    rouge pour la moindre difference).

    Le resultat est injecte tel quel comme innerHTML dans le tableau AgGrid
    (voir _render_table) : chaque token est html.escape() avant d'etre
    entoure du <span> de surlignage, pour qu'un wording/texte HTML contenant
    litteralement des caracteres '<'/'>' (email fourni par un tiers,
    composant mal forme...) s'affiche comme du texte plutot que de
    s'executer comme du HTML/JS dans le navigateur."""

    excel_text = str(excel_text or "")
    html_text = str(html_text or "")

    if char_level:
        excel_tokens = list(excel_text)
        html_tokens = list(html_text)
        sep = ""
    else:
        excel_tokens = excel_text.split()
        html_tokens = html_text.split()
        sep = " "

    matcher = difflib.SequenceMatcher(
        None,
        excel_tokens,
        html_tokens
    )

    excel_parts = []
    html_parts = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():

        ex = html.escape(sep.join(excel_tokens[i1:i2]))
        ht = html.escape(sep.join(html_tokens[j1:j2]))

        if tag == "equal":

            excel_parts.append(ex)
            html_parts.append(ht)

        else:

            excel_parts.append(
                f"<span style='font-weight:bold;color:#d62828'>{ex}</span>"
            )

            html_parts.append(
                f"<span style='font-weight:bold;color:#d62828'>{ht}</span>"
            )

    return (
        sep.join(excel_parts),
        sep.join(html_parts)
    )


def _send_teams_notification_safely(subject, message):
    """Point d'entree du thread daemon fire-and-forget qui envoie la
    notification Teams (voir _do_archive) : send_teams_notification()
    n'attrape deja que requests.RequestException (best-effort sur l'appel
    reseau lui-meme, voir qa/notifications.py) - toute AUTRE exception
    (bug de construction du message, regression future...) ne remonterait
    sinon nulle part de visible pour l'utilisateur de l'app (le thread est
    daemon, jamais rejoint), et ne serait imprimee que via le hook
    d'exception par defaut de threading, sans le contexte (sujet) de la
    notification concernee. Avale tout, comme le reste des fonctionnalites
    de confort de l'outil (historique, nettoyage temp...) : la notification
    Teams n'est jamais critique pour la QA elle-meme."""
    try:
        send_teams_notification(subject, message)
    except Exception as e:
        print(f"❌ Notification Teams ({subject}) : erreur inattendue : {e}")


def _score_pct(ok, total, digits=1):
    """Pourcentage OK/total arrondi, 0 si total est nul (aucune ligne a
    tester - un onglet vide, une sequence sans resultat...) plutot qu'une
    ZeroDivisionError. Motif repete a plusieurs endroits (recap batch,
    archivage, diagnostic Excel, score combine...) : centralise ici pour
    qu'aucun de ces points d'appel n'oublie la garde `if total`."""
    return round(ok / total * 100, digits) if total else 0


def run_with_debug_capture(fn, *args, **kwargs):
    """Execute fn en capturant tout ce qu'elle imprime (stdout), pour
    l'afficher dans l'UI. Le flux cible est enveloppe dans
    _ThreadSafeLineBuffer : fn peut elle-meme paralleliser du travail sur
    plusieurs threads (voir _run_jobs_in_parallel, appelee via cette
    fonction par _execute_batch/_execute_combined_batch) sans risquer de
    fusionner deux lignes issues de threads differents - le meme wrapper ne
    change rien d'observable pour un fn strictement sequentiel (voir
    _execute_single_or_combined_test)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(_ThreadSafeLineBuffer(buf)):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def _write_batch_html(uploaded_file, session_id) -> str:
    """Ecrit un fichier uploade (mode batch, plusieurs HTML par run) dans un
    chemin temporaire propre a ce fichier - contrairement au mode simple (un
    seul chemin fixe par session), chaque fichier du batch doit avoir le
    sien pour ne pas s'ecraser les uns les autres pendant la boucle."""
    os.makedirs("temp", exist_ok=True)
    path = f"temp/{session_id}_{uuid.uuid4().hex[:6]}.html"
    with open(path, "wb") as out:
        out.write(uploaded_file.getvalue())
    return path


def _run_batch_qa(html_path, excel_path, keyword, mode, include_generic, label):
    """
    Execute run_qa_for_sequence pour un fichier/sequence du batch (mode
    "batch" ou moitie wording/url du mode "combined_batch", voir le bouton
    Comparer plus bas) : si CE fichier precis est illisible, retourne un
    message d'erreur clair plutot que d'interrompre tout le run - les
    autres fichiers du batch continuent d'etre traites.

    Retourne (results, error_message) : error_message est None en cas de
    succes (results contient alors les resultats), ou l'inverse en cas
    d'echec. N'appelle plus st.error()/st.* directement (contrairement aux
    versions precedentes) : cette fonction est executee depuis un thread
    worker par _run_jobs_in_parallel, et Streamlit n'autorise pas les appels
    a son API depuis un thread autre que celui du script - c'est a
    l'appelant, sur le thread principal, d'afficher error_message avec
    st.error() une fois tous les jobs termines.
    """
    try:
        results, _ = run_qa_for_sequence(
            html_file=html_path,
            excel_file=excel_path,
            keyword=keyword,
            mode=mode,
            include_generic=include_generic,
        )
        return results, None
    except QA_RUN_READ_ERRORS as e:
        message = f"❌ {label} : {e}"
        print(message)
        return None, message


class _ThreadSafeLineBuffer:
    """Wrapper autour d'un flux (ex: io.StringIO) a utiliser comme cible de
    contextlib.redirect_stdout pendant que des jobs tournent en parallele
    sur un ThreadPoolExecutor (voir _run_jobs_in_parallel) : sys.stdout est
    un attribut global au process, partage par tous les threads, donc
    chaque print() (2 appels .write() distincts : le texte, puis le retour
    ligne) d'un job peut sinon s'entrelacer avec celui d'un autre job en
    cours - fusionnant par exemple le texte de deux sequences differentes
    en une seule ligne illisible dans l'expander "Logs de debug".

    Accumule les ecritures par thread (threading.local) jusqu'a une ligne
    complete, puis n'ecrit cette ligne dans le flux partage que sous verrou
    - une ligne entiere d'un thread ne peut alors plus se retrouver coupee
    au milieu par l'ecriture d'un autre thread."""

    def __init__(self, target):
        self._target = target
        self._lock = threading.Lock()
        self._local = threading.local()

    def write(self, text):
        pending = getattr(self._local, "pending", "") + text
        *complete_lines, self._local.pending = pending.split("\n")
        if complete_lines:
            with self._lock:
                for line in complete_lines:
                    self._target.write(line + "\n")

    def flush(self):
        pass


def _run_jobs_in_parallel(jobs):
    """
    Execute une liste de callables sans argument (voir les boucles du
    bouton Comparer plus bas, mode batch) en parallele et retourne leurs
    resultats dans le meme ordre que `jobs` - chaque job est un appel a
    _run_batch_qa, independant des autres (fichier/sequence different),
    donc majoritairement en attente d'I/O (lecture du classeur Excel et du
    fichier HTML) plutot que de calcul pur : les executer sur des threads
    plutot que sequentiellement raccourcit d'autant l'attente sur un batch
    de plusieurs fichiers, malgre le GIL.

    ThreadPoolExecutor.map preserve l'ordre de `jobs` quel que soit l'ordre
    reel de completion, ce qui permet a l'appelant de reassocier chaque
    resultat a son fichier/sequence d'origine par simple zip(). Aucun job
    n'appelle de fonction Streamlit (voir _run_batch_qa) : executer du code
    Streamlit depuis un thread secondaire n'est pas supporte.
    """
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as executor:
        return list(executor.map(lambda job: job(), jobs))


def _build_and_run_combined_batch_jobs(combined_sequence_assignments, excel_path, include_generic, session_id):
    """Ecrit les fichiers HTML temporaires puis construit et execute en
    parallele 2 jobs par fichier (Wording et URL, voir _run_jobs_in_parallel)
    - partie commune a _execute_combined_batch factorisee ici pour etre
    executee via run_with_debug_capture (voir plus haut) plutot que de
    dupliquer sa capture de stdout."""
    # Ecriture des fichiers temporaires d'abord (sequentielle, rapide) :
    # chaque job ci-dessous doit avoir son chemin final deja pret,
    # _write_batch_html n'est pas thread-safe a partager (compteur uuid
    # mis a part, aucun probleme reel, mais autant garder l'IO de
    # fichier hors des threads).
    batch_paths = [_write_batch_html(f, session_id) for f, _, _ in combined_sequence_assignments]

    # 2 jobs independants par paire (wording + url), executes en
    # parallele (voir _run_jobs_in_parallel) - un fichier lent a lire ne
    # retarde plus tous les autres.
    jobs = []
    for (f, w_seq, u_seq), batch_html_path in zip(combined_sequence_assignments, batch_paths):
        print(f"\n{'=' * 20} {w_seq} / {u_seq} ({f.name}) {'=' * 20}")

        jobs.append(lambda p=batch_html_path, s=w_seq, name=f.name: _run_batch_qa(
            p, excel_path, s, "wording", include_generic, label=f"Wording {s} ({name})"
        ))
        # include_generic ne s'applique qu'au wording (case a cocher non
        # affichee du tout en mode url, voir plus haut) - jamais la
        # valeur (potentiellement True) choisie pour la moitie wording
        # de ce meme run combine.
        jobs.append(lambda p=batch_html_path, s=u_seq, name=f.name: _run_batch_qa(
            p, excel_path, s, "url", False, label=f"URL {s} ({name})"
        ))

    return _run_jobs_in_parallel(jobs)


def _execute_combined_batch(excel_path, combined_sequence_assignments, include_generic, session_id):
    """
    Execute le mode batch "Wording + URL" (voir le bouton Comparer) :
    ecrit les fichiers HTML temporaires, lance en parallele 2 jobs par
    fichier (Wording et URL, voir _build_and_run_combined_batch_jobs), puis
    genere les rapports consolides. Ne fait AUCUN appel a l'API Streamlit
    (st.*) : entierement testable avec de simples objets Python (voir
    tests/test_home.py), les erreurs de fichiers individuels et les logs de
    debug sont retournes plutot qu'affiches directement, a charge de
    l'appelant (le bouton Comparer plus bas) de les rendre.

    Retourne (last_run, error_messages, debug_logs) : last_run est le dict a
    stocker dans st.session_state["last_run"] (kind="combined_batch"), ou
    None si aucun fichier n'a produit le moindre resultat (rien a
    afficher/archiver, seuls les logs de debug ont un sens dans ce cas).
    """
    wording_results_by_sequence = {}
    url_results_by_sequence = {}
    error_messages = []

    outcomes, debug_logs = run_with_debug_capture(
        _build_and_run_combined_batch_jobs, combined_sequence_assignments, excel_path, include_generic, session_id
    )

    for i, (f, w_seq, u_seq) in enumerate(combined_sequence_assignments):
        w_results, w_error = outcomes[2 * i]
        u_results, u_error = outcomes[2 * i + 1]

        if w_error:
            error_messages.append(w_error)
        if w_results is not None:
            wording_results_by_sequence[w_seq] = w_results

        if u_error:
            error_messages.append(u_error)
        if u_results is not None:
            url_results_by_sequence[u_seq] = u_results

    if not (wording_results_by_sequence or url_results_by_sequence):
        return None, error_messages, debug_logs

    today = datetime.now().strftime("%Y-%m-%d")

    wording_report_path = (
        save_consolidated_report(wording_results_by_sequence, mode="wording")
        if wording_results_by_sequence else None
    )
    url_report_path = (
        save_consolidated_report(url_results_by_sequence, mode="url")
        if url_results_by_sequence else None
    )

    pairs = []
    recap_rows = []

    for f, w_seq, u_seq in combined_sequence_assignments:
        w_rows = wording_results_by_sequence.get(w_seq, [])
        u_rows = url_results_by_sequence.get(u_seq, [])

        pairs.append({
            "file_name": f.name,
            "wording_sequence": w_seq,
            "wording_rows": w_rows,
            "url_sequence": u_seq,
            "url_rows": u_rows,
        })

        w_total, w_ok = len(w_rows), sum(1 for r in w_rows if r["Status"] == "OK")
        u_total, u_ok = len(u_rows), sum(1 for r in u_rows if r["Status"] == "OK")

        recap_rows.append({
            "Fichier": f.name,
            "Séquence Wording": w_seq,
            "Score Wording": _score_pct(w_ok, w_total),
            "Séquence URL": u_seq,
            "Score URL": _score_pct(u_ok, u_total),
        })

    last_run = {
        "kind": "combined_batch",
        "excel_path": excel_path,
        "title": f"Comparaison terminée pour {len(pairs)} séquence(s) (Wording + URL)",
        "pairs": pairs,
        "recap_rows": recap_rows,
        "wording_report_path": wording_report_path,
        "wording_download_filename": f"qa_report_multi_wording_{today}.xlsx",
        "url_report_path": url_report_path,
        "url_download_filename": f"qa_report_multi_url_{today}.xlsx",
        "debug_logs": debug_logs,
    }
    return last_run, error_messages, debug_logs


def _build_and_run_batch_jobs(sequence_assignments, excel_path, qa_mode, include_generic, session_id):
    """Ecrit les fichiers HTML temporaires puis construit et execute en
    parallele un job par fichier (voir _run_jobs_in_parallel) - equivalent
    de _build_and_run_combined_batch_jobs pour le mode batch a un seul type
    de test (Wording OU URL), factorise de la meme facon pour etre execute
    via run_with_debug_capture."""
    batch_paths = [_write_batch_html(f, session_id) for f, _ in sequence_assignments]

    jobs = []
    for (f, seq_name), batch_html_path in zip(sequence_assignments, batch_paths):
        print(f"\n{'=' * 20} {seq_name} ({f.name}) {'=' * 20}")
        jobs.append(lambda p=batch_html_path, s=seq_name, name=f.name: _run_batch_qa(
            p, excel_path, s, qa_mode, include_generic, label=f"{s} ({name})"
        ))

    return _run_jobs_in_parallel(jobs)


def _execute_batch(excel_path, sequence_assignments, qa_mode, include_generic, session_id):
    """
    Equivalent de _execute_combined_batch pour le mode batch a un seul type
    de test (Wording OU URL, voir le bouton Comparer) : un seul job par
    fichier plutot que deux (voir _build_and_run_batch_jobs). Memes
    garanties (aucun appel st.*, entierement testable) et meme forme de
    retour (last_run, error_messages, debug_logs).
    """
    results_by_sequence = {}
    error_messages = []

    outcomes, debug_logs = run_with_debug_capture(
        _build_and_run_batch_jobs, sequence_assignments, excel_path, qa_mode, include_generic, session_id
    )

    for (f, seq_name), (seq_results, error_message) in zip(sequence_assignments, outcomes):
        if error_message:
            error_messages.append(error_message)
        if seq_results is not None:
            results_by_sequence[seq_name] = seq_results

    if not results_by_sequence:
        return None, error_messages, debug_logs

    report_path = save_consolidated_report(results_by_sequence, mode=qa_mode)
    today = datetime.now().strftime("%Y-%m-%d")

    recap_rows = []
    for seq_name, rows in results_by_sequence.items():
        seq_total = len(rows)
        seq_ok = sum(1 for r in rows if r["Status"] == "OK")
        recap_rows.append({
            "Séquence": seq_name,
            "Total": seq_total,
            "OK": seq_ok,
            "KO": seq_total - seq_ok,
            "Score": _score_pct(seq_ok, seq_total)
        })

    last_run = {
        "kind": "batch",
        "mode": qa_mode,
        "excel_path": excel_path,
        "title": f"Comparaison terminée pour {len(results_by_sequence)} séquence(s)",
        "download_filename": f"qa_report_multi_{today}.xlsx",
        "report_path": report_path,
        "sheet_name": None,
        "sequences": results_by_sequence,
        "recap_rows": recap_rows,
        "debug_logs": debug_logs,
    }
    return last_run, error_messages, debug_logs


def _execute_single_or_combined_test(html_path, excel_path, test_wording, test_url,
                                      wording_keyword, url_keyword, include_generic):
    """
    Execute une comparaison sur un seul HTML (mode simple, "kind" single ou
    combined_test - voir le bouton Comparer) : Wording seul, URL seul, ou
    les deux pipelines l'un apres l'autre sur le meme fichier. Aucun appel
    st.* (voir _execute_combined_batch pour le rationnel) : l'erreur de
    lecture eventuelle est retournee (message d'exception brut) plutot
    qu'affichee, a charge de l'appelant de la reformuler pour l'utilisateur.

    Retourne (last_run, error_message) : error_message est None en cas de
    succes (last_run contient alors le dict a stocker dans
    st.session_state["last_run"]), ou l'inverse en cas d'echec.
    """
    try:
        if test_wording and test_url:
            # Wording + URL : les deux pipelines tournent sur le meme
            # HTML/Excel (un seul email peut etre teste sur les deux
            # aspects a la fois), avec chacun son propre onglet/sequence.
            (wording_results, wording_report_path, wording_sheet_name), wording_logs = run_with_debug_capture(
                run_qa,
                html_file=html_path,
                excel_file=excel_path,
                keyword=wording_keyword,
                mode="wording",
                include_generic=include_generic
            )
            (url_results, url_report_path, url_sheet_name), url_logs = run_with_debug_capture(
                run_qa,
                html_file=html_path,
                excel_file=excel_path,
                keyword=url_keyword,
                mode="url"
            )

            today = datetime.now().strftime("%Y-%m-%d")
            safe_wording_name = re.sub(r"[^\w\-]", "_", wording_sheet_name)
            safe_url_name = re.sub(r"[^\w\-]", "_", url_sheet_name)

            last_run = {
                "kind": "combined_test",
                "excel_path": excel_path,
                "title": "Comparaison terminée (Wording + URL)",
                "wording_results": wording_results,
                "wording_report_path": wording_report_path,
                "wording_sequence": wording_keyword,
                "wording_download_filename": f"qa_report_wording_{safe_wording_name}_{today}.xlsx",
                "url_results": url_results,
                "url_report_path": url_report_path,
                # onglet UTM plat (pas de decoupage en sequences, voir
                # url_ready) : url_keyword est vide, on reutilise la
                # sequence wording (meme sequence testee des deux cotes).
                "url_sequence": url_keyword or wording_keyword,
                "url_download_filename": f"qa_report_url_{safe_url_name}_{today}.xlsx",
                "debug_logs": f"--- Wording ---\n{wording_logs}\n\n--- URL ---\n{url_logs}",
            }

        else:
            qa_mode = "wording" if test_wording else "url"
            keyword = wording_keyword if test_wording else url_keyword

            (results, report_path, sheet_name), debug_logs = run_with_debug_capture(
                run_qa,
                html_file=html_path,
                excel_file=excel_path,
                keyword=keyword,
                mode=qa_mode,
                include_generic=include_generic
            )

            today = datetime.now().strftime("%Y-%m-%d")
            safe_sheet_name = re.sub(r"[^\w\-]", "_", sheet_name)

            last_run = {
                "kind": "single",
                "mode": qa_mode,
                "excel_path": excel_path,
                "title": "Comparaison terminée",
                "download_filename": f"qa_report_{safe_sheet_name}_{today}.xlsx",
                "report_path": report_path,
                "results": results,
                "sheet_name": sheet_name,
                # keyword == la sequence choisie par l'utilisateur (onglet UTM
                # en mode url, ou onglet Excel en mode wording - dans ce cas
                # identique a sheet_name). En mode url, sheet_name est le nom
                # de l'onglet UTM (souvent unique, partage par toutes les
                # sequences) : ce n'est PAS la sequence, il ne faut pas s'en
                # servir pour le classement par sequence dans l'historique -
                # sauf si keyword est vide (onglet UTM plat, voir url_ready),
                # auquel cas c'est le seul nom disponible.
                "sequence": keyword or sheet_name,
                "debug_logs": debug_logs,
            }

        return last_run, None

    except QA_RUN_READ_ERRORS as e:
        return None, str(e)


# Memes libelles que l'export Excel (qa.report.URL_HEADER_LABELS), pour que
# l'apercu Streamlit et le rapport telecharge affichent toujours les memes
# noms de colonne - un renommage/ajout de colonne ne se fait plus qu'a un
# seul endroit.
URL_COLUMN_RENAME = URL_HEADER_LABELS


def _safe_html_cell(status, excel_value, html_value, char_level=False):
    """Version HTML-safe (echappee, avec diff en gras rouge sur un KO) d'une
    paire Excel Wording/HTML Wording - seul point (avec highlight_differences
    lui-meme) qui produit le HTML brut ensuite injecte tel quel comme
    innerHTML dans le tableau AgGrid (voir _render_table, colonnes excel_col/
    html_col) : un KO passe par le diff mot-a-mot ou caractere par caractere
    (deja echappe token par token, voir highlight_differences), un OK n'a
    rien a surligner mais doit tout de meme etre echappe - un wording
    contenant litteralement '<'/'>' ne doit jamais s'executer comme du
    HTML/JS. Utilisee par _prepare_display_df pour les deux modes (wording
    et url) plutot que dupliquee dans chacun, pour qu'un futur mode ne
    puisse pas oublier cet echappement."""
    if status == "KO":
        return highlight_differences(excel_value, html_value, char_level=char_level)
    return (
        html.escape(str(excel_value or "")),
        html.escape(str(html_value or "")),
    )


def _prepare_display_df(results, mode):
    """df = donnees brutes (pour les metriques/graphiques) ; df_display =
    version mise en forme pour le tableau (colonnes URL renommees, diff
    mot-a-mot en gras rouge sur les KO en mode wording).

    Trie par ordre d'apparition dans le HTML (voir qa.report), comme le
    rapport Excel telecharge : sans ca, l'apercu Streamlit affiche les
    resultats dans leur ordre de production interne (tous les KO/matchs
    directs d'abord, les wordings "retrouves apres coup" comme les branches
    if/else soeurs ou les mentions manquantes tout a la fin), qui n'a aucun
    rapport avec l'ordre reel de la page.
    """
    df = pd.DataFrame(results)
    if "_html_order" in df.columns:
        df = sort_results_by_html_order(df).reset_index(drop=True)
        df = df.drop(columns="_html_order")

    if mode == "url":
        display_cols = [
            c for c in [
                "Sequence", "Source Section", "Expected Class", "HTML Class",
                "Excel Wording", "HTML Wording", "Status", "Error Type", "Similarity", "Occurrences"
            ]
            if c in df.columns
        ]
        df_display = df[display_cols].copy()

        for idx, row in df_display.iterrows():
            excel_safe, html_safe = _safe_html_cell(
                row["Status"], row.get("Excel Wording", ""), row.get("HTML Wording", ""), char_level=True
            )
            df_display.at[idx, "Excel Wording"] = excel_safe
            df_display.at[idx, "HTML Wording"] = html_safe

        df_display = df_display.rename(columns=URL_COLUMN_RENAME)
    else:
        df_display = df.copy()

        for idx, row in df_display.iterrows():
            excel_safe, html_safe = _safe_html_cell(
                row["Status"], row.get("Excel Wording", ""), row.get("HTML Wording", "")
            )
            df_display.at[idx, "Excel Wording"] = excel_safe
            df_display.at[idx, "HTML Wording"] = html_safe

    return df, df_display


def _render_summary_and_charts(df):
    """Metrique de succes + camemberts (statut global, repartition des
    erreurs) pour un jeu de resultats (df = donnees brutes, colonne Status
    obligatoire)."""
    ok_count = len(df[df["Status"] == "OK"]) if len(df) else 0
    ko_count = len(df[df["Status"] == "KO"]) if len(df) else 0

    total = ok_count + ko_count

    success_rate = round(
        ok_count / total * 100,
        1
    ) if total else 0

    st.metric(
        "Taux de réussite QA",
        f"{success_rate}%"
    )

    status_df = pd.DataFrame({
        "Status": ["OK", "KO"],
        "Count": [ok_count, ko_count]
    })

    fig_status = px.pie(
        status_df,
        values="Count",
        names="Status",
        hole=0.6,
        color="Status",
        title="Résultat global",
        color_discrete_map={
            "OK": "#28a745",
            "KO": "#dc3545"
        }
    )

    ko_df = df[df["Status"] == "KO"] if len(df) else df

    if len(ko_df) > 0:

        error_counts = (
            ko_df["Error Type"]
            .fillna("Unknown")
            .value_counts()
            .reset_index()
        )

        error_counts.columns = [
            "Error Type",
            "Count"
        ]

        fig_errors = px.pie(
            error_counts,
            values="Count",
            names="Error Type",
            hole=0.6,
            title="Répartition des erreurs"
        )

        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            st.plotly_chart(
                fig_status,
                use_container_width=True
            )

        with chart_col2:
            st.plotly_chart(
                fig_errors,
                use_container_width=True
            )


def _render_table(results, mode, key_suffix):
    """Tableau AgGrid detaille (lignes vertes/rouges, diff mot-a-mot en gras
    rouge sur les KO en mode wording) pour un jeu de resultats, sans
    metriques ni bouton de telechargement (voir render_results_block /
    render_grouped_results pour la vue complete)."""
    _, df_display = _prepare_display_df(results, mode)

    show_only_errors = st.checkbox(
        "🔎 Afficher uniquement les erreurs (KO)",
        key=f"filter_ko_{key_suffix}"
    )
    if show_only_errors:
        df_display = df_display[df_display["Status"] == "KO"]

    if df_display.empty:
        st.caption("Aucune erreur — tous les éléments sont OK. 🎉" if show_only_errors else "Aucun résultat.")
        return

    html_renderer = JsCode("""
    class HtmlCellRenderer {
        init(params) {

            this.eGui = document.createElement('div');

            this.eGui.style.color = 'black';
            this.eGui.style.fontSize = '14px';

            this.eGui.innerHTML = params.value || '';
        }

        getGui() {
            return this.eGui;
        }
    }
    """)

    gb = GridOptionsBuilder.from_dataframe(df_display)

    gb.configure_default_column(
        wrapText=True,
        autoHeight=True,
        resizable=True
    )

    row_style = JsCode("""
    function(params) {

        if (params.data.Status === 'OK') {
            return {
                'backgroundColor': '#d4edda'
            }
        }

        return {
            'backgroundColor': '#f8d7da'
        }
    }
    """)

    # En mode URL, ces colonnes ont ete renommees en francais dans
    # df_display (voir _prepare_display_df / URL_COLUMN_RENAME) - il faut
    # configurer le rendu HTML sur leur nom final, pas l'original.
    excel_col, html_col = (
        ("URL attendue (Excel)", "URL trouvée (HTML)") if mode == "url"
        else ("Excel Wording", "HTML Wording")
    )

    gb.configure_column(
        excel_col,
        cellRenderer=html_renderer,
        width=500
    )

    gb.configure_column(
        html_col,
        cellRenderer=html_renderer,
        width=500
    )

    grid_options = gb.build()

    grid_options["getRowStyle"] = row_style

    custom_css = {
        ".ag-root-wrapper": {
            "color": "black !important"
        },
        ".ag-cell": {
            "color": "black !important"
        },

        ".ag-row": {
            "color": "black !important"
        }
    }

    df_display = df_display.fillna("")
    df_display = df_display.astype(str)

    AgGrid(
        df_display,
        gridOptions=grid_options,
        allow_unsafe_jscode=True,
        custom_css=custom_css,
        height=800,
        theme="streamlit",
        key=f"grid_{key_suffix}"
    )


def render_results_block(results, report_path, title, download_filename, mode="wording"):
    """Affiche metriques, graphiques, tableau detaille et bouton de telechargement pour un jeu de resultats."""

    st.success(f"✅ {title}")

    df, _ = _prepare_display_df(results, mode)
    _render_summary_and_charts(df)
    _render_table(results, mode, key_suffix=download_filename)

    with open(report_path, "rb") as f:
        st.download_button(
            "⬇️ Télécharger le rapport Excel",
            data=f,
            file_name=download_filename,
            key=f"download_{download_filename}"
        )


def render_grouped_results(results_by_sequence, mode, report_path, download_filename, title):
    """
    Comme render_results_block, mais pour un run multi-sequences : les
    metriques/graphiques globaux restent communs a toutes les sequences,
    mais le tableau detaille de chaque sequence est isole dans son propre
    expander plutot que fusionne dans un seul grand tableau - impossible de
    confondre un OK/KO d'une sequence avec celui d'une autre.
    """
    st.success(f"✅ {title}")

    all_rows = []
    for seq, rows in results_by_sequence.items():
        for r in rows:
            row = dict(r)
            row["Sequence"] = seq
            all_rows.append(row)

    df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(columns=["Status"])
    _render_summary_and_charts(df)

    with open(report_path, "rb") as f:
        st.download_button(
            "⬇️ Télécharger le rapport Excel consolidé",
            data=f,
            file_name=download_filename,
            key=f"download_{download_filename}"
        )

    st.markdown("**Détail par séquence :**")

    for seq, rows in results_by_sequence.items():
        seq_total = len(rows)
        seq_ok = sum(1 for r in rows if r["Status"] == "OK")
        seq_score = _score_pct(seq_ok, seq_total)
        icon = "✅" if seq_total and seq_ok == seq_total else "⚠️"

        with st.expander(f"{icon} {seq} — {seq_ok}/{seq_total} OK ({seq_score}%)"):
            if rows:
                _render_table(rows, mode, key_suffix=f"{download_filename}_{seq}")
            else:
                st.caption("Aucun résultat pour cette séquence.")


def _run_items(last_run):
    """
    Liste de {mode, sequence, rows, report_path} a archiver pour ce run.
    report_path=None signifie qu'un rapport individuel doit encore etre
    genere (cas batch : le rapport consolide ne se decoupe pas par
    sequence, un rapport par sequence est regenere au moment d'archiver).

    Le kind "combined_test" (mode "Wording + URL") retourne un item par
    type - c'est ce qui permet a _do_archive de les archiver chacun sous
    son propre type dans le meme dossier de sequence (une fois reconcilies,
    voir normalize_sequence_name) et de declencher le rapport combine.
    """
    kind = last_run["kind"]

    if kind == "batch":
        return [
            {"mode": last_run["mode"], "sequence": seq_name, "rows": rows, "report_path": None}
            for seq_name, rows in last_run["sequences"].items()
        ]

    if kind == "combined_test":
        return [
            {
                "mode": "wording", "sequence": last_run["wording_sequence"],
                "rows": last_run["wording_results"], "report_path": last_run["wording_report_path"]
            },
            {
                "mode": "url", "sequence": last_run["url_sequence"],
                "rows": last_run["url_results"], "report_path": last_run["url_report_path"]
            },
        ]

    if kind == "combined_batch":
        # Deux items par paire (un wording, un url), chacun regenere son
        # propre rapport individuel a l'archivage (les rapports consolides
        # ne se decoupent pas par sequence). Une paire dont un cote a echoue
        # (voir le try/except du bouton Comparer) a des rows vides pour ce
        # cote : on ne l'archive pas (rien de reel a y mettre).
        items = []
        for pair in last_run["pairs"]:
            if pair["wording_rows"]:
                items.append({
                    "mode": "wording", "sequence": pair["wording_sequence"],
                    "rows": pair["wording_rows"], "report_path": None
                })
            if pair["url_rows"]:
                items.append({
                    "mode": "url", "sequence": pair["url_sequence"],
                    "rows": pair["url_rows"], "report_path": None
                })
        return items

    return [{
        "mode": last_run["mode"],
        "sequence": last_run.get("sequence") or last_run["sheet_name"],
        "rows": last_run["results"],
        "report_path": last_run["report_path"],
    }]


def _archive_key(last_run):
    """Cle session_state identifiant ce run pour l'archivage (idempotence)."""
    if last_run["kind"] in ("combined_test", "combined_batch"):
        return f"archived::{last_run['wording_report_path']}::{last_run['url_report_path']}"
    return f"archived::{last_run['report_path']}"


def _do_archive(last_run, country, trigger):
    """Archive chaque item du run (voir _run_items) dans
    output/<Pays>/<Trigger>/<Sequence>/ (voir qa.archive) et enregistre
    l'entree d'historique correspondante (voir qa.history.record_run).

    Le nom de sequence est normalise (voir qa.classification) avant
    archivage : wording ("SEQ 1 NON LOYAL") et url ("SEQ 1 NON FID") d'une
    meme sequence tombent ainsi dans le meme dossier plutot que deux
    dossiers distincts. Des que les deux types sont presents pour une
    sequence, un rapport combine Wording+URL est (re)genere (voir
    qa.combined_report) et son propre score est aussi archive dans
    l'historique (mode="combined").

    Au passage : calcule la repartition par type d'erreur de chaque item
    (voir qa.history.aggregate_error_types/error_type_monthly_trend, page
    Historique), detecte une eventuelle regression de score par rapport au
    dernier run archive du meme (pays, trigger, sequence, mode) (voir
    qa.history.find_regression - appele AVANT chaque record_run, sinon le
    run courant se comparerait a lui-meme), et notifie un webhook Teams si
    configure (qa.notifications.send_teams_notification, no-op silencieux
    sinon). Retourne la liste des regressions detectees (eventuellement
    vide), affichee par l'appelant (voir ensure_archived)."""
    resolved_sequences = set()
    regressions = []
    summary_lines = []
    combined_error_types = {}

    try:
        # Utilisateur du systeme d'exploitation (ex: "mbonhomme") : l'outil
        # n'a pas de systeme d'authentification propre, donc pas d'autre
        # notion fiable de "qui" sans ajouter de friction (champ a remplir a
        # chaque comparaison). getuser() peut lever OSError si aucun nom
        # d'utilisateur n'est determinable (environnement tres restreint) -
        # tested_by reste alors None, comme avant l'ajout de cette
        # fonctionnalite (voir qa.history.record_run).
        tested_by = getpass.getuser()
    except OSError:
        tested_by = None

    for item in _run_items(last_run):
        sequence = resolve_sequence_alias(country, trigger, normalize_sequence_name(item["sequence"]))
        resolved_sequences.add(sequence)

        rows = item["rows"]
        total = len(rows)
        ok = sum(1 for r in rows if r["Status"] == "OK")
        score = _score_pct(ok, total, digits=2)

        error_types = Counter(r["Error Type"] for r in rows if r["Status"] == "KO" and r.get("Error Type"))
        combined_error_types.setdefault(sequence, Counter()).update(error_types)

        regression = find_regression(country, trigger, sequence, item["mode"], score)
        if regression:
            regressions.append({"sequence": sequence, "mode": item["mode"], **regression})

        summary_lines.append(
            f"- **{escape_teams_markdown(item['sequence'])}** ({item['mode']}) : {score}% ({ok}/{total} OK)"
        )

        report_path = item["report_path"] or save_report(rows, item["sequence"], mode=item["mode"])
        archive_report(report_path, country, trigger, sequence, mode=item["mode"])
        record_run(
            item["sequence"], item["mode"], total, ok, total - ok, score,
            country=country, trigger=trigger, sequence=sequence, error_types=dict(error_types),
            tested_by=tested_by,
        )

    for sequence in resolved_sequences:
        combined_path, combined_stats = build_combined_report(country, trigger, sequence)
        if combined_stats:
            combined_regression = find_regression(country, trigger, sequence, "combined", combined_stats["score"])
            if combined_regression:
                regressions.append({"sequence": sequence, "mode": "combined", **combined_regression})
            summary_lines.append(
                f"- **{escape_teams_markdown(sequence)}** (combined) : {combined_stats['score']}% "
                f"({combined_stats['ok']}/{combined_stats['total']} OK)"
            )
            record_run(
                sequence, "combined",
                combined_stats["total"], combined_stats["ok"], combined_stats["ko"], combined_stats["score"],
                country=country, trigger=trigger, sequence=sequence,
                error_types=dict(combined_error_types.get(sequence, {})),
                tested_by=tested_by,
            )

    notification_lines = list(summary_lines)
    if regressions:
        notification_lines.append("")
        notification_lines.append("⚠️ **Régression(s) détectée(s) :**")
        for r in regressions:
            notification_lines.append(
                f"- {escape_teams_markdown(r['sequence'])} ({r['mode']}) : "
                f"{r['previous_score']}% → {r['current_score']}% ({r['delta']:+.1f} pts)"
            )
    # Fire-and-forget : send_teams_notification() reste synchrone et
    # unitairement testable (voir tests/test_notifications.py), mais
    # l'attendre ici bloquerait l'archivage - donc l'affichage des resultats
    # a l'utilisateur - jusqu'a 10s (son timeout, voir qa/notifications.py)
    # si Teams est lent/indisponible, alors que la notification est une
    # fonctionnalite de confort, jamais critique pour la QA elle-meme.
    threading.Thread(
        target=_send_teams_notification_safely,
        args=(
            f"QA {escape_teams_markdown(country)} / {escape_teams_markdown(trigger)}",
            "\n".join(notification_lines),
        ),
        daemon=True,
    ).start()

    return regressions


@st.dialog("📁 Informations manquantes pour l'historique")
def _prompt_missing_classification(last_run, guess, sequences):
    st.write(
        "Le pays et/ou le trigger n'ont pas pu être détectés automatiquement à partir du "
        "classeur Excel. Merci de les compléter pour archiver ce rapport dans l'historique."
    )
    st.caption(f"Séquence(s) concernée(s) : {', '.join(sequences)}")

    country = st.text_input("Pays", value=guess.get("country") or "")
    trigger = st.text_input("Trigger", value=guess.get("trigger") or "")

    archive_key = _archive_key(last_run)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("💾 Valider et archiver", use_container_width=True):
            country, trigger = country.strip(), trigger.strip()
            if not country or not trigger:
                st.error("Les deux champs sont obligatoires.")
            else:
                regressions = _do_archive(last_run, country, trigger) or []
                st.session_state[archive_key] = {"country": country, "trigger": trigger, "regressions": regressions}
                st.rerun()
    with col2:
        if st.button("Ne pas archiver", use_container_width=True):
            st.session_state[archive_key] = {"skipped": True}
            st.rerun()


def ensure_archived(last_run):
    """
    Archive automatiquement ce run dans l'historique des qu'il est produit,
    sans action de l'utilisateur : pays/trigger detectes depuis le contenu
    du classeur Excel (voir qa.classification), la sequence etant deja
    connue de maniere fiable par ailleurs. Si le pays et/ou le trigger n'ont
    pas pu etre detectes, ouvre une pop-up pour les completer avant
    d'archiver. Idempotent (ne re-traite pas un run deja archive/skip).
    """
    archive_key = _archive_key(last_run)
    if archive_key in st.session_state:
        return

    items = _run_items(last_run)
    if not items:
        return

    # La detection pays (langue de la colonne wording) ne fonctionne qu'en
    # mode wording : on privilegie cet item quand il existe (mode combine),
    # sinon le premier disponible.
    detection_item = next((i for i in items if i["mode"] == "wording"), items[0])
    guess = detect_classification(last_run["excel_path"], detection_item["sequence"], detection_item["mode"])
    country, trigger = guess.get("country"), guess.get("trigger")

    sequences = [i["sequence"] for i in items]

    if country and trigger:
        regressions = _do_archive(last_run, country, trigger) or []
        st.session_state[archive_key] = {"country": country, "trigger": trigger, "regressions": regressions}
    else:
        _prompt_missing_classification(last_run, guess, sequences)


NO_SELECTION = "— Sélectionnez —"


def _guess_sequence_choice(filename, options):
    """Suggestion (jamais une pre-selection silencieuse, voir NO_SELECTION)
    de la sequence la plus probable pour un fichier HTML uploade en mode
    batch, a partir de la ressemblance entre son nom de fichier et le nom de
    sequence - affichee en caption sous le menu deroulant tant qu'aucun
    choix n'a ete fait, pour accelerer l'association sans jamais decider a
    la place de l'utilisateur (une suggestion peut se tromper, une
    pre-selection silencieuse ferait lancer la comparaison sur la mauvaise
    campagne sans que personne ne s'en aperçoive)."""
    base = re.sub(r"[^a-z0-9]", "", os.path.splitext(filename)[0].lower())
    if not base:
        return None

    best_option, best_score = None, 0.0
    for option in options:
        normalized_option = re.sub(r"[^a-z0-9]", "", option.lower())
        if not normalized_option:
            continue
        score = difflib.SequenceMatcher(None, base, normalized_option).ratio()
        if normalized_option in base or base in normalized_option:
            score = max(score, 0.9)
        if score > best_score:
            best_option, best_score = option, score

    return best_option if best_score >= 0.6 else None


_DIAGNOSTIC_RENDERERS = {
    "caption": st.caption,
    "success": st.success,
    "warning": st.warning,
    "error": st.error,
}


def _run_excel_diagnostics(excel_path, html_path, test_wording, test_url,
                            wording_keyword, url_keyword, include_generic):
    """Diagnostic du classeur Excel (et du HTML si deja uploade) avant de
    lancer la QA (mode simple, hors multi-sequences) : onglets detectes,
    taux de remplissage des classes techniques, composants dupliques,
    wordings dans la mauvaise langue/vides/mal espaces, blocs AEM mal
    formes, classes Excel introuvables dans le HTML. Aucun appel st.* (meme
    convention que _execute_batch/_do_archive) : retourne une liste de
    (level, text) - level parmi "caption"/"success"/"warning"/"error" (voir
    _DIAGNOSTIC_RENDERERS) - pour que l'appelant puisse decider si le
    panneau doit s'ouvrir automatiquement (presence d'un warning/error)
    avant de le rendre, sans dupliquer toute cette logique pour le savoir."""
    messages = []

    try:
        xls_preview = _open_excel_file(excel_path)
        messages.append(("caption", f"Onglets détectés : {', '.join(xls_preview.sheet_names)}"))
    except EXCEL_READ_ERRORS as e:
        messages.append(("error", f"Impossible de lire le fichier Excel : {e}"))
        xls_preview = None

    if xls_preview is None:
        return messages

    if test_wording:
        try:
            diag_sheet, diag_wordings = load_excel_wordings(excel_path, wording_keyword)
            messages.append(("success", f"✅ Onglet trouvé : **{diag_sheet}** — {len(diag_wordings)} wordings chargés"))

            n_with_class = sum(1 for w in diag_wordings if w.get("Technical"))
            fill_rate = _score_pct(n_with_class, len(diag_wordings))

            if fill_rate == 100:
                messages.append(("success", "Classes techniques (TECHNICAL NAME) renseignées sur 100% des lignes."))
            elif fill_rate == 0:
                messages.append(("warning",
                    "⚠️ Colonne 'TECHNICAL NAME' absente ou vide sur tout l'onglet — "
                    "le matching par classe ne fonctionnera pas, tout reposera sur la comparaison de texte seule."
                ))
            else:
                messages.append(("warning",
                    f"⚠️ Classes techniques renseignées sur seulement {fill_rate}% des lignes — "
                    "le matching sera moins précis pour les lignes sans classe."
                ))

            combined_wordings = list(diag_wordings)

            if include_generic:
                try:
                    diag_generic_sheet, diag_generic_wordings = load_generic_wordings(excel_path)
                    messages.append(("success",
                        f"✅ Onglet générique trouvé : **{diag_generic_sheet}** — "
                        f"{len(diag_generic_wordings)} wording(s) génériques seront ajoutés"
                    ))
                    combined_wordings += diag_generic_wordings
                except ValueError as e:
                    messages.append(("error", f"❌ Composants génériques : {e}"))

            duplicates = find_duplicate_components(combined_wordings)
            if duplicates:
                details = "; ".join(f"**{comp}** ({', '.join(classes)})" for comp, classes in duplicates)
                messages.append(("warning",
                    f"⚠️ Composant(s) présents avec plusieurs classes techniques différentes — "
                    f"souvent un bloc dupliqué par erreur (copier-coller) : {details}"
                ))

            language_mismatches = find_wrong_language_wordings(combined_wordings)
            if language_mismatches:
                preview = "; ".join(
                    f"**{m['Component']}** ({m['detected_lang']}) : \"{str(m['Wording'])[:40]}...\""
                    for m in language_mismatches[:5]
                )
                more = f" (+{len(language_mismatches) - 5} autre(s))" if len(language_mismatches) > 5 else ""
                messages.append(("warning",
                    f"⚠️ {len(language_mismatches)} wording(s) semblent dans une langue différente "
                    f"de la langue dominante (`{language_mismatches[0]['expected_lang']}`) — "
                    f"probablement collés depuis une autre campagne/marché : {preview}{more}"
                ))

            empty_wordings = find_empty_wordings(combined_wordings)
            if empty_wordings:
                details = "; ".join(f"**{w['Component']}** / {w['Element']}" for w in empty_wordings[:10])
                more = f" (+{len(empty_wordings) - 10} autre(s))" if len(empty_wordings) > 10 else ""
                messages.append(("warning", f"⚠️ {len(empty_wordings)} wording(s) vide(s) dans le référentiel : {details}{more}"))

            whitespace_issues = find_whitespace_anomalies(combined_wordings)
            if whitespace_issues:
                details = "; ".join(
                    f"**{w['Component']}** / {w['Element']} ({w['issue']})" for w in whitespace_issues[:10]
                )
                more = f" (+{len(whitespace_issues) - 10} autre(s))" if len(whitespace_issues) > 10 else ""
                messages.append(("warning",
                    f"⚠️ {len(whitespace_issues)} wording(s) avec une anomalie d'espacement "
                    f"(souvent invisible dans Excel) : {details}{more}"
                ))

            unbalanced_blocks = find_unbalanced_aem_blocks(combined_wordings)
            if unbalanced_blocks:
                details = "; ".join(f"**{w['Component']}** / {w['Element']}" for w in unbalanced_blocks[:10])
                more = f" (+{len(unbalanced_blocks) - 10} autre(s))" if len(unbalanced_blocks) > 10 else ""
                messages.append(("warning",
                    f"⚠️ {len(unbalanced_blocks)} wording(s) avec un bloc conditionnel AEM mal formé "
                    f"(balises `<%`/`%>` non équilibrées) : {details}{more}"
                ))

            if html_path:
                try:
                    diag_html_texts = load_and_extract_html_texts(html_path)
                    unused_classes = find_unused_excel_classes(diag_html_texts, combined_wordings)
                    if unused_classes:
                        messages.append(("warning",
                            "⚠️ Classe(s) technique(s) déclarée(s) dans l'Excel mais introuvable(s) dans le HTML "
                            "(le nom a probablement divergé du vrai template) : "
                            + ", ".join(f"`{c}`" for c in unused_classes)
                        ))
                except (OSError, UnicodeDecodeError):
                    pass  # le HTML sera re-analyse (avec message d'erreur clair) au clic sur Comparer

        except ValueError as e:
            messages.append(("error", f"❌ {e}"))

    if test_url:
        try:
            diag_sheet, _, diag_urls = load_excel_urls(excel_path, url_keyword)
            messages.append(("success",
                f"✅ Onglet UTM trouvé : **{diag_sheet}** — "
                f"{len(diag_urls)} URL(s) de référence pour cette séquence"
            ))
        except ValueError as e:
            messages.append(("error", f"❌ {e}"))

    return messages


@functools.lru_cache(maxsize=8)
def _cached_excel_diagnostics(excel_path, excel_mtime_ns, excel_size,
                               html_path, html_mtime_ns, html_size,
                               test_wording, test_url, wording_keyword, url_keyword, include_generic):
    """Cache de _run_excel_diagnostics par (chemin, mtime_ns, taille) de
    chaque fichier - meme principe que qa.excel_loader._cached_excel_file.
    Sans ce cache, le diagnostic (qui reparse tout le classeur onglet par
    onglet - et le HTML si deja uploade -, plus un appel
    langdetect.detect() par wording, voir find_wrong_language_wordings)
    tournerait a chaque interaction avec un widget de la page, pas
    seulement au clic sur Comparer : Streamlit reexecute tout le script a
    chaque rerun. mtime_ns+taille (pas seulement le chemin) pour ne jamais
    servir un diagnostic perime si l'utilisateur reuploade un fichier
    different sous le meme chemin temporaire (voir home.py, un chemin fixe
    par session)."""
    return _run_excel_diagnostics(
        excel_path, html_path, test_wording, test_url, wording_keyword, url_keyword, include_generic
    )


def _run_excel_diagnostics_cached(excel_path, html_path, test_wording, test_url,
                                   wording_keyword, url_keyword, include_generic):
    """Point d'entree cache de _run_excel_diagnostics (voir
    _cached_excel_diagnostics) : calcule la cle de cache (stat() de chaque
    fichier) puis delegue."""
    excel_stat = os.stat(excel_path)
    if html_path:
        html_stat = os.stat(html_path)
        html_mtime_ns, html_size = html_stat.st_mtime_ns, html_stat.st_size
    else:
        html_mtime_ns, html_size = None, None

    return _cached_excel_diagnostics(
        excel_path, excel_stat.st_mtime_ns, excel_stat.st_size,
        html_path, html_mtime_ns, html_size,
        test_wording, test_url, wording_keyword, url_keyword, include_generic,
    )


st.set_page_config(page_title="QA Email Tool", layout="wide")

header_col1, header_col2 = st.columns([5, 1])
with header_col1:
    st.title("📧 Email QA Tool")
with header_col2:
    st.page_link("pages/1_Historique.py", label="📈 Historique", use_container_width=True)

# Un identifiant unique par session de navigateur, pour que les fichiers
# temporaires de deux utilisateurs (ou deux onglets) ne s'ecrasent pas.
if "session_id" not in st.session_state:
    st.session_state["session_id"] = uuid.uuid4().hex[:8]
session_id = st.session_state["session_id"]

# Purge les fichiers de temp/ plus vieux que 24h (throttle : ne scanne
# reellement le dossier qu'une fois par heure au plus, voir qa.cleanup).
cleanup_old_temp_files_throttled()

# Migration unique et idempotente de l'ancien historique JSONL vers SQLite
# (voir qa.history.migrate_legacy_jsonl_history) - appelee ici (et pas
# seulement depuis la page Historique) pour garantir qu'elle s'execute
# AVANT le moindre record_run de cette page (ex: le premier "Comparer"
# apres la mise a jour) : sinon ce record_run creerait une base neuve vide
# avant que la migration n'ait eu la chance de tourner, abandonnant
# silencieusement l'historique existant (voir le docstring de la fonction).
migrate_legacy_jsonl_history()

with st.expander("❓ Comment utiliser cet outil ?"):
    st.markdown(
        "1. Choisissez le **type de test** (Wording, URL, ou les deux).\n"
        "2. Glissez-déposez le **fichier HTML** de l'email et le **fichier Excel** référentiel.\n"
        "3. Une fois l'Excel chargé, sélectionnez la **campagne/séquence** à tester dans le "
        "menu déroulant qui apparaît.\n"
        "4. Cliquez sur **🔍 Comparer**.\n"
        "5. Le résultat affiche un taux de réussite, le détail ligne par ligne (vert = OK, "
        "rouge = KO), et un bouton pour télécharger le rapport Excel complet.\n\n"
        "Chaque comparaison est automatiquement archivée dans la page **📈 Historique** "
        "(en haut à droite), qui garde une trace de l'évolution du score dans le temps."
    )
    st.caption(
        "Vous ne savez pas à quoi doit ressembler le fichier Excel référentiel ? "
        "Téléchargez un exemple minimal pour voir la structure attendue (onglet par "
        "séquence pour le wording, onglet contenant \"UTM\" pour les URL trackées)."
    )
    st.download_button(
        "📥 Télécharger un exemple de fichier Excel",
        data=build_example_workbook_bytes(),
        file_name="exemple_referentiel_qa.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# =========================
# MODE (Wording / URL)
# =========================
mode = st.radio(
    "Type de test",
    ["Wording", "URL", "Wording + URL"],
    index=0,  # par défaut wording
    horizontal=True,
    help=(
        "**Wording** : compare les textes de l'email au référentiel Excel.\n\n"
        "**URL** : vérifie que les liens de l'email pointent vers les bonnes URL trackées.\n\n"
        "**Wording + URL** : lance les deux vérifications en même temps sur le même email."
    )
)

test_wording = mode in ("Wording", "Wording + URL")
test_url = mode in ("URL", "Wording + URL")

# Repliees par defaut : ces deux reglages ne concernent qu'une minorite de
# campagnes (plusieurs sequences a la fois, composants generiques dans un
# onglet separe) - les laisser toujours visibles alourdissait la page pour
# le cas le plus frequent (un seul HTML, mode Wording simple).
with st.expander("⚙️ Options avancées"):
    batch_mode = st.checkbox(
        "📦 Traiter plusieurs séquences en une fois (même Excel, plusieurs HTML)",
        help=(
            "À cocher si vous avez plusieurs emails HTML à tester d'un coup pour la même "
            "campagne (un seul fichier Excel, un HTML par séquence)."
        )
    )

    include_generic = False
    if test_wording:
        include_generic = st.checkbox(
            "🧩 Les composants génériques (footer, mentions légales, fidélité...) "
            "sont dans un onglet séparé de l'Excel",
            help=(
                "À cocher si le référentiel Excel de cette campagne ne répète pas, dans chaque "
                "séquence, les composants communs à toutes les séquences (footer, mentions "
                "légales...) : ils seront ajoutés automatiquement depuis leur onglet dédié."
            )
        )
        if include_generic:
            st.caption(
                "ℹ️ L'onglet des composants génériques est retrouvé automatiquement "
                "(nom contenant \"GENERIC\") et fusionné avec l'onglet de la séquence testée."
            )

# =========================
# UPLOAD FILES
# =========================
col4, col5 = st.columns(2)

with col4:
    excel_file = st.file_uploader(
        "📊 Drag & drop Excel",
        type=["xlsx"],
        help="Le classeur référentiel validé par les équipes marketing (format .xlsx)."
    )

with col5:
    if batch_mode:
        html_files = st.file_uploader(
            "📄 Drag & drop plusieurs fichiers HTML (un par séquence)",
            type=["html"],
            accept_multiple_files=True,
            help="Un fichier HTML par séquence à tester (export \"view-source\" AEM ou HTML brut)."
        )
        html_file = None
    else:
        html_file = st.file_uploader(
            "📄 Drag & drop HTML",
            type=["html"],
            help="L'export HTML de l'email à vérifier (export \"view-source\" AEM ou HTML brut)."
        )
        html_files = None

excel_path = None
if excel_file:
    os.makedirs("temp", exist_ok=True)
    excel_path = f"temp/{session_id}_input.xlsx"
    with open(excel_path, "wb") as f:
        f.write(excel_file.getvalue())

html_path = None
if html_file:
    os.makedirs("temp", exist_ok=True)
    html_path = f"temp/{session_id}_input.html"
    with open(html_path, "wb") as f:
        f.write(html_file.getvalue())

sequence_assignments = []
combined_sequence_assignments = []

# Definis ici (et pas seulement dans la branche "else" plus bas) : en mode
# batch, ces variables ne sont pas utilisees pour choisir une sequence (voir
# sequence_assignments/combined_sequence_assignments), mais le calcul de
# missing_keyword plus bas les reference inconditionnellement (test_wording/
# test_url ne savent pas que le mode batch les rend hors-sujet).
wording_keyword = ""
url_keyword = ""
url_ready = True

if batch_mode and mode == "Wording + URL":

    if excel_path and html_files:
        wording_sheet_choices = []
        try:
            wording_sheet_choices = list_wording_sheet_choices(excel_path)
        except EXCEL_READ_ERRORS as e:
            st.error(f"❌ Impossible de lire les onglets du fichier Excel : {e}")

        wording_sheet_names = [name for name, _ in wording_sheet_choices]
        wording_sheet_labels = dict(wording_sheet_choices)

        url_sequence_choices = []
        url_tab_ok = False
        try:
            url_sequence_choices = list_url_sequence_choices(excel_path)
            url_tab_ok = True
        except ValueError as e:
            st.error(f"❌ {e}")

        url_sequence_names = [name for name, _ in url_sequence_choices]
        url_sequence_labels = dict(url_sequence_choices)

        if wording_sheet_names and url_tab_ok:
            st.write("**Associe chaque fichier HTML à sa séquence Wording et à sa séquence URL :**")

            if not url_sequence_names:
                st.caption(
                    "ℹ️ L'onglet UTM ne regroupe qu'une seule séquence — la séquence Wording "
                    "est réutilisée automatiquement côté URL, aucun choix supplémentaire n'est nécessaire."
                )

            for f in html_files:
                col_a, col_b = st.columns(2)
                with col_a:
                    w_choice = st.selectbox(
                        f"Onglet Wording pour **{f.name}**",
                        [NO_SELECTION] + wording_sheet_names,
                        format_func=lambda x: wording_sheet_labels.get(x, x),
                        key=f"wseq_{f.name}"
                    )
                    if w_choice == NO_SELECTION:
                        w_guess = _guess_sequence_choice(f.name, wording_sheet_names)
                        if w_guess:
                            st.caption(f"💡 Suggestion d'après le nom du fichier : {wording_sheet_labels.get(w_guess, w_guess)}")
                with col_b:
                    if url_sequence_names:
                        u_choice = st.selectbox(
                            f"Séquence URL pour **{f.name}**",
                            [NO_SELECTION] + url_sequence_names,
                            format_func=lambda x: url_sequence_labels.get(x, x),
                            key=f"useq_{f.name}"
                        )
                        if u_choice == NO_SELECTION:
                            u_guess = _guess_sequence_choice(f.name, url_sequence_names)
                            if u_guess:
                                st.caption(f"💡 Suggestion d'après le nom du fichier : {url_sequence_labels.get(u_guess, u_guess)}")
                    else:
                        u_choice = w_choice  # onglet UTM a sequence unique : reutilise la sequence wording

                if w_choice != NO_SELECTION and u_choice != NO_SELECTION:
                    combined_sequence_assignments.append((f, w_choice, u_choice))

            unassigned = len(html_files) - len(combined_sequence_assignments)
            if unassigned:
                st.warning(
                    f"⚠️ {unassigned} fichier(s) incomplet(s) (séquence Wording et/ou URL manquante) : "
                    "ils ne seront pas comparés."
                )

elif batch_mode:

    if excel_path and html_files:
        sequence_options = []
        sequence_labels = {}
        try:
            if mode == "Wording":
                sequence_choices = list_wording_sheet_choices(excel_path)
                sequence_options = [name for name, _ in sequence_choices]
                sequence_labels = dict(sequence_choices)
            else:
                sequence_choices = list_url_sequence_choices(excel_path)
                sequence_options = [name for name, _ in sequence_choices]
                sequence_labels = dict(sequence_choices)
                if not sequence_options:
                    st.warning(
                        "⚠️ Aucune séquence détectée dans l'onglet UTM — le mode multi-séquences "
                        "nécessite un onglet UTM qui regroupe plusieurs séquences."
                    )
        except ValueError as e:
            st.error(f"❌ {e}")

        if sequence_options:
            st.write("**Associe chaque fichier HTML à sa séquence :**")
            for f in html_files:
                choice = st.selectbox(
                    f"Séquence pour **{f.name}**",
                    [NO_SELECTION] + sequence_options,
                    format_func=lambda x: sequence_labels.get(x, x),
                    key=f"seq_choice_{f.name}"
                )
                if choice == NO_SELECTION:
                    guess = _guess_sequence_choice(f.name, sequence_options)
                    if guess:
                        st.caption(f"💡 Suggestion d'après le nom du fichier : {sequence_labels.get(guess, guess)}")
                else:
                    sequence_assignments.append((f, choice))

            unassigned = len(html_files) - len(sequence_assignments)
            if unassigned:
                st.warning(
                    f"⚠️ {unassigned} fichier(s) sans séquence sélectionnée : ils ne seront pas comparés."
                )

else:
    wording_keyword = ""
    url_keyword = ""
    url_ready = not test_url  # pas de test URL: rien a valider de ce cote

    if test_wording:
        sheet_choices = []
        if excel_path:
            try:
                sheet_choices = list_wording_sheet_choices(excel_path)
            except EXCEL_READ_ERRORS as e:
                st.error(f"❌ Impossible de lire les onglets du fichier Excel : {e}")

        if sheet_choices:
            sheet_names = [name for name, _ in sheet_choices]
            sheet_labels = dict(sheet_choices)
            wording_keyword = st.selectbox(
                "Onglet Excel (campagne)",
                [NO_SELECTION] + sheet_names,
                format_func=lambda x: sheet_labels.get(x, x),
                help="L'onglet du classeur Excel qui contient le wording de la séquence à tester."
            )
            if wording_keyword == NO_SELECTION:
                wording_keyword = ""
        else:
            wording_keyword = st.text_input("Onglet Excel (campagne)", placeholder="Ex : SEQ 1 NON LOYAL")
            st.caption("ℹ️ Ajoutez d'abord le fichier Excel ci-dessus pour voir la liste des onglets disponibles.")

    if test_url:
        sequence_options = []
        sequence_labels = {}
        excel_read_ok = False
        if excel_path:
            try:
                sequence_choices = list_url_sequence_choices(excel_path)
                sequence_options = [name for name, _ in sequence_choices]
                sequence_labels = dict(sequence_choices)
                excel_read_ok = True
            except ValueError as e:
                st.error(f"❌ {e}")

        if sequence_options:
            # L'onglet UTM regroupe plusieurs sequences empilees : il faut
            # choisir laquelle tester.
            url_keyword = st.selectbox(
                "Séquence (onglet UTM)",
                [NO_SELECTION] + sequence_options,
                format_func=lambda x: sequence_labels.get(x, x),
                help="La section de l'onglet UTM correspondant à la séquence à tester."
            )
            if url_keyword == NO_SELECTION:
                url_keyword = ""
            url_ready = bool(url_keyword)
            st.caption("ℹ️ L'onglet UTM lui-même est retrouvé automatiquement (nom contenant \"UTM\").")
        elif excel_read_ok:
            # Onglet UTM trouve automatiquement, mais sans decoupage en
            # sequences (une seule sequence implicite pour tout l'onglet) :
            # rien a selectionner, keyword vide = tout l'onglet est utilise.
            url_keyword = ""
            url_ready = True
            st.caption(
                "ℹ️ L'onglet UTM est retrouvé automatiquement (nom contenant \"UTM\") et ne regroupe "
                "qu'une seule séquence — aucune sélection n'est nécessaire ici."
            )
        else:
            url_keyword = st.text_input("Séquence (onglet UTM)", placeholder="Ex : SEQ 1 NON FID")
            url_ready = False
            st.caption(
                "ℹ️ Ajoutez d'abord le fichier Excel ci-dessus : l'onglet UTM est retrouvé automatiquement "
                "(nom contenant \"UTM\"), et la liste des séquences qu'il contient apparaîtra ici s'il en regroupe plusieurs."
            )

# En mode multi-sequences (test_wording et test_url ne peuvent pas etre tous
# les deux vrais dans ce cas, voir plus haut), qa_mode reste le mode unique
# teste pour toutes les sequences.
qa_mode = "wording" if mode == "Wording" else "url"

# =========================
# DIAGNOSTIC EXCEL (avant de lancer la QA, hors mode multi-séquences)
# =========================
missing_keyword = (test_wording and not wording_keyword) or (test_url and not url_ready)

if excel_path and not batch_mode and missing_keyword:
    st.info("👆 Sélectionnez une campagne/séquence ci-dessus pour afficher le diagnostic.")

if excel_path and not batch_mode and not missing_keyword:
    diagnostic_messages = _run_excel_diagnostics_cached(
        excel_path, html_path, test_wording, test_url, wording_keyword, url_keyword, include_generic
    )
    # Replie par defaut, sauf si le diagnostic a quelque chose a signaler
    # (warning/error) : la plupart des campagnes n'ont rien a corriger, pas
    # la peine d'occuper l'espace avec un panneau toujours ouvert pour ca.
    has_diagnostic_issues = any(level in ("warning", "error") for level, _ in diagnostic_messages)
    diagnostic_label = "🔍 Diagnostic du fichier Excel"
    if has_diagnostic_issues:
        diagnostic_label += " — ⚠️ point(s) à vérifier"

    with st.expander(diagnostic_label, expanded=has_diagnostic_issues):
        for level, text in diagnostic_messages:
            _DIAGNOSTIC_RENDERERS[level](text)

# =========================
# BOUTON COMPARER
# =========================
button_label = "🔍 Comparer toutes les séquences" if batch_mode else "🔍 Comparer"

if st.button(button_label):

    if batch_mode and mode == "Wording + URL":

        if not (excel_path and combined_sequence_assignments):
            st.error(
                "Merci d'uploader l'Excel et au moins un fichier HTML, puis d'associer chaque "
                "fichier à sa séquence Wording et à sa séquence URL."
            )
            st.stop()

        with st.spinner("Analyse en cours..."):
            last_run, error_messages, debug_logs = _execute_combined_batch(
                excel_path, combined_sequence_assignments, include_generic, session_id
            )

        for message in error_messages:
            st.error(message)

        if last_run is not None:
            st.session_state["last_run"] = last_run
        else:
            st.session_state.pop("last_run", None)
            with st.expander("🐛 Logs de debug (extraction HTML/Excel)"):
                st.code(debug_logs or "Aucun log.", language=None)

    elif batch_mode:

        if not (excel_path and sequence_assignments):
            st.error(
                "Merci d'uploader l'Excel et au moins un fichier HTML, "
                "puis d'associer chaque fichier à sa séquence."
            )
            st.stop()

        with st.spinner("Analyse en cours..."):
            last_run, error_messages, debug_logs = _execute_batch(
                excel_path, sequence_assignments, qa_mode, include_generic, session_id
            )

        for message in error_messages:
            st.error(message)

        if last_run is not None:
            st.session_state["last_run"] = last_run
        else:
            st.session_state.pop("last_run", None)
            with st.expander("🐛 Logs de debug (extraction HTML/Excel)"):
                st.code(debug_logs or "Aucun log.", language=None)

    elif html_file and excel_file:

        if (test_wording and not wording_keyword) or (test_url and not url_ready):
            st.error("Merci de sélectionner une campagne/séquence dans la liste déroulante.")
            st.stop()

        with st.spinner("Analyse en cours..."):
            last_run, error_message = _execute_single_or_combined_test(
                html_path, excel_path, test_wording, test_url, wording_keyword, url_keyword, include_generic
            )

        if last_run is not None:
            st.session_state["last_run"] = last_run
        else:
            st.session_state.pop("last_run", None)
            st.error(
                "❌ Impossible d'analyser ces fichiers. Vérifiez qu'il s'agit bien d'un "
                "export HTML valide (\"view-source\" AEM ou HTML brut) et d'un classeur "
                "Excel non corrompu, puis réessayez."
            )
            st.caption(f"Détail technique : {error_message}")

    else:
        st.session_state.pop("last_run", None)
        st.error("Merci d'uploader le HTML et l'Excel")

# =========================
# AFFICHAGE DU DERNIER RESULTAT (persiste entre reruns, ex: pendant que la
# pop-up d'informations manquantes est ouverte, pour que le resultat reste
# affiche sans devoir relancer toute la comparaison)
# =========================
last_run = st.session_state.get("last_run")

if last_run:

    if last_run["kind"] == "batch":
        st.subheader("Récapitulatif par séquence")
        st.dataframe(pd.DataFrame(last_run["recap_rows"]), use_container_width=True)
        render_grouped_results(
            last_run["sequences"],
            last_run["mode"],
            last_run["report_path"],
            last_run["download_filename"],
            last_run["title"]
        )

    elif last_run["kind"] == "combined_test":
        st.success(f"✅ {last_run['title']}")

        wording_total = len(last_run["wording_results"])
        wording_ok = sum(1 for r in last_run["wording_results"] if r["Status"] == "OK")
        url_total = len(last_run["url_results"])
        url_ok = sum(1 for r in last_run["url_results"] if r["Status"] == "OK")
        combined_total = wording_total + url_total
        combined_ok = wording_ok + url_ok
        combined_score = _score_pct(combined_ok, combined_total)

        st.metric("Score combiné (Wording + URL)", f"{combined_score}%")

        tab_wording, tab_url = st.tabs(["📝 Résultats Wording", "🔗 Résultats URL"])
        with tab_wording:
            render_results_block(
                last_run["wording_results"],
                last_run["wording_report_path"],
                "📝 Résultats Wording",
                last_run["wording_download_filename"],
                mode="wording"
            )
        with tab_url:
            render_results_block(
                last_run["url_results"],
                last_run["url_report_path"],
                "🔗 Résultats URL",
                last_run["url_download_filename"],
                mode="url"
            )

    elif last_run["kind"] == "combined_batch":
        st.subheader("Récapitulatif par séquence")
        st.dataframe(pd.DataFrame(last_run["recap_rows"]), use_container_width=True)

        wording_by_sequence = {
            pair["wording_sequence"]: pair["wording_rows"] for pair in last_run["pairs"] if pair["wording_rows"]
        }
        url_by_sequence = {
            pair["url_sequence"]: pair["url_rows"] for pair in last_run["pairs"] if pair["url_rows"]
        }

        if last_run["wording_report_path"] and last_run["url_report_path"]:
            tab_wording, tab_url = st.tabs(["📝 Résultats Wording", "🔗 Résultats URL"])
            with tab_wording:
                render_grouped_results(
                    wording_by_sequence,
                    "wording",
                    last_run["wording_report_path"],
                    last_run["wording_download_filename"],
                    "📝 Résultats Wording (toutes séquences)"
                )
            with tab_url:
                render_grouped_results(
                    url_by_sequence,
                    "url",
                    last_run["url_report_path"],
                    last_run["url_download_filename"],
                    "🔗 Résultats URL (toutes séquences)"
                )
        elif last_run["wording_report_path"]:
            render_grouped_results(
                wording_by_sequence,
                "wording",
                last_run["wording_report_path"],
                last_run["wording_download_filename"],
                "📝 Résultats Wording (toutes séquences)"
            )
        elif last_run["url_report_path"]:
            render_grouped_results(
                url_by_sequence,
                "url",
                last_run["url_report_path"],
                last_run["url_download_filename"],
                "🔗 Résultats URL (toutes séquences)"
            )

    else:
        render_results_block(
            last_run["results"],
            last_run["report_path"],
            last_run["title"],
            last_run["download_filename"],
            mode=last_run["mode"]
        )

    with st.expander("🐛 Logs de debug (extraction HTML/Excel)"):
        st.code(last_run.get("debug_logs") or "Aucun log.", language=None)

    ensure_archived(last_run)

    archived_state = st.session_state.get(_archive_key(last_run))
    if archived_state and archived_state.get("skipped"):
        st.caption("ℹ️ Ce rapport n'a pas été archivé dans l'historique.")
    elif archived_state:
        st.success(
            f"📁 Archivé automatiquement dans l'historique sous "
            f"**{archived_state['country']} / {archived_state['trigger']}**."
        )
        for r in archived_state.get("regressions") or []:
            st.warning(
                f"⚠️ Régression détectée sur **{r['sequence']}** ({r['mode']}) : "
                f"{r['previous_score']}% → {r['current_score']}% ({r['delta']:+.1f} pts)"
            )
