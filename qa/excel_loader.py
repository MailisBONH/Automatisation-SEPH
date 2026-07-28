import re

import pandas as pd

from qa.config import (
    EXCEL_PLACEHOLDER_PATTERN,
    EXCLUDED_QA_ROWS,
    MIN_TEXT_LENGTH,
    URL_COLUMN_NAME,
    URL_SHEET_KEYWORD,
    WORDING_COLUMN_NAME,
)
from qa.text_utils import normalize, normalize_currency


# =========================
# LECTURE EXCEL
# =========================
def find_sheet(excel_file: str, keyword: str):
    xls = pd.ExcelFile(excel_file)

    matches = [
        sheet for sheet in xls.sheet_names
        if keyword.lower() in sheet.lower()
    ]

    # Aucun match
    if len(matches) == 0:
        raise ValueError(
            f"Aucune feuille ne correspond à '{keyword}'. "
            "Merci de vérifier votre Campaign keyword."
        )

    # Plusieurs matchs
    if len(matches) > 1:
        sheets_list = ", ".join(matches)
        raise ValueError(
            f"Plusieurs feuilles correspondent à '{keyword}' : {sheets_list}. "
            "Merci de préciser davantage le Campaign keyword."
        )

    return matches[0]


def clean_excel_wording(text: str) -> str:
    if not text:
        return ""

    text = str(text)

    # supprime [gift validity end date]
    text = EXCEL_PLACEHOLDER_PATTERN.sub("", text)

    # nettoie les espaces générés
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def load_excel_wordings(excel_file: str, keyword: str) -> tuple:
    sheet = find_sheet(excel_file, keyword)
    if not sheet:
        raise ValueError("Feuille Excel non trouvee avec le mot-cle fourni.")

    df = pd.read_excel(excel_file, sheet_name=sheet, header=1, engine="openpyxl")
    df.columns = df.columns.str.strip()

    if WORDING_COLUMN_NAME not in df.columns:
        raise ValueError(f"Colonne '{WORDING_COLUMN_NAME}' introuvable dans la feuille '{sheet}'.")

    excel_wordings = []
    TECHNICAL_COLUMN = "TECHNICAL NAME"
    last_comp = None

    for _, row in df.iterrows():
        comp = row.get("COMPONENT NAME")
        if pd.notna(comp):
            last_comp = str(comp).strip()
        else:
            comp = last_comp

        elem = str(row.get("ELEMENT", "")).strip()


        comp_norm = str(comp).strip().lower() if comp else ""
        elem_norm = elem.lower()

        if (comp_norm, elem_norm) in EXCLUDED_QA_ROWS:
            continue


        wording = row.get(WORDING_COLUMN_NAME)

        if pd.isna(wording):
            continue
        wording = clean_excel_wording(wording)
        if len(wording) < MIN_TEXT_LENGTH:
            continue

        excel_wordings.append({
            "Component": comp,
            "Element": elem,
            "Wording": wording,
            "Technical": row.get(TECHNICAL_COLUMN),
            "norm": normalize(wording),
            "curr": normalize_currency(wording)
        })

    return sheet, excel_wordings


def load_excel_urls(excel_file: str) -> tuple:
    """
    Charge les URLs de reference depuis l'onglet dont le nom contient
    URL_SHEET_KEYWORD ("UTM" par defaut, le nom peut contenir d'autres mots).
    """
    sheet = find_sheet(excel_file, URL_SHEET_KEYWORD)

    df = pd.read_excel(excel_file, sheet_name=sheet, engine="openpyxl")
    df.columns = df.columns.str.strip()

    if URL_COLUMN_NAME not in df.columns:
        raise ValueError(f"Colonne '{URL_COLUMN_NAME}' introuvable dans la feuille '{sheet}'.")

    urls = [
        str(u).strip()
        for u in df[URL_COLUMN_NAME].dropna()
        if str(u).strip()
    ]

    return sheet, urls
