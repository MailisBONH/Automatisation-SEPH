import re

import pandas as pd

from qa.config import (
    EXCEL_PLACEHOLDER_PATTERN,
    EXCLUDED_QA_ELEMENTS,
    EXCLUDED_QA_ROWS,
    GENERIC_SHEET_KEYWORD,
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


def find_column(df: pd.DataFrame, keyword) -> str:
    """
    Trouve le nom exact d'une colonne dont le nom contient keyword
    (insensible a la casse). Meme principe que find_sheet().

    keyword peut etre une chaine unique, ou une liste de mots-clés candidats
    essayes dans l'ordre (utile quand le nom de la colonne wording varie
    selon le type d'onglet, ex: "TRANSLATION" pour un onglet de campagne,
    "TEXT" pour un onglet de composants génériques) : le premier mot-clé qui
    resout une colonne unique est retenu.
    """
    keywords = [keyword] if isinstance(keyword, str) else list(keyword)

    issues = []
    for kw in keywords:
        matches = [c for c in df.columns if kw.lower() in str(c).lower()]

        if len(matches) == 1:
            return matches[0]

        if len(matches) == 0:
            issues.append(f"aucune colonne ne correspond à '{kw}'")
        else:
            cols_list = ", ".join(str(c) for c in matches)
            issues.append(f"plusieurs colonnes correspondent à '{kw}' ({cols_list})")

    raise ValueError(
        "Impossible de déterminer la colonne wording : " + " ; ".join(issues) + ". "
        "Merci de vérifier le nom de la colonne wording."
    )


def clean_excel_wording(text: str) -> str:
    if not text:
        return ""

    text = str(text)

    # supprime [gift validity end date]
    text = EXCEL_PLACEHOLDER_PATTERN.sub("", text)

    # nettoie les espaces générés
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _parse_wording_rows(df: pd.DataFrame, wording_column: str) -> list:
    """
    Lit les lignes wording d'un onglet (campagne ou generique) deja charge
    avec header=1. Commun a load_excel_wordings() et load_generic_wordings().
    """
    wordings = []
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

        if (comp_norm, elem_norm) in EXCLUDED_QA_ROWS or elem_norm in EXCLUDED_QA_ELEMENTS:
            continue

        wording = row.get(wording_column)

        if pd.isna(wording):
            continue
        wording = clean_excel_wording(wording)
        if len(wording) < MIN_TEXT_LENGTH:
            continue

        technical = row.get(TECHNICAL_COLUMN)
        if pd.notna(technical):
            technical = str(technical).strip()

        wordings.append({
            "Component": comp,
            "Element": elem,
            "Wording": wording,
            "Technical": technical,
            "norm": normalize(wording),
            "curr": normalize_currency(wording)
        })

    return wordings


def load_excel_wordings(excel_file: str, keyword: str) -> tuple:
    sheet = find_sheet(excel_file, keyword)
    if not sheet:
        raise ValueError("Feuille Excel non trouvee avec le mot-cle fourni.")

    df = pd.read_excel(excel_file, sheet_name=sheet, header=1, engine="openpyxl")
    df.columns = df.columns.str.strip()

    wording_column = find_column(df, WORDING_COLUMN_NAME)

    return sheet, _parse_wording_rows(df, wording_column)


def load_generic_wordings(excel_file: str) -> tuple:
    """
    Charge les wordings "composants génériques" (communs à toutes les
    séquences d'une campagne : footer, mentions légales, programme de
    fidélité...) depuis l'onglet dont le nom contient GENERIC_SHEET_KEYWORD
    ("GENERIC"). Même structure de retour que load_excel_wordings, pour être
    fusionnée avec les wordings spécifiques à la séquence testée.
    """
    sheet = find_sheet(excel_file, GENERIC_SHEET_KEYWORD)

    df = pd.read_excel(excel_file, sheet_name=sheet, header=1, engine="openpyxl")
    df.columns = df.columns.str.strip()

    # Le nom de cette colonne varie selon les onglets : "TRANSLATION ..."
    # comme un onglet de campagne, ou simplement "TEXT" (deja localise).
    wording_column = find_column(df, [WORDING_COLUMN_NAME, "TEXT"])

    return sheet, _parse_wording_rows(df, wording_column)


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


def _locate_url_sheet_layout(excel_file: str):
    """
    Trouve l'onglet UTM et repere la ligne d'en-tete, la colonne des URLs
    trackees et (si presente) la colonne des labels de sequence.
    Retourne (sheet, raw, header_row_idx, tracked_col_idx, section_col_idx).
    section_col_idx et/ou header_row_idx peuvent etre None si la structure
    attendue n'est pas trouvee.
    """
    sheet = find_sheet(excel_file, URL_SHEET_KEYWORD)

    raw = pd.read_excel(excel_file, sheet_name=sheet, header=None, engine="openpyxl")

    header_row_idx, tracked_col_idx = _find_cell(raw, URL_COLUMN_NAME)
    if header_row_idx is None:
        return sheet, raw, None, None, None

    section_col_idx = _find_cell(raw.iloc[[header_row_idx]].reset_index(drop=True), "URL")[1]

    return sheet, raw, header_row_idx, tracked_col_idx, section_col_idx


def list_url_sequences(excel_file: str) -> list:
    """
    Liste les noms de sequences detectees dans l'onglet UTM, sans filtrer par
    mot-cle (utile pour proposer un choix a l'utilisateur, ex: mode
    multi-sequences). Retourne une liste vide si l'onglet ne comporte pas de
    decoupage en sequences (onglet a plat) ou si sa structure est inattendue.
    """
    sheet, raw, header_row_idx, tracked_col_idx, section_col_idx = _locate_url_sheet_layout(excel_file)

    if header_row_idx is None or section_col_idx is None:
        return []

    sections = _parse_url_sections(raw, header_row_idx, section_col_idx, tracked_col_idx)
    return [name for name, urls in sections.items() if name and urls]


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
    sheet, raw, header_row_idx, tracked_col_idx, section_col_idx = _locate_url_sheet_layout(excel_file)

    if header_row_idx is None:
        raise ValueError(f"Colonne '{URL_COLUMN_NAME}' introuvable dans la feuille '{sheet}'.")

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
