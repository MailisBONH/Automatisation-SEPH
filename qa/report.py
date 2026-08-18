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

from qa.config import CRITICALITY_ORDER, OUTPUT_DIR
from qa.history import record_run


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
def build_rich_diff(text1, text2):

    text1 = str(text1 or "")
    text2 = str(text2 or "")

    words1 = text1.split()
    words2 = text2.split()

    matcher = difflib.SequenceMatcher(
        None,
        words1,
        words2
    )

    rich1 = CellRichText()
    rich2 = CellRichText()

    red_bold = InlineFont(
        color="FF0000",
        b=True
    )

    normal = InlineFont()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():

        part1 = " ".join(words1[i1:i2])
        part2 = " ".join(words2[j1:j2])

        if tag == "equal":

            if part1:
                rich1.append(
                    TextBlock(normal, part1 + " ")
                )

            if part2:
                rich2.append(
                    TextBlock(normal, part2 + " ")
                )

        else:

            if part1:
                rich1.append(
                    TextBlock(red_bold, part1 + " ")
                )

            if part2:
                rich2.append(
                    TextBlock(red_bold, part2 + " ")
                )

    return rich1, rich2


BASE_COLUMNS = [
    "Component Name", "Element", "Source Sheet", "Expected Class", "HTML Class",
    "Excel Wording", "HTML Wording",
    "Status", "Error Type", "Similarity", "Criticality",
    "Match Type"
]

# Rapport dedie aux URLs : pas de wording/composant, seulement classe +
# URL attendues (Excel) vs trouvees (HTML), et le statut.
URL_COLUMNS = ["Expected Class", "HTML Class", "Excel Wording", "HTML Wording", "Status"]

URL_HEADER_LABELS = {
    "Expected Class": "Classe attendue (Excel)",
    "HTML Class": "Classe trouvée (HTML)",
    "Excel Wording": "URL attendue (Excel)",
    "HTML Wording": "URL trouvée (HTML)",
    "Status": "Status",
    "Sequence": "Séquence",
}


def _write_results_sheet(ws_target, df_data, header_color, columns, header_labels=None):
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
                    html_wording
                )

                cell.value = rich_excel

            elif (
                col_name == "HTML Wording"
                and row[status_idx] == "KO"
            ):

                excel_wording = row[excel_idx]

                _, rich_html = build_rich_diff(
                    excel_wording,
                    safe_val
                )

                cell.value = rich_html

            else:

                cell.value = safe_val
            if col_name == "Status":
                cell.fill = PatternFill(
                    start_color="C6EFCE" if val == "OK" else "FFC7CE",
                    fill_type="solid"
                )
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
    ws.title = "Summary"
    ws["A1"] = f"QA - {campaign_sheet_name}"
    ws["A1"].font = Font(size=14, bold=True)
    meta = [
        ("Date", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Total", total), ("OK", ok), ("KO", total - ok), ("Score", score)
    ]
    for i, (k, v) in enumerate(meta, start=3):
        ws[f"A{i}"] = k
        ws[f"A{i}"].font = Font(bold=True)
        ws[f"B{i}"] = v
    ws["B7"].fill = PatternFill(
        start_color="C6EFCE" if score == 100 else "FFEB9C" if score >= 80 else "FFC7CE",
        fill_type="solid"
    )

    # Details
    ws2 = wb.create_sheet("Details")
    df_sorted = sort_results_by_html_order(df).reset_index(drop=True)
    _write_results_sheet(ws2, df_sorted, "4472C4", columns, header_labels)

    # KO Only
    ws3 = wb.create_sheet("KO Only")
    df_ko = _sort_by_criticality(df[df["Status"] == "KO"]).reset_index(drop=True)
    _write_results_sheet(ws3, df_ko, "C00000", columns, header_labels)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(
        OUTPUT_DIR,
        f"QA_{campaign_sheet_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )
    wb.save(path)
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
    ws.title = "Summary"
    ws["A1"] = f"QA - {report_name}"
    ws["A1"].font = Font(size=14, bold=True)
    meta = [
        ("Date", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Total", total), ("OK", ok), ("KO", total - ok), ("Score global", score)
    ]
    for i, (k, v) in enumerate(meta, start=3):
        ws[f"A{i}"] = k
        ws[f"A{i}"].font = Font(bold=True)
        ws[f"B{i}"] = v
    ws["B7"].fill = PatternFill(
        start_color="C6EFCE" if score == 100 else "FFEB9C" if score >= 80 else "FFC7CE",
        fill_type="solid"
    )

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
        score_cell.fill = PatternFill(
            start_color="C6EFCE" if seq_score == 100 else "FFEB9C" if seq_score >= 80 else "FFC7CE",
            fill_type="solid"
        )

    for c, width in enumerate([30, 10, 10, 10, 10], start=1):
        ws.column_dimensions[get_column_letter(c)].width = width

    # Details
    ws2 = wb.create_sheet("Details")
    df_sorted = sort_results_by_html_order(df, prefix_columns=["Sequence"]).reset_index(drop=True) if total else df
    _write_results_sheet(ws2, df_sorted, "4472C4", columns, header_labels)

    # KO Only
    ws3 = wb.create_sheet("KO Only")
    df_ko = _sort_by_criticality(
        df[df["Status"] == "KO"], prefix_columns=["Sequence"]
    ).reset_index(drop=True) if total else df
    _write_results_sheet(ws3, df_ko, "C00000", columns, header_labels)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_name = re.sub(r"[^\w\-]", "_", report_name)
    path = os.path.join(
        OUTPUT_DIR,
        f"QA_MULTI_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )
    wb.save(path)
    print(f"📄 Rapport consolidé généré : {path}")
    return path
