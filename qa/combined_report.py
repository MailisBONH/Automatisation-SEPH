"""
Rapport combine Wording + URL pour une sequence : fusionne le dernier
rapport de chaque type deja archive (voir qa.archive) en un seul classeur
(un onglet par type + un resume global), pour juger la qualite globale
d'une sequence en un coup d'oeil plutot que dans deux rapports separes.
"""
import os
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows

from qa.archive import archive_report, list_reports
from qa.config import OUTPUT_DIR
from qa.report import status_fill_color, write_summary_sheet


def _write_df_sheet(ws, df: pd.DataFrame):
    for r, row in enumerate(dataframe_to_rows(df, index=False, header=True), start=1):
        for c, val in enumerate(row, start=1):
            cell = ws.cell(r, c, val)
            if r == 1:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill(start_color="4472C4", fill_type="solid")

    status_col = df.columns.get_loc("Status") + 1 if "Status" in df.columns else None
    if status_col:
        for r in range(2, len(df) + 2):
            status_val = ws.cell(r, status_col).value
            ws.cell(r, status_col).fill = PatternFill(start_color=status_fill_color(status_val), fill_type="solid")
    for c, col in enumerate(df.columns, start=1):
        try:
            max_len = max(df[col].astype(str).map(len).max(), len(str(col)))
        except (TypeError, ValueError):
            max_len = len(str(col))
        ws.column_dimensions[get_column_letter(c)].width = min(max_len + 3, 60)
    ws.freeze_panes = "A2"


def build_combined_report(country: str, trigger: str, sequence: str):
    """
    Construit un rapport combine a partir du dernier rapport wording et du
    dernier rapport url archives pour cette sequence (voir qa.archive).
    Retourne (path, stats) ou (None, None) si un seul des deux types est
    disponible pour l'instant (rien a combiner). stats = {total, ok, ko,
    score} sur l'ensemble wording+url.
    """
    wording_reports = list_reports(country, trigger, sequence, mode="wording")
    url_reports = list_reports(country, trigger, sequence, mode="url")

    if not wording_reports or not url_reports:
        return None, None

    wording_df = pd.read_excel(wording_reports[0], sheet_name="Details", engine="openpyxl")
    url_df = pd.read_excel(url_reports[0], sheet_name="Details", engine="openpyxl")

    wording_total, wording_ok = len(wording_df), int((wording_df["Status"] == "OK").sum())
    url_total, url_ok = len(url_df), int((url_df["Status"] == "OK").sum())

    total = wording_total + url_total
    ok = wording_ok + url_ok
    score = round(ok / total * 100, 2) if total else 0

    wording_score = round(wording_ok / wording_total * 100, 2) if wording_total else 0
    url_score = round(url_ok / url_total * 100, 2) if url_total else 0

    wb = Workbook()

    ws = wb.active
    meta = [
        ("Date", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Score Wording", f"{wording_score}% ({wording_ok}/{wording_total})"),
        ("Score URL", f"{url_score}% ({url_ok}/{url_total})"),
        ("Score combiné", f"{score}% ({ok}/{total})"),
    ]
    write_summary_sheet(ws, f"QA combiné — {sequence}", meta, score)
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 40

    _write_df_sheet(wb.create_sheet("Wording"), wording_df)
    _write_df_sheet(wb.create_sheet("URL"), url_df)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    flat_path = os.path.join(OUTPUT_DIR, f"QA_COMBINED_{sequence.replace(' ', '_')}_{timestamp}.xlsx")
    wb.save(flat_path)

    final_path = archive_report(flat_path, country, trigger, sequence, mode="combined")

    print(f"📄 Rapport combiné généré : {final_path}")

    return final_path, {"total": total, "ok": ok, "ko": total - ok, "score": score}
