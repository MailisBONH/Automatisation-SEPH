import streamlit as st
import pandas as pd
import os
import io
import re
import uuid
import contextlib
from qa.archive import archive_report
from qa.classification import detect_classification, normalize_sequence_name, resolve_sequence_alias
from qa.cleanup import cleanup_old_temp_files_throttled
from qa.combined_report import build_combined_report
from qa.diagnostics import find_duplicate_components, find_unused_excel_classes, find_wrong_language_wordings
from qa.engine import run_qa, run_qa_for_sequence
from qa.excel_loader import list_url_sequences, load_excel_urls, load_excel_wordings, load_generic_wordings
from qa.history import record_run
from qa.html_extraction import load_and_extract_html_texts
from qa.report import save_consolidated_report, save_report

from datetime import datetime

import plotly.express as px
from st_aggrid import AgGrid
from st_aggrid import GridOptionsBuilder
from st_aggrid.shared import JsCode

import difflib


def highlight_differences(excel_text, html_text):

    excel_words = str(excel_text or "").split()
    html_words = str(html_text or "").split()

    matcher = difflib.SequenceMatcher(
        None,
        excel_words,
        html_words
    )

    excel_parts = []
    html_parts = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():

        ex = " ".join(excel_words[i1:i2])
        ht = " ".join(html_words[j1:j2])

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
        " ".join(excel_parts),
        " ".join(html_parts)
    )


def run_with_debug_capture(fn, *args, **kwargs):
    """Execute fn en capturant tout ce qu'elle imprime (stdout), pour l'afficher dans l'UI."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


URL_COLUMN_RENAME = {
    "Expected Class": "Classe attendue (Excel)",
    "HTML Class": "Classe trouvée (HTML)",
    "Excel Wording": "URL attendue (Excel)",
    "HTML Wording": "URL trouvée (HTML)",
    "Sequence": "Séquence",
}


def _prepare_display_df(results, mode):
    """df = donnees brutes (pour les metriques/graphiques) ; df_display =
    version mise en forme pour le tableau (colonnes URL renommees, diff
    mot-a-mot en gras rouge sur les KO en mode wording)."""
    df = pd.DataFrame(results)

    if mode == "url":
        display_cols = [
            c for c in ["Sequence", "Expected Class", "HTML Class", "Excel Wording", "HTML Wording", "Status"]
            if c in df.columns
        ]
        df_display = df[display_cols].rename(columns=URL_COLUMN_RENAME)
    else:
        df_display = df.copy()

        for idx, row in df_display.iterrows():

            if row["Status"] == "KO":

                excel_diff, html_diff = highlight_differences(
                    row.get("Excel Wording", ""),
                    row.get("HTML Wording", "")
                )

                df_display.at[idx, "Excel Wording"] = excel_diff
                df_display.at[idx, "HTML Wording"] = html_diff

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
        "QA Success Rate",
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

    if mode != "url":
        gb.configure_column(
            "Excel Wording",
            cellRenderer=html_renderer,
            width=500
        )

        gb.configure_column(
            "HTML Wording",
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
        seq_score = round(seq_ok / seq_total * 100, 1) if seq_total else 0
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
    l'historique (mode="combined")."""
    resolved_sequences = set()

    for item in _run_items(last_run):
        sequence = resolve_sequence_alias(country, trigger, normalize_sequence_name(item["sequence"]))
        resolved_sequences.add(sequence)

        rows = item["rows"]
        total = len(rows)
        ok = sum(1 for r in rows if r["Status"] == "OK")
        score = round(ok / total * 100, 2) if total else 0

        report_path = item["report_path"] or save_report(rows, item["sequence"], mode=item["mode"])
        archive_report(report_path, country, trigger, sequence, mode=item["mode"])
        record_run(
            item["sequence"], item["mode"], total, ok, total - ok, score,
            country=country, trigger=trigger, sequence=sequence
        )

    for sequence in resolved_sequences:
        combined_path, combined_stats = build_combined_report(country, trigger, sequence)
        if combined_stats:
            record_run(
                sequence, "combined",
                combined_stats["total"], combined_stats["ok"], combined_stats["ko"], combined_stats["score"],
                country=country, trigger=trigger, sequence=sequence
            )


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
                _do_archive(last_run, country, trigger)
                st.session_state[archive_key] = {"country": country, "trigger": trigger}
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
        _do_archive(last_run, country, trigger)
        st.session_state[archive_key] = {"country": country, "trigger": trigger}
    else:
        _prompt_missing_classification(last_run, guess, sequences)


NO_SELECTION = "— Sélectionnez —"

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

# =========================
# MODE (Wording / URL)
# =========================
mode = st.radio(
    "Type de test",
    ["Wording", "URL", "Wording + URL"],
    index=0,  # par défaut wording
    horizontal=True
)

test_wording = mode in ("Wording", "Wording + URL")
test_url = mode in ("URL", "Wording + URL")

batch_mode = st.checkbox(
    "📦 Traiter plusieurs séquences en une fois (même Excel, plusieurs HTML)"
)

include_generic = False
if test_wording:
    include_generic = st.checkbox(
        "🧩 Les composants génériques (footer, mentions légales, fidélité...) "
        "sont dans un onglet séparé de l'Excel"
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
        type=["xlsx"]
    )

with col5:
    if batch_mode:
        html_files = st.file_uploader(
            "📄 Drag & drop plusieurs fichiers HTML (un par séquence)",
            type=["html"],
            accept_multiple_files=True
        )
        html_file = None
    else:
        html_file = st.file_uploader(
            "📄 Drag & drop HTML",
            type=["html"]
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
        wording_sheet_options = []
        try:
            wording_sheet_options = pd.ExcelFile(excel_path).sheet_names
        except Exception as e:
            st.error(f"❌ Impossible de lire les onglets du fichier Excel : {e}")

        url_sequence_options = []
        url_tab_ok = False
        try:
            url_sequence_options = list_url_sequences(excel_path)
            url_tab_ok = True
        except ValueError as e:
            st.error(f"❌ {e}")

        if wording_sheet_options and url_tab_ok:
            st.write("**Associe chaque fichier HTML à sa séquence Wording et à sa séquence URL :**")

            if not url_sequence_options:
                st.caption(
                    "ℹ️ L'onglet UTM ne regroupe qu'une seule séquence — la séquence Wording "
                    "est réutilisée automatiquement côté URL, aucun choix supplémentaire n'est nécessaire."
                )

            for f in html_files:
                col_a, col_b = st.columns(2)
                with col_a:
                    w_choice = st.selectbox(
                        f"Onglet Wording pour **{f.name}**",
                        [NO_SELECTION] + wording_sheet_options,
                        key=f"wseq_{f.name}"
                    )
                with col_b:
                    if url_sequence_options:
                        u_choice = st.selectbox(
                            f"Séquence URL pour **{f.name}**",
                            [NO_SELECTION] + url_sequence_options,
                            key=f"useq_{f.name}"
                        )
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
        try:
            if mode == "Wording":
                sequence_options = pd.ExcelFile(excel_path).sheet_names
            else:
                sequence_options = list_url_sequences(excel_path)
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
                    key=f"seq_choice_{f.name}"
                )
                if choice != NO_SELECTION:
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
        sheet_options = []
        if excel_path:
            try:
                sheet_options = pd.ExcelFile(excel_path).sheet_names
            except Exception as e:
                st.error(f"❌ Impossible de lire les onglets du fichier Excel : {e}")

        if sheet_options:
            wording_keyword = st.selectbox("Onglet Excel (campagne)", [NO_SELECTION] + sheet_options)
            if wording_keyword == NO_SELECTION:
                wording_keyword = ""
        else:
            wording_keyword = st.text_input("Onglet Excel (campagne)", placeholder="Ex : SEQ 1 NON LOYAL")
            st.caption("ℹ️ Ajoutez d'abord le fichier Excel ci-dessus pour voir la liste des onglets disponibles.")

    if test_url:
        sequence_options = []
        excel_read_ok = False
        if excel_path:
            try:
                sequence_options = list_url_sequences(excel_path)
                excel_read_ok = True
            except ValueError as e:
                st.error(f"❌ {e}")

        if sequence_options:
            # L'onglet UTM regroupe plusieurs sequences empilees : il faut
            # choisir laquelle tester.
            url_keyword = st.selectbox("Séquence (onglet UTM)", [NO_SELECTION] + sequence_options)
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
    with st.expander("🔍 Diagnostic du fichier Excel", expanded=True):
        try:
            xls_preview = pd.ExcelFile(excel_path)
            st.caption(f"Onglets détectés : {', '.join(xls_preview.sheet_names)}")
        except Exception as e:
            st.error(f"Impossible de lire le fichier Excel : {e}")
            xls_preview = None

        if xls_preview is not None:
            if test_wording:
                try:
                    diag_sheet, diag_wordings = load_excel_wordings(excel_path, wording_keyword)
                    st.success(f"✅ Onglet trouvé : **{diag_sheet}** — {len(diag_wordings)} wordings chargés")

                    n_with_class = sum(1 for w in diag_wordings if w.get("Technical"))
                    fill_rate = round(n_with_class / len(diag_wordings) * 100, 1) if diag_wordings else 0

                    if fill_rate == 100:
                        st.success("Classes techniques (TECHNICAL NAME) renseignées sur 100% des lignes.")
                    elif fill_rate == 0:
                        st.warning(
                            "⚠️ Colonne 'TECHNICAL NAME' absente ou vide sur tout l'onglet — "
                            "le matching par classe ne fonctionnera pas, tout reposera sur la comparaison de texte seule."
                        )
                    else:
                        st.warning(
                            f"⚠️ Classes techniques renseignées sur seulement {fill_rate}% des lignes — "
                            "le matching sera moins précis pour les lignes sans classe."
                        )

                    combined_wordings = list(diag_wordings)

                    if include_generic:
                        try:
                            diag_generic_sheet, diag_generic_wordings = load_generic_wordings(excel_path)
                            st.success(
                                f"✅ Onglet générique trouvé : **{diag_generic_sheet}** — "
                                f"{len(diag_generic_wordings)} wording(s) génériques seront ajoutés"
                            )
                            combined_wordings += diag_generic_wordings
                        except ValueError as e:
                            st.error(f"❌ Composants génériques : {e}")

                    duplicates = find_duplicate_components(combined_wordings)
                    if duplicates:
                        details = "; ".join(f"**{comp}** ({', '.join(classes)})" for comp, classes in duplicates)
                        st.warning(
                            f"⚠️ Composant(s) présents avec plusieurs classes techniques différentes — "
                            f"souvent un bloc dupliqué par erreur (copier-coller) : {details}"
                        )

                    language_mismatches = find_wrong_language_wordings(combined_wordings)
                    if language_mismatches:
                        preview = "; ".join(
                            f"**{m['Component']}** ({m['detected_lang']}) : \"{str(m['Wording'])[:40]}...\""
                            for m in language_mismatches[:5]
                        )
                        more = f" (+{len(language_mismatches) - 5} autre(s))" if len(language_mismatches) > 5 else ""
                        st.warning(
                            f"⚠️ {len(language_mismatches)} wording(s) semblent dans une langue différente "
                            f"de la langue dominante (`{language_mismatches[0]['expected_lang']}`) — "
                            f"probablement collés depuis une autre campagne/marché : {preview}{more}"
                        )

                    if html_path:
                        try:
                            diag_html_texts = load_and_extract_html_texts(html_path)
                            unused_classes = find_unused_excel_classes(diag_html_texts, combined_wordings)
                            if unused_classes:
                                st.warning(
                                    "⚠️ Classe(s) technique(s) déclarée(s) dans l'Excel mais introuvable(s) dans le HTML "
                                    "(le nom a probablement divergé du vrai template) : "
                                    + ", ".join(f"`{c}`" for c in unused_classes)
                                )
                        except Exception:
                            pass  # le HTML sera re-analyse (avec message d'erreur clair) au clic sur Comparer

                except ValueError as e:
                    st.error(f"❌ {e}")

            if test_url:
                try:
                    diag_sheet, diag_urls = load_excel_urls(excel_path, url_keyword)
                    st.success(
                        f"✅ Onglet UTM trouvé : **{diag_sheet}** — "
                        f"{len(diag_urls)} URL(s) de référence pour cette séquence"
                    )
                except ValueError as e:
                    st.error(f"❌ {e}")

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

        st.info("Analyse en cours... 🔄")

        wording_results_by_sequence = {}
        url_results_by_sequence = {}
        debug_buf = io.StringIO()

        with contextlib.redirect_stdout(debug_buf):
            for f, w_seq, u_seq in combined_sequence_assignments:

                batch_html_path = f"temp/{session_id}_{uuid.uuid4().hex[:6]}.html"
                with open(batch_html_path, "wb") as out:
                    out.write(f.getvalue())

                print(f"\n{'=' * 20} {w_seq} / {u_seq} ({f.name}) {'=' * 20}")

                try:
                    w_results, _ = run_qa_for_sequence(
                        html_file=batch_html_path,
                        excel_file=excel_path,
                        keyword=w_seq,
                        mode="wording",
                        include_generic=include_generic
                    )
                    wording_results_by_sequence[w_seq] = w_results
                except ValueError as e:
                    print(f"❌ Wording {w_seq} ({f.name}) : {e}")
                    st.error(f"❌ Wording {w_seq} ({f.name}) : {e}")

                try:
                    u_results, _ = run_qa_for_sequence(
                        html_file=batch_html_path,
                        excel_file=excel_path,
                        keyword=u_seq,
                        mode="url"
                    )
                    url_results_by_sequence[u_seq] = u_results
                except ValueError as e:
                    print(f"❌ URL {u_seq} ({f.name}) : {e}")
                    st.error(f"❌ URL {u_seq} ({f.name}) : {e}")

        if wording_results_by_sequence or url_results_by_sequence:

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
                    "Score Wording": round(w_ok / w_total * 100, 1) if w_total else 0,
                    "Séquence URL": u_seq,
                    "Score URL": round(u_ok / u_total * 100, 1) if u_total else 0,
                })

            st.session_state["last_run"] = {
                "kind": "combined_batch",
                "excel_path": excel_path,
                "title": f"Comparaison terminée pour {len(pairs)} séquence(s) (Wording + URL)",
                "pairs": pairs,
                "recap_rows": recap_rows,
                "wording_report_path": wording_report_path,
                "wording_download_filename": f"qa_report_multi_wording_{today}.xlsx",
                "url_report_path": url_report_path,
                "url_download_filename": f"qa_report_multi_url_{today}.xlsx",
                "debug_logs": debug_buf.getvalue(),
            }

        else:
            st.session_state.pop("last_run", None)
            with st.expander("🐛 Logs de debug (extraction HTML/Excel)"):
                st.code(debug_buf.getvalue() or "Aucun log.", language=None)

    elif batch_mode:

        if not (excel_path and sequence_assignments):
            st.error(
                "Merci d'uploader l'Excel et au moins un fichier HTML, "
                "puis d'associer chaque fichier à sa séquence."
            )
            st.stop()

        st.info("Analyse en cours... 🔄")

        results_by_sequence = {}
        debug_buf = io.StringIO()

        with contextlib.redirect_stdout(debug_buf):
            for f, seq_name in sequence_assignments:

                batch_html_path = f"temp/{session_id}_{uuid.uuid4().hex[:6]}.html"
                with open(batch_html_path, "wb") as out:
                    out.write(f.getvalue())

                print(f"\n{'=' * 20} {seq_name} ({f.name}) {'=' * 20}")

                try:
                    seq_results, resolved_sheet = run_qa_for_sequence(
                        html_file=batch_html_path,
                        excel_file=excel_path,
                        keyword=seq_name,
                        mode=qa_mode,
                        include_generic=include_generic
                    )
                    results_by_sequence[seq_name] = seq_results
                except ValueError as e:
                    print(f"❌ {seq_name} ({f.name}) : {e}")
                    st.error(f"❌ {seq_name} ({f.name}) : {e}")

        if results_by_sequence:

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
                    "Score": round(seq_ok / seq_total * 100, 1) if seq_total else 0
                })

            st.session_state["last_run"] = {
                "kind": "batch",
                "mode": qa_mode,
                "excel_path": excel_path,
                "title": f"Comparaison terminée pour {len(results_by_sequence)} séquence(s)",
                "download_filename": f"qa_report_multi_{today}.xlsx",
                "report_path": report_path,
                "sheet_name": None,
                "sequences": results_by_sequence,
                "recap_rows": recap_rows,
                "debug_logs": debug_buf.getvalue(),
            }

        else:
            st.session_state.pop("last_run", None)
            with st.expander("🐛 Logs de debug (extraction HTML/Excel)"):
                st.code(debug_buf.getvalue() or "Aucun log.", language=None)

    elif html_file and excel_file:

        if (test_wording and not wording_keyword) or (test_url and not url_ready):
            st.error("Merci de sélectionner une campagne/séquence dans la liste déroulante.")
            st.stop()

        st.info("Analyse en cours... 🔄")

        # appel backend
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

                st.session_state["last_run"] = {
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

                st.session_state["last_run"] = {
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

        except ValueError as e:
            st.session_state.pop("last_run", None)
            st.error(str(e))

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
        combined_score = round(combined_ok / combined_total * 100, 1) if combined_total else 0

        st.metric("Score combiné (Wording + URL)", f"{combined_score}%")

        render_results_block(
            last_run["wording_results"],
            last_run["wording_report_path"],
            "📝 Résultats Wording",
            last_run["wording_download_filename"],
            mode="wording"
        )

        st.divider()

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

        if last_run["wording_report_path"]:
            render_grouped_results(
                wording_by_sequence,
                "wording",
                last_run["wording_report_path"],
                last_run["wording_download_filename"],
                "📝 Résultats Wording (toutes séquences)"
            )

        if last_run["wording_report_path"] and last_run["url_report_path"]:
            st.divider()

        if last_run["url_report_path"]:
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
