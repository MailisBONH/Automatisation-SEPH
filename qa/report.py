import os
import re
import difflib
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.cell.rich_text import CellRichText
from openpyxl.cell.text import InlineFont
from openpyxl.cell.rich_text import TextBlock

from qa.archive import prune_root_reports
from qa.config import CRITICALITY_ORDER, OUTPUT_DIR, SCORE_COLOR_GOOD, SCORE_COLOR_WARN
from qa.history import record_run


def score_fill_color(score: float) -> str:
    """Couleur de remplissage (hex ARGB sans '#') pour une cellule de score
    (%) dans un rapport Excel : vert a SCORE_COLOR_GOOD (100%), jaune
    au-dessus de SCORE_COLOR_WARN, rouge en-dessous - seuils centralises
    dans qa.config pour que report.py et combined_report.py ne divergent
    jamais l'un de l'autre."""
    if score == SCORE_COLOR_GOOD:
        return "C6EFCE"
    if score >= SCORE_COLOR_WARN:
        return "FFEB9C"
    return "FFC7CE"


def status_fill_color(status: str) -> str:
    """Couleur de remplissage pour une cellule 'Status' (OK vert / KO
    rouge) - partagee par tous les rapports Excel (report.py,
    combined_report.py)."""
    return "C6EFCE" if status == "OK" else "FFC7CE"


def write_summary_sheet(ws, title: str, meta: list, score: float) -> None:
    """
    Ecrit l'onglet "Summary" commun aux 3 rapports (sequence, consolide
    multi-sequences, combine wording+url) : titre en gras en A1, puis une
    ligne (libelle en gras, valeur) par entree de meta a partir de la ligne
    3. La cellule de score (toujours la DERNIERE entree de meta - "Score"/
    "Score global"/"Score combiné" selon le rapport) est colorée via
    score_fill_color.
    """
    ws.title = "Summary"
    ws["A1"] = title
    ws["A1"].font = Font(size=14, bold=True)
    for i, (k, v) in enumerate(meta, start=3):
        ws[f"A{i}"] = k
        ws[f"A{i}"].font = Font(bold=True)
        ws[f"B{i}"] = v
    score_row = 3 + len(meta) - 1
    ws[f"B{score_row}"].fill = PatternFill(start_color=score_fill_color(score), fill_type="solid")


def _sort_by_criticality(df: pd.DataFrame, prefix_columns: list = None) -> pd.DataFrame:
    """Trie un DataFrame de KO par vraie gravite (CRITICALITY_ORDER), apres
    d'eventuelles colonnes de regroupement (ex: "Sequence"), puis par ordre
    d'apparition dans le HTML (_html_order, voir qa.wording_qa) si
    disponible - Component Name ne sert plus que de tie-break final. Un tri
    par Component Name alphabetique en position dominante placerait par
    exemple "LEGAL NOTICE" avant "LOYALTY SECTION" (E < O) meme quand la
    section loyalty apparait bien avant les mentions legales sur la page.
    Remplace aussi un tri alphabetique sur Criticality, qui donnerait
    "High" < "Low" < "Medium"."""
    crit = pd.Categorical(df["Criticality"], categories=CRITICALITY_ORDER, ordered=True)
    sort_cols = (prefix_columns or []) + ["_criticality_order"]
    if "_html_order" in df.columns:
        sort_cols.append("_html_order")
    sort_cols.append("Component Name")
    return (
        df.assign(_criticality_order=crit)
          .sort_values(sort_cols)
          .drop(columns="_criticality_order")
    )


def sort_results_by_html_order(df: pd.DataFrame, prefix_columns: list = None) -> pd.DataFrame:
    """Trie un DataFrame (KO et OK confondus) par ordre d'apparition dans le
    HTML (_html_order, voir qa.wording_qa) si disponible, Component Name ne
    servant que de tie-break final. Utilisee pour l'onglet Details :
    contrairement a l'onglet KO Only (qui priorise la criticite pour le
    triage, voir _sort_by_criticality), Details doit suivre le fil de la
    page. Trier KO et OK separement (tous les KO d'abord, puis tous les OK)
    eclaterait un meme composant en 2 endroits du rapport des qu'il a au
    moins un KO et un OK ailleurs sur la page."""
    sort_cols = list(prefix_columns or [])
    if "_html_order" in df.columns:
        sort_cols.append("_html_order")
    sort_cols.append("Component Name")
    return df.sort_values(sort_cols)


# =========================
# RAPPORT EXCEL
# =========================
def build_rich_diff(text1, text2, char_level=False):
    """
    Diff en gras rouge (openpyxl CellRichText, pour une cellule de rapport
    Excel) entre 2 textes. Par mots par defaut (wording) ; char_level=True
    decoupe caractere par caractere (mode URL, ou une URL n'a pas
    d'espaces - un decoupage par mots surlignerait l'URL entiere en rouge
    pour la moindre difference). Meme principe que
    home.highlight_differences (equivalent cote apercu Streamlit),
    factorise ici en une seule fonction plutot que deux copies quasi
    identiques (build_rich_diff/build_rich_diff_char).
    """
    text1 = str(text1 or "")
    text2 = str(text2 or "")

    if char_level:
        tokens1 = list(text1)
        tokens2 = list(text2)
        sep = ""
    else:
        tokens1 = text1.split()
        tokens2 = text2.split()
        sep = " "

    matcher = difflib.SequenceMatcher(
        None,
        tokens1,
        tokens2
    )

    rich1 = CellRichText()
    rich2 = CellRichText()

    red_bold = InlineFont(
        color="FF0000",
        b=True
    )

    normal = InlineFont()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():

        part1 = sep.join(tokens1[i1:i2])
        part2 = sep.join(tokens2[j1:j2])
        font = normal if tag == "equal" else red_bold

        if part1:
            rich1.append(
                TextBlock(font, part1 + sep)
            )

        if part2:
            rich2.append(
                TextBlock(font, part2 + sep)
            )

    return rich1, rich2


BASE_COLUMNS = [
    "Component Name", "Element", "Source Sheet", "Expected Class", "HTML Class",
    "Excel Wording", "HTML Wording",
    "Status", "Error Type", "Similarity", "Criticality",
    "Match Type"
]

# Rapport dedie aux URLs : pas de wording/composant, seulement la section
# source (FID/NON FID), la classe + URL attendues (Excel) vs trouvees
# (HTML), le statut, la raison (KO, ou une note informative sur un OK -
# voir INFO_URL_QUERY_IGNORED), la similarite caractere par caractere et le
# nombre d'occurrences trouvees dans le HTML.
URL_COLUMNS = [
    "Source Section", "Expected Class", "HTML Class", "Excel Wording", "HTML Wording",
    "Status", "Error Type", "Similarity", "Occurrences"
]

URL_HEADER_LABELS = {
    "Source Section": "Section (FID/NON FID)",
    "Expected Class": "Classe attendue (Excel)",
    "HTML Class": "Classe trouvée (HTML)",
    "Excel Wording": "URL attendue (Excel)",
    "HTML Wording": "URL trouvée (HTML)",
    "Status": "Status",
    "Error Type": "Raison",
    "Similarity": "Similarité (%)",
    "Occurrences": "Occurrences (HTML)",
    "Sequence": "Séquence",
}


def _write_results_sheet(ws_target, df_data, header_color, columns, header_labels=None, char_diff=False):

    status_idx = columns.index("Status")
    excel_idx = columns.index("Excel Wording")
    html_idx = columns.index("HTML Wording")

    for c, col in enumerate(columns, 1):
        label = (header_labels or {}).get(col, col)
        cell = ws_target.cell(1, c, label)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color=header_color, fill_type="solid")
    crit_colors = {"High": "FF0000", "Medium": "FFA500", "Low": "FFD966"}
    for r, row in enumerate(df_data[columns].itertuples(index=False), start=2):
        for c, val in enumerate(row, start=1):
            safe_val = "" if (isinstance(val, float) and val != val) else val
            cell = ws_target.cell(r, c)
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=True
            )
            col_name = columns[c - 1]
            if (
                col_name == "Excel Wording"
                and row[status_idx] == "KO"
            ):

                html_wording = row[html_idx]

                rich_excel, _ = build_rich_diff(
                    safe_val,
                    html_wording,
                    char_level=char_diff
                )

                cell.value = rich_excel

            elif (
                col_name == "HTML Wording"
                and row[status_idx] == "KO"
            ):

                excel_wording = row[excel_idx]

                _, rich_html = build_rich_diff(
                    excel_wording,
                    safe_val,
                    char_level=char_diff
                )

                cell.value = rich_html

            else:

                cell.value = safe_val
            if col_name == "Status":
                cell.fill = PatternFill(start_color=status_fill_color(val), fill_type="solid")
            if col_name == "Criticality" and val in crit_colors:
                cell.font = Font(bold=True, color=crit_colors[val])
    for c, col in enumerate(columns, 1):
        try:
            max_len = max(df_data[col].astype(str).map(len).max(), len(col))
        except Exception:
            max_len = len(col)
        ws_target.column_dimensions[get_column_letter(c)].width = min(max_len + 3, 60)
    ws_target.freeze_panes = "A2"


def save_report(results: list, campaign_sheet_name: str, mode: str = "wording"):
    df = pd.DataFrame(results)
    total = len(df)
    ok = len(df[df["Status"] == "OK"])
    score = round((ok / total) * 100, 2) if total else 0

    record_run(campaign_sheet_name, mode, total, ok, total - ok, score)

    columns = URL_COLUMNS if mode == "url" else BASE_COLUMNS
    header_labels = URL_HEADER_LABELS if mode == "url" else None

    wb = Workbook()

    # Summary
    ws = wb.active
    meta = [
        ("Date", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Total", total), ("OK", ok), ("KO", total - ok), ("Score", score)
    ]
    write_summary_sheet(ws, f"QA - {campaign_sheet_name}", meta, score)

    # Details
    ws2 = wb.create_sheet("Details")
    df_sorted = sort_results_by_html_order(df).reset_index(drop=True)
    _write_results_sheet(ws2, df_sorted, "4472C4", columns, header_labels, char_diff=(mode == "url"))

    # KO Only
    ws3 = wb.create_sheet("KO Only")
    df_ko = _sort_by_criticality(df[df["Status"] == "KO"]).reset_index(drop=True)
    _write_results_sheet(ws3, df_ko, "C00000", columns, header_labels, char_diff=(mode == "url"))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(
        OUTPUT_DIR,
        f"QA_{campaign_sheet_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )
    wb.save(path)
    prune_root_reports()
    print(f"📄 Rapport genere : {path}")
    return path


def save_consolidated_report(results_by_sequence: dict, report_name: str = "Multi-sequences", mode: str = "wording"):
    """
    Genere un rapport Excel unique couvrant plusieurs sequences.
    results_by_sequence : { nom_sequence: [row_dict, ...] }.
    Ajoute une colonne "Sequence" aux onglets Details/KO Only, et un tableau
    recapitulatif par sequence (en plus du total global) dans le Summary.
    """
    columns = ["Sequence"] + (URL_COLUMNS if mode == "url" else BASE_COLUMNS)
    header_labels = URL_HEADER_LABELS if mode == "url" else None

    all_rows = []
    per_sequence_stats = []
    for seq_name, rows in results_by_sequence.items():
        for r in rows:
            row = dict(r)
            row["Sequence"] = seq_name
            all_rows.append(row)

        seq_total = len(rows)
        seq_ok = sum(1 for r in rows if r.get("Status") == "OK")
        seq_score = round((seq_ok / seq_total) * 100, 2) if seq_total else 0
        per_sequence_stats.append((seq_name, seq_total, seq_ok, seq_total - seq_ok, seq_score))
        record_run(seq_name, mode, seq_total, seq_ok, seq_total - seq_ok, seq_score)

    # df garde TOUS les champs des lignes de resultat (Criticality, Component
    # Name... utilises pour le tri ci-dessous meme en mode "url"), `columns`
    # ne sert qu'a restreindre l'affichage/export dans _write_results_sheet.
    df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(columns=["Sequence"] + BASE_COLUMNS)
    total = len(df)
    ok = len(df[df["Status"] == "OK"]) if total else 0
    score = round((ok / total) * 100, 2) if total else 0

    wb = Workbook()

    # Summary
    ws = wb.active
    meta = [
        ("Date", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Total", total), ("OK", ok), ("KO", total - ok), ("Score global", score)
    ]
    write_summary_sheet(ws, f"QA - {report_name}", meta, score)

    recap_header_row = 9
    for c, label in enumerate(["Séquence", "Total", "OK", "KO", "Score"], start=1):
        cell = ws.cell(recap_header_row, c, label)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="4472C4", fill_type="solid")

    for i, (seq_name, seq_total, seq_ok, seq_ko, seq_score) in enumerate(per_sequence_stats, start=recap_header_row + 1):
        ws.cell(i, 1, seq_name)
        ws.cell(i, 2, seq_total)
        ws.cell(i, 3, seq_ok)
        ws.cell(i, 4, seq_ko)
        score_cell = ws.cell(i, 5, seq_score)
        score_cell.fill = PatternFill(start_color=score_fill_color(seq_score), fill_type="solid")

    for c, width in enumerate([30, 10, 10, 10, 10], start=1):
        ws.column_dimensions[get_column_letter(c)].width = width

    # Details
    ws2 = wb.create_sheet("Details")
    df_sorted = sort_results_by_html_order(df, prefix_columns=["Sequence"]).reset_index(drop=True) if total else df
    _write_results_sheet(ws2, df_sorted, "4472C4", columns, header_labels, char_diff=(mode == "url"))

    # KO Only
    ws3 = wb.create_sheet("KO Only")
    df_ko = _sort_by_criticality(
        df[df["Status"] == "KO"], prefix_columns=["Sequence"]
    ).reset_index(drop=True) if total else df
    _write_results_sheet(ws3, df_ko, "C00000", columns, header_labels, char_diff=(mode == "url"))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_name = re.sub(r"[^\w\-]", "_", report_name)
    path = os.path.join(
        OUTPUT_DIR,
        f"QA_MULTI_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )
    wb.save(path)
    prune_root_reports()
    print(f"📄 Rapport consolidé généré : {path}")
    return path
