import re

import pandas as pd

from qa.config import (
    EXCEL_PLACEHOLDER_PATTERN,
    EXCLUDED_QA_ELEMENTS,
    EXCLUDED_QA_ROWS,
    GENERIC_SHEET_KEYWORD,
    LOYAL_KEYWORDS,
    MIN_TEXT_LENGTH,
    NON_LOYAL_KEYWORDS,
    SEQ_NUMBER_PATTERN,
    URL_COLUMN_NAME,
    URL_SHEET_KEYWORD,
    WORDING_COLUMN_NAME,
)
from qa.text_utils import expand_conditional_variants, normalize, normalize_currency


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


def _parse_wording_rows(df: pd.DataFrame, wording_column: str, sheet_label: str = "") -> list:
    """
    Lit les lignes wording d'un onglet (campagne ou generique) deja charge
    avec header=1. Commun a load_excel_wordings(), load_sibling_sequence_wordings()
    et load_generic_wordings().

    sheet_label : nom de l'onglet source, propage sur chaque wording (cle
    "Sheet") pour que le rapport final puisse indiquer d'ou vient chaque
    ligne (onglet sequence testee, onglet frere FID/NON FID fusionne, ou
    onglet generique) - voir qa.matching.make_row et qa.wording_qa.
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

        # Une cellule wording peut contenir un bloc conditionnel AEM (copie
        # depuis le HTML pour traduction) : norm_variants/curr_variants
        # portent une entree par branche possible (voir
        # expand_conditional_variants), norm/curr restent la 1ere variante
        # pour compat avec tout code qui ne lit que ces champs simples.
        variants = expand_conditional_variants(wording)
        norm_variants = [normalize(v) for v in variants]
        curr_variants = [normalize_currency(v) for v in variants]

        wordings.append({
            "Component": comp,
            "Element": elem,
            "Wording": wording,
            "Technical": technical,
            "norm": norm_variants[0],
            "curr": curr_variants[0],
            "norm_variants": norm_variants,
            "curr_variants": curr_variants,
            "Sheet": sheet_label,
        })

    return wordings


def load_excel_wordings(excel_file: str, keyword: str) -> tuple:
    sheet = find_sheet(excel_file, keyword)
    if not sheet:
        raise ValueError("Feuille Excel non trouvee avec le mot-cle fourni.")

    df = pd.read_excel(excel_file, sheet_name=sheet, header=1, engine="openpyxl")
    df.columns = df.columns.str.strip()

    wording_column = find_column(df, WORDING_COLUMN_NAME)

    return sheet, _parse_wording_rows(df, wording_column, sheet_label=sheet)


def _loyalty_status(sheet_name: str) -> str:
    """'non_loyal' / 'loyal' / None selon les mots-cles fidelite presents
    dans le nom de l'onglet (voir qa.config.NON_LOYAL_KEYWORDS/LOYAL_KEYWORDS ;
    NON_LOYAL_KEYWORDS teste en premier car "NON LOYAL" contient aussi "LOYAL")."""
    upper = sheet_name.upper()
    if any(kw in upper for kw in NON_LOYAL_KEYWORDS):
        return "non_loyal"
    if any(kw in upper for kw in LOYAL_KEYWORDS):
        return "loyal"
    return None


def find_sibling_sequence_sheet(excel_file: str, sheet_name: str) -> str:
    """
    Pour un onglet de sequence deja resolu (ex: "SEQ 1 NON LOYAL"), cherche
    l'onglet "frere" du meme classeur qui couvre la MEME sequence (meme
    numero SEQ) mais l'audience OPPOSEE (LOYAL <-> NON LOYAL, FID <-> NON
    FID).

    Cas cible : les deux onglets ne sont pas deux campagnes differentes,
    mais les 2 wordings possibles d'un seul et meme template HTML, qui
    bascule de l'un a l'autre via une condition AEM selon l'audience du
    destinataire (voir "Blocs conditionnels AEM" dans le README). Tester ce
    HTML contre un seul des deux onglets ferait ressortir a tort le wording
    de l'autre audience comme introuvable.

    Retourne le nom de l'onglet frere, ou None si le nom fourni n'a pas de
    numero de sequence / mot-cle fidelite, si aucun autre onglet ne
    correspond, ou si PLUSIEURS onglets correspondent (ambigu : mieux vaut
    ne rien fusionner que fusionner a tort).
    """
    match = SEQ_NUMBER_PATTERN.search(sheet_name.upper())
    status = _loyalty_status(sheet_name)
    if not match or not status:
        return None

    seq_num = int(match.group(1))
    target_status = "loyal" if status == "non_loyal" else "non_loyal"

    xls = pd.ExcelFile(excel_file)
    candidates = []
    for candidate in xls.sheet_names:
        if candidate == sheet_name:
            continue
        candidate_match = SEQ_NUMBER_PATTERN.search(candidate.upper())
        if not candidate_match or int(candidate_match.group(1)) != seq_num:
            continue
        if _loyalty_status(candidate) == target_status:
            candidates.append(candidate)

    if len(candidates) == 1:
        return candidates[0]
    return None


def list_wording_sheet_choices(excel_file: str) -> list:
    """
    Liste les onglets a proposer dans le menu de selection d'une sequence
    Wording (voir home.py), en excluant ceux qui ne doivent jamais etre
    choisis directement soi-meme dans ce menu :
    - l'onglet UTM (URL_SHEET_KEYWORD) : dedie a la QA URL, jamais un onglet
      de wording de sequence.
    - l'onglet des composants generiques (GENERIC_SHEET_KEYWORD) : toujours
      fusionne automatiquement avec la sequence testee via la case "composants
      generiques" (voir load_generic_wordings), jamais teste seul.
    - un seul des deux onglets d'une paire FID/NON FID (ou LOYAL/NON LOYAL)
      "frere" (voir find_sibling_sequence_sheet) : le second est fusionne
      automatiquement des qu'on teste le premier (voir
      load_sibling_sequence_wordings) - le proposer separement ferait
      croire a 2 tests distincts pour ce qui est en realite le meme HTML,
      teste une seule fois avec les wordings des deux audiences reunis.

    Retourne une liste de tuples (sheet_name, display_label) dans l'ordre du
    classeur : sheet_name est la valeur reelle a repasser a
    load_excel_wordings/run_qa, display_label est le texte a afficher dans
    le menu (indique explicitement la fusion pour une paire FID/NON FID).
    """
    xls = pd.ExcelFile(excel_file)
    all_sheets = xls.sheet_names

    relevant = [
        s for s in all_sheets
        if URL_SHEET_KEYWORD.lower() not in s.lower()
        and GENERIC_SHEET_KEYWORD.lower() not in s.lower()
    ]

    choices = []
    hidden_siblings = set()
    for sheet in relevant:
        if sheet in hidden_siblings:
            continue

        sibling = find_sibling_sequence_sheet(excel_file, sheet)
        if sibling and sibling in relevant:
            hidden_siblings.add(sibling)
            choices.append((sheet, f"{sheet} (regroupé avec {sibling})"))
        else:
            choices.append((sheet, sheet))

    return choices


def load_sibling_sequence_wordings(excel_file: str, sheet_name: str) -> tuple:
    """
    Charge les wordings de l'onglet "frere" LOYAL/NON LOYAL de sheet_name
    (voir find_sibling_sequence_sheet), meme structure de retour que
    load_excel_wordings. Retourne (None, []) si aucun onglet frere n'a ete
    trouve.
    """
    sibling_sheet = find_sibling_sequence_sheet(excel_file, sheet_name)
    if not sibling_sheet:
        return None, []

    df = pd.read_excel(excel_file, sheet_name=sibling_sheet, header=1, engine="openpyxl")
    df.columns = df.columns.str.strip()

    wording_column = find_column(df, WORDING_COLUMN_NAME)

    return sibling_sheet, _parse_wording_rows(df, wording_column, sheet_label=sibling_sheet)


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

    return sheet, _parse_wording_rows(df, wording_column, sheet_label=sheet)


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


URL_LABEL_COL_IDX = 0  # colonne (la plus a gauche) portant le nom du bloc (HEADER, ALGO, CLEAN...)


def _parse_url_sections(raw: pd.DataFrame, header_row_idx: int, section_col_idx: int, tracked_col_idx: int,
                         label_col_idx: int = URL_LABEL_COL_IDX) -> dict:
    """
    Decoupe les lignes de l'onglet UTM en sequences. Une ligne "separateur"
    (une seule cellule non vide, dans la colonne des labels de sequence)
    demarre une nouvelle sequence ; toutes les lignes de donnees suivantes lui
    sont rattachees jusqu'au separateur suivant. Les lignes de donnees
    trouvees avant le premier separateur sont rattachees a la sequence "" (cas
    d'un onglet sans decoupage en sequences).

    Chaque URL est associee au nom du bloc auquel elle appartient (colonne
    label_col_idx, ex: "HEADER", "ALGO"...), retrouve par report vers le bas
    quand la cellule est vide (meme principe que COMPONENT NAME dans les
    onglets wording) : utilise ensuite comme "classe attendue" pour la QA URL.
    Chaque URL porte aussi sa propre section d'origine ("Section", ex. "SEQ 1
    NON FID") : propagee jusqu'au rapport (colonne "Source Section") pour
    savoir de quelle section - la testee, ou sa soeur FID/NON FID fusionnee -
    vient chaque ligne (voir qa.engine.run_qa_for_sequence).
    """
    sections = {"": []}
    current = ""
    last_label = ""

    for r in range(header_row_idx + 1, len(raw)):
        row = raw.iloc[r]
        non_null = row.notna()
        count = int(non_null.sum())

        if count == 0:
            continue

        if count == 1 and non_null.iloc[section_col_idx]:
            current = str(row.iloc[section_col_idx]).strip()
            sections.setdefault(current, [])
            last_label = ""
            continue

        label_val = row.iloc[label_col_idx] if label_col_idx < len(row) else None
        if pd.notna(label_val) and str(label_val).strip():
            last_label = str(label_val).strip()

        value = row.iloc[tracked_col_idx]
        if pd.notna(value):
            url = str(value).strip()
            if url:
                sections[current].append({"label": last_label, "url": url, "Section": current})

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

    Retourne (sheet, section_name, urls) : section_name est le nom EXACT de
    la section resolue (utilise par qa.engine pour retrouver sa section
    "soeur" FID/NON FID, voir find_sibling_url_section), ou "" si l'onglet
    ne comporte pas de decoupage en sequences.
    """
    sheet, raw, header_row_idx, tracked_col_idx, section_col_idx = _locate_url_sheet_layout(excel_file)

    if header_row_idx is None:
        raise ValueError(f"Colonne '{URL_COLUMN_NAME}' introuvable dans la feuille '{sheet}'.")

    if section_col_idx is None:
        # Pas de colonne "URL" (label de sequence) : impossible de decouper
        # en sequences, on retourne tout l'onglet sans filtrer.
        urls = []
        last_label = ""
        for r in range(header_row_idx + 1, len(raw)):
            row = raw.iloc[r]
            label_val = row.iloc[URL_LABEL_COL_IDX] if URL_LABEL_COL_IDX < len(row) else None
            if pd.notna(label_val) and str(label_val).strip():
                last_label = str(label_val).strip()

            value = row.iloc[tracked_col_idx]
            if pd.notna(value):
                url = str(value).strip()
                if url:
                    urls.append({"label": last_label, "url": url, "Section": ""})
        return sheet, "", urls

    sections = _parse_url_sections(raw, header_row_idx, section_col_idx, tracked_col_idx)
    named_sections = {name: urls for name, urls in sections.items() if name and urls}

    if not named_sections:
        # Onglet sans decoupage en sequences : tout est dans la section "".
        return sheet, "", sections[""]

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

    return sheet, matches[0], named_sections[matches[0]]


def find_sibling_url_section(excel_file: str, section_name: str) -> str:
    """
    Symetrique de find_sibling_sequence_sheet, mais pour les sections
    nommees d'un MEME onglet UTM (voir _parse_url_sections) plutot que des
    onglets Excel separes : pour une section deja resolue (ex: "SEQ 1 NON
    FID"), cherche la section "soeur" de la meme sequence mais l'audience
    opposee (FID <-> NON FID, LOYAL <-> NON LOYAL) - meme HTML, le lien
    (et sa destination/ses parametres UTM) pouvant basculer de l'un a
    l'autre selon l'audience exactement comme le wording (voir
    find_sibling_sequence_sheet).

    Retourne le nom de la section soeur, ou None si section_name n'a pas de
    numero de sequence/mot-cle fidelite reconnaissable, si aucune autre
    section ne correspond, ou si PLUSIEURS correspondent (ambigu).
    """
    match = SEQ_NUMBER_PATTERN.search(section_name.upper())
    status = _loyalty_status(section_name)
    if not match or not status:
        return None

    seq_num = int(match.group(1))
    target_status = "loyal" if status == "non_loyal" else "non_loyal"

    candidates = []
    for candidate in list_url_sequences(excel_file):
        if candidate == section_name:
            continue
        candidate_match = SEQ_NUMBER_PATTERN.search(candidate.upper())
        if not candidate_match or int(candidate_match.group(1)) != seq_num:
            continue
        if _loyalty_status(candidate) == target_status:
            candidates.append(candidate)

    if len(candidates) == 1:
        return candidates[0]
    return None


def load_sibling_url_section(excel_file: str, section_name: str) -> tuple:
    """
    Charge les URLs de la section "soeur" FID/NON FID de section_name (voir
    find_sibling_url_section), meme structure de retour que load_excel_urls
    (sans le sheet). Retourne (None, []) si aucune section soeur n'a ete
    trouvee.
    """
    sibling_name = find_sibling_url_section(excel_file, section_name)
    if not sibling_name:
        return None, []

    _, raw, header_row_idx, tracked_col_idx, section_col_idx = _locate_url_sheet_layout(excel_file)
    if header_row_idx is None or section_col_idx is None:
        return None, []

    sections = _parse_url_sections(raw, header_row_idx, section_col_idx, tracked_col_idx)
    return sibling_name, sections.get(sibling_name, [])


def list_url_sequence_choices(excel_file: str) -> list:
    """
    Liste les sequences a proposer dans le menu de selection URL (voir
    home.py), en ne proposant qu'UN SEUL des deux noms d'une paire FID/NON
    FID (ou LOYAL/NON LOYAL) "soeur" au sein de l'onglet UTM (voir
    find_sibling_url_section) - la seconde est fusionnee automatiquement
    des qu'on teste la premiere (voir load_sibling_url_section dans
    qa.engine), la proposer separement ferait croire a 2 tests distincts
    pour ce qui est en realite le meme HTML.

    Retourne une liste de tuples (section_name, display_label), dans le
    meme ordre que list_url_sequences (liste vide si l'onglet UTM ne
    comporte pas de decoupage en sequences - meme cas que list_url_sequences).
    """
    all_sections = list_url_sequences(excel_file)

    choices = []
    hidden_siblings = set()
    for section in all_sections:
        if section in hidden_siblings:
            continue

        sibling = find_sibling_url_section(excel_file, section)
        if sibling and sibling in all_sections:
            hidden_siblings.add(sibling)
            choices.append((section, f"{section} (regroupé avec {sibling})"))
        else:
            choices.append((section, section))

    return choices
