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


def _find_cell(raw: pd.DataFrame, label: str):
    """
    Cherche la premiere cellule dont la valeur (str, strip, insensible a la
    casse) est strictement egale a label. Retourne (row_idx, col_idx) ou
    (None, None).
    """
    target = label.strip().lower()
    for r in range(len(raw)):
        row = raw.iloc[r]
        for c, val in enumerate(row):
            if isinstance(val, str) and val.strip().lower() == target:
                return r, c
    return None, None


def _parse_url_sections(raw: pd.DataFrame, header_row_idx: int, section_col_idx: int, tracked_col_idx: int) -> dict:
    """
    Decoupe les lignes de l'onglet UTM en sequences. Une ligne "separateur"
    (une seule cellule non vide, dans la colonne des labels de sequence)
    demarre une nouvelle sequence ; toutes les lignes de donnees suivantes lui
    sont rattachees jusqu'au separateur suivant. Les lignes de donnees
    trouvees avant le premier separateur sont rattachees a la sequence "" (cas
    d'un onglet sans decoupage en sequences).
    """
    sections = {"": []}
    current = ""

    for r in range(header_row_idx + 1, len(raw)):
        row = raw.iloc[r]
        non_null = row.notna()
        count = int(non_null.sum())

        if count == 0:
            continue

        if count == 1 and non_null.iloc[section_col_idx]:
            current = str(row.iloc[section_col_idx]).strip()
            sections.setdefault(current, [])
            continue

        value = row.iloc[tracked_col_idx]
        if pd.notna(value):
            url = str(value).strip()
            if url:
                sections[current].append(url)

    return sections


def load_excel_urls(excel_file: str, keyword: str) -> tuple:
    """
    Charge les URLs de reference depuis l'onglet dont le nom contient
    URL_SHEET_KEYWORD ("UTM" par defaut, le nom peut contenir d'autres mots).

    Cet onglet peut regrouper plusieurs sequences empilees, separees par une
    ligne ne contenant que le nom de la sequence (dans la colonne "URL"). Si
    c'est le cas, on ne garde que les URLs de la sequence dont le nom
    correspond a keyword (recherche insensible a la casse). Si l'onglet ne
    contient aucun decoupage en sequences, keyword est ignore et toutes les
    URLs sont retournees.
    """
    sheet = find_sheet(excel_file, URL_SHEET_KEYWORD)

    raw = pd.read_excel(excel_file, sheet_name=sheet, header=None, engine="openpyxl")

    header_row_idx, tracked_col_idx = _find_cell(raw, URL_COLUMN_NAME)
    if header_row_idx is None:
        raise ValueError(f"Colonne '{URL_COLUMN_NAME}' introuvable dans la feuille '{sheet}'.")

    section_col_idx = _find_cell(raw.iloc[[header_row_idx]].reset_index(drop=True), "URL")[1]

    if section_col_idx is None:
        # Pas de colonne "URL" (label de sequence) : impossible de decouper
        # en sequences, on retourne tout l'onglet sans filtrer.
        urls = [
            str(v).strip()
            for v in raw.iloc[header_row_idx + 1:, tracked_col_idx].dropna()
            if str(v).strip()
        ]
        return sheet, urls

    sections = _parse_url_sections(raw, header_row_idx, section_col_idx, tracked_col_idx)
    named_sections = {name: urls for name, urls in sections.items() if name and urls}

    if not named_sections:
        # Onglet sans decoupage en sequences : tout est dans la section "".
        return sheet, sections[""]

    matches = [name for name in named_sections if keyword.lower() in name.lower()]

    if len(matches) == 0:
        available = ", ".join(named_sections.keys())
        raise ValueError(
            f"Aucune séquence ne correspond à '{keyword}' dans l'onglet '{sheet}'. "
            f"Séquences disponibles : {available}."
        )

    if len(matches) > 1:
        matches_list = ", ".join(matches)
        raise ValueError(
            f"Plusieurs séquences correspondent à '{keyword}' dans l'onglet '{sheet}' : {matches_list}. "
            "Merci de préciser davantage le mot-clé."
        )

    return sheet, named_sections[matches[0]]
