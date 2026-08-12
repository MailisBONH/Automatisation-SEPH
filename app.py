import streamlit as st
import pandas as pd
import os
import io
import re
import uuid
import contextlib
from qa.diagnostics import find_duplicate_components, find_unused_excel_classes, find_wrong_language_wordings
from qa.engine import run_qa, run_qa_for_sequence
from qa.excel_loader import list_url_sequences, load_excel_urls, load_excel_wordings, load_generic_wordings
from qa.history import load_history
from qa.html_extraction import load_and_extract_html_texts
from qa.report import save_consolidated_report

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


def render_results_block(results, report_path, title, download_filename, sheet_name=None):
    """Affiche metriques, graphiques, tableau detaille et bouton de telechargement pour un jeu de resultats."""

    st.success(f"✅ {title}")

    df = pd.DataFrame(results)

    df_display = df.copy()

    for idx, row in df_display.iterrows():

        if row["Status"] == "KO":

            excel_diff, html_diff = highlight_differences(
                row.get("Excel Wording", ""),
                row.get("HTML Wording", "")
            )

            df_display.at[idx, "Excel Wording"] = excel_diff
            df_display.at[idx, "HTML Wording"] = html_diff

    ok_count = len(df[df["Status"] == "OK"])
    ko_count = len(df[df["Status"] == "KO"])

    total = ok_count + ko_count

    success_rate = round(
        ok_count / total * 100,
        1
    ) if total else 0

    st.metric(
        "QA Success Rate",
        f"{success_rate}%"
    )

    if sheet_name:
        history = load_history(sheet=sheet_name)
        if len(history) > 1:
            with st.expander(f"📈 Historique — {sheet_name}", expanded=False):
                history_df = pd.DataFrame(history)
                fig_history = px.line(
                    history_df,
                    x="timestamp",
                    y="score",
                    markers=True,
                    title="Évolution du score",
                    labels={"timestamp": "Date", "score": "Score (%)"}
                )
                fig_history.update_yaxes(range=[0, 100])
                st.plotly_chart(fig_history, use_container_width=True)
                st.dataframe(
                    history_df[["timestamp", "total", "ok", "ko", "score"]].iloc[::-1],
                    use_container_width=True,
                    hide_index=True
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

    ko_df = df[df["Status"] == "KO"]

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
        theme="streamlit"
    )

    with open(report_path, "rb") as f:
        st.download_button(
            "⬇️ Télécharger le rapport Excel",
            data=f,
            file_name=download_filename,
            key=f"download_{download_filename}"
        )


st.set_page_config(page_title="QA Email Tool", layout="wide")

st.title("📧 Email QA Tool")

# Un identifiant unique par session de navigateur, pour que les fichiers
# temporaires de deux utilisateurs (ou deux onglets) ne s'ecrasent pas.
if "session_id" not in st.session_state:
    st.session_state["session_id"] = uuid.uuid4().hex[:8]
session_id = st.session_state["session_id"]

# =========================
# MODE (Wording / URL)
# =========================
mode = st.radio(
    "Type de test",
    ["Wording", "URL"],
    index=0,  # par défaut wording
    horizontal=True
)

batch_mode = st.checkbox(
    "📦 Traiter plusieurs séquences en une fois (même Excel, plusieurs HTML)"
)

include_generic = False
if mode == "Wording":
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
col1, col2, col3 = st.columns(3)

with col1:
    aem_url = "" if batch_mode else st.text_input("Lien AEM")

with col2:
    username = "" if batch_mode else st.text_input("Identifiant AEM")

with col3:
    password = "" if batch_mode else st.text_input("Mot de passe AEM", type="password")

if batch_mode:
    st.caption(
        "ℹ️ Le mode multi-séquences ne supporte que l'upload de fichiers HTML "
        "(pas la récupération AEM par URL)."
    )

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
            "📄 Drag & drop HTML (optionnel)",
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

if batch_mode:
    keyword = ""

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
                    sequence_options,
                    key=f"seq_choice_{f.name}"
                )
                sequence_assignments.append((f, choice))

else:
    if mode == "Wording":
        sheet_options = []
        if excel_path:
            try:
                sheet_options = pd.ExcelFile(excel_path).sheet_names
            except Exception as e:
                st.error(f"❌ Impossible de lire les onglets du fichier Excel : {e}")

        if sheet_options:
            keyword = st.selectbox("Onglet Excel (campagne)", sheet_options)
        else:
            keyword = st.text_input("Campaign keyword", "HugPlan S1 Bronze")

    else:
        sequence_options = []
        if excel_path:
            try:
                sequence_options = list_url_sequences(excel_path)
            except ValueError as e:
                st.error(f"❌ {e}")

        if sequence_options:
            keyword = st.selectbox("Séquence (onglet UTM)", sequence_options)
            st.caption("ℹ️ L'onglet UTM lui-même est retrouvé automatiquement (nom contenant \"UTM\").")
        else:
            keyword = st.text_input("Campaign keyword", "HugPlan S1 Bronze")
            st.caption(
                "ℹ️ En mode URL, l'onglet Excel est retrouvé automatiquement (nom contenant \"UTM\"). "
                "Ce mot-clé sert à sélectionner la bonne séquence à l'intérieur de cet onglet, s'il en contient plusieurs "
                "(le menu déroulant apparaîtra une fois l'Excel uploadé, si l'onglet en regroupe plusieurs)."
            )

qa_mode = "wording" if mode == "Wording" else "url"

# =========================
# DIAGNOSTIC EXCEL (avant de lancer la QA, hors mode multi-séquences)
# =========================
if excel_path and not batch_mode:
    with st.expander("🔍 Diagnostic du fichier Excel", expanded=True):
        try:
            xls_preview = pd.ExcelFile(excel_path)
            st.caption(f"Onglets détectés : {', '.join(xls_preview.sheet_names)}")
        except Exception as e:
            st.error(f"Impossible de lire le fichier Excel : {e}")
            xls_preview = None

        if xls_preview is not None:
            if qa_mode == "wording":
                try:
                    diag_sheet, diag_wordings = load_excel_wordings(excel_path, keyword)
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

            else:
                try:
                    diag_sheet, diag_urls = load_excel_urls(excel_path, keyword)
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

    if batch_mode:

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
            all_rows = []

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

                for r in rows:
                    row = dict(r)
                    row["Sequence"] = seq_name
                    all_rows.append(row)

            st.subheader("Récapitulatif par séquence")
            st.dataframe(pd.DataFrame(recap_rows), use_container_width=True)

            render_results_block(
                all_rows,
                report_path,
                f"Comparaison terminée pour {len(results_by_sequence)} séquence(s)",
                f"qa_report_multi_{today}.xlsx"
            )

        with st.expander("🐛 Logs de debug (extraction HTML/Excel)"):
            st.code(debug_buf.getvalue() or "Aucun log.", language=None)

    elif (html_file and excel_file) or (aem_url and excel_file):

        if aem_url and (not username or not password):
            st.error("Merci de renseigner les identifiants AEM")
            st.stop()

        st.info("Analyse en cours... 🔄")

        # appel backend
        try:
            (results, report_path, sheet_name), debug_logs = run_with_debug_capture(
                run_qa,
                html_file=html_path if not aem_url else None,
                excel_file=excel_path,
                keyword=keyword,
                aem_url=aem_url if aem_url else None,
                username=username if aem_url else None,
                password=password if aem_url else None,
                mode=qa_mode,
                aem_temp_path=f"temp/{session_id}_aem_input.html",
                include_generic=include_generic
            )

            today = datetime.now().strftime("%Y-%m-%d")

            safe_sheet_name = re.sub(r"[^\w\-]", "_", sheet_name)

            render_results_block(
                results,
                report_path,
                "Comparaison terminée",
                f"qa_report_{safe_sheet_name}_{today}.xlsx",
                sheet_name=sheet_name
            )

            with st.expander("🐛 Logs de debug (extraction HTML/Excel)"):
                st.code(debug_logs or "Aucun log.", language=None)

        except ValueError as e:
            st.error(str(e))

    else:
        st.error("Merci d'uploader le HTML et l'Excel")
