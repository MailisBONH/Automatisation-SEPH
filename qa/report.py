import os
import difflib
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.cell.rich_text import CellRichText
from openpyxl.cell.text import InlineFont
from openpyxl.cell.rich_text import TextBlock

from qa.config import OUTPUT_DIR


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


def save_report(results: list, campaign_sheet_name: str):
    df = pd.DataFrame(results)
    total = len(df)
    ok = len(df[df["Status"] == "OK"])
    score = round((ok / total) * 100, 2) if total else 0

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

    columns = [
        "Component Name", "Element", "Expected Class", "HTML Class",
        "Excel Wording", "HTML Wording",
        "Status", "Error Type", "Similarity", "Criticality",
         "Match Type"
    ]

    def _write_sheet(ws_target, df_data, header_color):
        for c, col in enumerate(columns, 1):
            cell = ws_target.cell(1, c, col)
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
                    and row[6] == "KO"   # Status
                ):

                    html_wording = row[5]

                    rich_excel, _ = build_rich_diff(
                        safe_val,
                        html_wording
                    )

                    cell.value = rich_excel

                elif (
                    col_name == "HTML Wording"
                    and row[6] == "KO"
                ):

                    excel_wording = row[4]

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

    # Details
    ws2 = wb.create_sheet("Details")
    df_sorted = pd.concat([
        df[df["Status"] == "KO"].sort_values(["Criticality", "Component Name"]),
        df[df["Status"] == "OK"].sort_values(["Component Name"])
    ]).reset_index(drop=True)
    _write_sheet(ws2, df_sorted, "4472C4")

    # KO Only
    ws3 = wb.create_sheet("KO Only")
    df_ko = df[df["Status"] == "KO"].sort_values(
        ["Criticality", "Component Name"]).reset_index(drop=True)
    _write_sheet(ws3, df_ko, "C00000")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(
        OUTPUT_DIR,
        f"QA_{campaign_sheet_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )
    wb.save(path)
    print(f"📄 Rapport genere : {path}")
    return path
