import os
import re
import html
import difflib
from datetime import datetime

import pandas as pd
from bs4 import BeautifulSoup, NavigableString
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


# =========================
# CONFIGURATION
# =========================
HTML_FILE = "input/email.html"            # Chemin vers le HTML à tester
EXCEL_FILE = "input/wording.xlsx"         # Chemin vers l'Excel référentiel
OUTPUT_DIR = "output"

WORDING_COLUMN_NAME = "TEXT EN"           # Colonne texte dans l'Excel
CAMPAIGN_KEYWORD = "HugPlan S1 Bronze"    # Mot-clé pour sélectionner l'onglet dans l'Excel
MIN_TEXT_LENGTH = 3
DEBUG_MODE = True


AEM_PATTERN = re.compile(r"<%=?\s*.*?\s*%>", re.DOTALL)
VAR_PATTERN = re.compile(r"\$\{[^}]*\}")

EXCEL_PLACEHOLDER_PATTERN = re.compile(
    r"\[[^\]]+\]"
)


def clean_text(text: str) -> str:
    if not text:
        return ""

    text = html.unescape(text)
    text = AEM_PATTERN.sub("", text)
    text = VAR_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


# =========================
# RECONSTRUCTION VIEW-SOURCE
# =========================
def clean_template_code(content: str) -> str:
    content = clean_text(content)
    return content


def is_view_source_document(raw: str) -> bool:
    return 'class="line-content"' in raw and 'class="line-number"' in raw


def extract_real_html_from_viewsource(raw: str) -> str:
    """
    FIX : utilise td.get_text() + html.unescape() au lieu de stripped_strings.
    L'ancienne approche fragmentait les attributs HTML sur plusieurs lignes,
    empêchant BeautifulSoup de trouver class="text-contribution".
    """
    soup = BeautifulSoup(raw, "html.parser")
    cells = soup.find_all("td", class_="line-content")

    if not cells:
        return raw
    lines = [td.get_text() for td in cells]
    reconstructed = "\n".join(lines)
    reconstructed = html.unescape(reconstructed)
    return reconstructed


# =========================
# UTILITAIRES
# =========================
def get_type(tag: str) -> str:
    mapping = {
        "h1": "H1", "h2": "H2", "h3": "H3",
        "a": "CTA", "img": "ALT", "title": "Title",
        "li": "List Item", "p": "Paragraph",
        "span": "Text", "b": "Text", "strong": "Text",
        "em": "Text", "i": "Text", "small": "Text", "div": "Text",
    }
    return mapping.get(tag.lower(), "Text")


def is_valid(text: str) -> bool:
    t = text.strip()
    if len(t) < MIN_TEXT_LENGTH:
        return False
    t_low = t.lower()
    invalid_patterns = [
        r"^<", r"^/\/\*", r"^http", r"^mailto:", r"^/content/",
        r"charset", r"viewport", r"xmlns", r"doctype",
        r"role=", r"style=", r"class=",
        r"^\d+$", r"^#[0-9A-Fa-f]{3,8}$", r"^\d+(px|em|%)$", r"mso-",
        r"font-family\s*:", r"padding\s*:", r"margin\s*:", r"background\s*:"
    ]
    for p in invalid_patterns:
        if re.search(p, t_low):
            return False
    
    tech = sum(
        1 for c in t
        if c in '<>{}[]'
    )

    if len(t) > 0 and tech / len(t) > 0.05:
        return False
    alpha = sum(1 for c in t if c.isalpha())
    if alpha < 3:
        return False
    return True


# =========================
# NORMALISATION
# =========================
def normalize(text: str) -> str:
    """
    Normalisation standard.

    FIXES :
    1. Supprime <%= %> et <% %> (AEM / Adobe Campaign)
    2. Supprime les placeholders PO : [xxx], XXX
    3. Corrige les mots AEM collés : 'pointspoint' -> 'points point'
       (causé par <% if(n>1){%>points<%}else{%>point<%}%> → suppression → collision)
    4. Ne normalise PAS les separateurs | / I de facon globale
       (casserait 'In need', "I'll", etc.) → géré dans normalize_currency()
    5. Retire les caracteres invisibles LRM/RLM et espaces insecables
    """
    if text is None:
        return ""
    s = str(text)

    # Directives AEM / Adobe Campaign
    s = re.sub(r"<%=?\s*.*?\s*%>", "", s, flags=re.DOTALL)
    s = re.sub(r"\$\{[^}]*\}", "", s)
    s = re.sub(r"\[.*?\]", "", text)


    # Placeholders metier PO
    s = re.sub(r"\[Name\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"\bXXX\b", "", s)
    s = re.sub(r"Version \d[^:]*:", "", s, flags=re.IGNORECASE)

    # FIX mots AEM colles : 'pointspoint' -> 'points point'

    s = re.sub(r"(points?)(points?)", r"\1 \2", text)
    s = re.sub(r"\b(gifts)(gift)\b", r"\1 \2", s, flags=re.IGNORECASE)

    
    s = re.sub(r"\s+([.,])", r"\1", text)

    
    s = " ".join(text.split())


    # HTML
    s = re.sub(r"(?i)<br\s*/?>", " ", s)
    s = html.unescape(s)

    # Caracteres invisibles
    s = s.replace("\u200e", "").replace("\u200f", "").replace("\xa0", " ")
    s = " ".join(s.split())
    return s.strip()


def normalize_currency(text: str) -> str:
    """
    Normalisation supplementaire pour les listes de devises.
    Retire les separateurs visuels (|, /, 'I' majuscule entre montants).
    Ex: '10 AED | 10 SAR' == '10 AED I 10 SAR' apres cette normalisation.
    Utilisee uniquement en fallback dans compare() si le matching standard echoue.
    """
    s = normalize(text)
    s = re.sub(r"\s*\|\s*", " ", s)
    s = re.sub(r"\s*/\s*", " ", s)
    # 'I' separateur entre code devise et chiffre : 'AED I 10' ou 'KWD I0'
    s = re.sub(r"([A-Z]{2,4})\s+I\s*(\d)", r"\1 \2", s)
    s = re.sub(r"(\d)\s+I\s+(\d)", r"\1 \2", s)
    s = " ".join(s.split())
    return s.strip()


# =========================
# COMPARAISON
# =========================
def compare(html_text: str, excel_text: str) -> tuple:
    """
    Retour :
    (Status, ErrorType, Similarity, MatchType)
    """

    h = normalize(html_text)
    e = normalize(excel_text)

    # -----------------------------
    # Cas vides
    # -----------------------------
    if not h and not e:
        return ("OK", None, 100, "Empty")

    if not h or not e:
        return ("KO", "Text differs", 0, "Empty mismatch")

    # -----------------------------
    # Exact match uniquement
    # -----------------------------
    if h == e:
        return ("OK", None, 100, "Exact")

    # -----------------------------
    # Différence de casse uniquement
    # -----------------------------
    if h.lower() == e.lower():
        return ("KO", "Case Error", 99, "Case diff")

    # -----------------------------
    # Similarité principale
    # -----------------------------
    sim = difflib.SequenceMatcher(
        None,
        h.lower(),
        e.lower()
    ).ratio()

    similarity = round(sim * 100)

    # Très proche = Typo
    if sim >= 0.95:
        return ("KO", "Typo", similarity, "Near exact")

    # Proche = Typo
    if sim >= 0.80:
        return ("KO", "Typo", similarity, "Fuzzy")

    # -----------------------------
    # Cas spécifique devises
    # -----------------------------
    hc = normalize_currency(html_text)
    ec = normalize_currency(excel_text)

    if hc and ec:

        if hc == ec:
            return ("OK", None, 100, "Currency exact")

        sim2 = difflib.SequenceMatcher(
            None,
            hc.lower(),
            ec.lower()
        ).ratio()

        similarity2 = round(sim2 * 100)

        if sim2 >= 0.95:
            return ("KO", "Typo", similarity2, "Currency typo")

    # -----------------------------
    # Tout le reste
    # -----------------------------
    return (
        "KO",
        "Text differs",
        similarity,
        "No match"
    )


# =========================
# EXTRACTION HTML
# =========================

import requests
from requests.auth import HTTPBasicAuth

def fetch_html_from_aem(link: str, username: str, password: str) -> str:
    try:
        response = requests.get(
            link,
            auth=HTTPBasicAuth(username, password),
            verify=False 
        )

        response.raise_for_status()

        return response.text

    except requests.exceptions.RequestException as e:
        raise ValueError(f"Erreur lors de la récupération AEM : {e}")
    
def save_html_to_temp(html_content: str, path: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)

def build_title_to_component_map(soup: BeautifulSoup) -> dict:
    """
    Construit la map { texte_normalisé_du_titre -> "audit-component-xxx" }

    Règles :
    - Si un titre est suivi directement d'un autre titre (ou est le dernier élément
      audit-* du document) → il appartient au composant PRÉCÉDENT dans la séquence.
    - Si un titre est suivi d'un audit-component-* → il appartient à ce composant SUIVANT.
    """
    all_elements = soup.find_all(True)

    # 1. Extraire la séquence ordonnée des éléments audit-* pertinents
    #    Chaque entrée : ("component", "audit-component-xxx") ou ("title", texte_normalisé)
    audit_sequence = []
    for el in all_elements:
        classes = el.get("class", [])
        for c in classes:
            if c.startswith("audit-component"):
                audit_sequence.append(("component", c))
                break
            elif c == "audit-generique-title":
                text_norm = normalize(el.get_text(" ", strip=True))
                if text_norm:
                    audit_sequence.append(("title", text_norm))
                break

    if DEBUG_MODE:
        print("\n🔢 Séquence audit-* détectée :")
        for kind, val in audit_sequence:
            label = "★ COMPOSANT" if kind == "component" else "  TITRE    "
            print(f"   {label} : {val[:70]}")

    # 2. Résoudre chaque titre vers son composant
    title_to_component = {}
    last_component = None

    for i, (kind, val) in enumerate(audit_sequence):
        if kind == "component":
            last_component = val
        elif kind == "title":
            # Regarder l'élément suivant dans la séquence
            next_kind = None
            next_val = None
            if i + 1 < len(audit_sequence):
                next_kind, next_val = audit_sequence[i + 1]

            if next_kind == "component":
                # Titre juste avant un composant → appartient au composant SUIVANT
                title_to_component[val] = next_val
            else:
                # Titre suivi d'un autre titre, ou dernier élément → composant PRÉCÉDENT
                title_to_component[val] = last_component

    if DEBUG_MODE:
        print("\n🗺️  Map titre → composant résolue :")
        for k, v in title_to_component.items():
            print(f"   '{k[:60]}' → {v}")

    return title_to_component


def extract_texts_from_textcontribution(soup: BeautifulSoup, title_to_component: dict) -> list:
    """
    Extraction depuis les <div class="text-contribution">.

    FIX DEDUPLICATION : suppression de la deduplication par inclusion.
    L'ancienne logique eliminait les elements enfants inclus dans un parent plus long
    (ex: <h3> inclus dans le texte du <a> parent). Cela causait des pertes
    d'elements qui sont pourtant des entrees distinctes dans l'Excel.
    On conserve maintenant TOUS les elements dans leur ordre d'apparition.
    Seule la deduplication par texte exact est maintenue (evite vrais doublons).
    """
    if DEBUG_MODE:
        print("\n🔎 Extraction depuis <div class='text-contribution'>…")

    results = []
    seen = set()

    blocks = soup.find_all(class_="text-contribution")

    if DEBUG_MODE:
        print(f"   → {len(blocks)} bloc(s) detecte(s)")

    for block in blocks:


        for el in block.find_all(True):
            if el.name.lower() not in {
                "h1", "h2", "h3", "p", "span", "a",
                "b", "strong", "em", "i", "small", "li"
            }:
                continue

            text = el.get_text(" ", strip=True)
            text = clean_text(text)


            if "Card Number" in text:
                print(
                    "CARD CHECK",
                    repr(text),
                    is_valid(text)
                )

            if not text or not is_valid(text):
                continue
            if text in seen:
                continue
            
            
            component_class = find_component_class(el)

            
            if (
                component_class == "audit-component-legal-mention"
                and el.name.lower() == "a"
            ):
                continue


            item = {
                "text": text,
                "tag": el.name.lower(),
                "type": get_type(el.name),
                "component_class": component_class
            }
            results.append(item)
            seen.add(text)
            if DEBUG_MODE:
                print(f"   ✓ [{item['type']}] {text[:90]}")

        # Fallback : texte nu du bloc
        block_text = block.get_text(" ", strip=True)
        block_text = clean_text(block_text)

        if block_text and is_valid(block_text) and block_text not in seen:
            item = {
                "text": block_text,
                "tag": "div",
                "type": "Text",
                "component_class": find_component_class(block)
            }
            results.append(item)
            seen.add(block_text)
            if DEBUG_MODE:
                print(f"   ✓ [Text] {block_text[:90]}")

        # --- Traitement des audit-generique-title ---
    titles = soup.find_all(class_="audit-generique-title")

    for title_tag in titles:
        for el in title_tag.find_all(True):  # h1, h2, span, etc. à l'intérieur
            if el.name.lower() not in {"h1", "h2", "h3", "p", "span", "b", "strong"}:
                continue

            text = clean_text(el.get_text(" ", strip=True))

            
            if "Card" in str(el):
                print("RAW EL =", repr(el.get_text(" ", strip=True)))
                print("CLEAN =", repr(text))


            if not text or not is_valid(text):
                continue
            if text in seen:
                continue

            # Résolution de la classe composant via la map
            text_norm = normalize(text)
            component_class = title_to_component.get(text_norm)

            item = {
                "text": text,
                "tag": el.name.lower(),
                "type": get_type(el.name),
                "component_class": component_class   # peut être None si non trouvé
            }


            if "Card" in text:
                print("FOUND CARD", text)


            results.append(item)
            seen.add(text)

            if DEBUG_MODE:
                print(f"   ✓ [TITRE/{item['type']}] {text[:80]} → {component_class}")

    if DEBUG_MODE:
        print(f"\n{'='*62}")
        print(f"✔ {len(results)} textes prets pour la comparaison")
        print(f"{'='*62}")

    return results





def load_and_extract_html_texts(html_path: str) -> list:
    with open(html_path, "r", encoding="utf-8") as f:
        raw = f.read()

    if is_view_source_document(raw):
        if DEBUG_MODE:
            print("🧩 Fichier 'view-source' AEM detecte → reconstruction du HTML reel…")
        raw = extract_real_html_from_viewsource(raw)

    cleaned = clean_template_code(raw)
    
    soup = BeautifulSoup(cleaned, "html.parser")


    for tag in soup(["script", "style", "meta", "link", "head"]):
        tag.decompose()

    title_to_component = build_title_to_component_map(soup)

    return extract_texts_from_textcontribution(soup, title_to_component)



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

EXCLUDED_QA_ROWS = {
    ("subject", "subject"),
    ("header", "loyalty program name"),
}

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


# =========================
# MATCHING
# =========================
def find_best_match(html_item: dict, excel_wordings: list, matched_excel_idx: set):
    """
    Cherche uniquement parmi les wordings Excel dont la classe technique
    correspond à la component_class de l'élément HTML.
    Si aucun wording de la bonne classe ne matche → None (sera traité comme KO).
    """
    found_class = html_item.get("component_class")
    h_text = html_item["text"]
    best = None

    def _scan(indices):
        nonlocal best
        for idx in indices:
            ex = excel_wordings[idx]

            # ── NOUVEAU : filtrage strict par classe ──────────────────────────
            # On ne compare que les wordings dont la classe attendue correspond
            # à la classe du composant HTML où le texte a été trouvé.
            if ex.get("Technical") != found_class:
                continue
            # ─────────────────────────────────────────────────────────────────

            status, error, sim, match_type = compare(h_text, ex["Wording"])

            if best is None or sim > best[3]:
                best = (idx, status, error, sim)

            if status == "OK":
                return (idx, status, error, sim)

    _scan(range(len(excel_wordings)))
    return best


def find_aggregated_match(html_items, start_idx, excel_wordings, matched_excel_idx, max_window=3):
    first_item = html_items[start_idx]
    first_norm = normalize(first_item["text"])
    if first_item["tag"] == "a" and len(first_norm) < 20:
        return None

    for window in range(2, max_window + 1):
        end = start_idx + window
        if end > len(html_items):
            break

        chunk_text = " ".join(item["text"] for item in html_items[start_idx:end])
        if len(chunk_text) < MIN_TEXT_LENGTH:
            continue

        # ── NOUVEAU : construire un pseudo html_item avec la classe du premier élément ──
        chunk_item = {
            "text": chunk_text,
            "tag": first_item["tag"],
            "type": first_item["type"],
            "component_class": first_item.get("component_class")  # classe portée par le 1er bloc
        }
        # ────────────────────────────────────────────────────────────────────────────────

        best = find_best_match(chunk_item, excel_wordings, matched_excel_idx)
        if best is not None:
            idx, status, error, sim = best
            target_len = len(normalize(excel_wordings[idx]["Wording"]))
            chunk_len = len(normalize(chunk_text))
            if target_len > 0 and chunk_len > target_len * 2.0:
                continue
            return (window,) + best

    return None


def find_split_match(h_text: str, excel_wordings: list, matched_excel_idx: set):
    """
    Matching par splitting : le texte HTML est une sous-phrase d'une cellule Excel.
    Ex: HTML = 'Get a gift ... before .'
        Excel = 'Get a gift ... before [gift validity end date]'
        Apres normalize(), Excel devient 'Get a gift ... before .'
        → egalite exacte, deja gere dans compare().

    Ce fallback couvre le cas ou la sous-phrase HTML est incluse dans l'Excel normalise.
    Ne marque PAS matched_excel_idx (d'autres fragments HTML peuvent couvrir la meme cellule).
    Retourne (idx, status, error, sim) ou None.
    """
    h_norm = normalize(h_text)
    if not h_norm or len(h_norm) < MIN_TEXT_LENGTH:
        return None

    for idx, ex in enumerate(excel_wordings):
        if idx in matched_excel_idx:
            continue
        e_norm = ex["norm"]
        if not e_norm:
            continue
        ratio = len(h_norm) / len(e_norm) if len(e_norm) > 0 else 0
        if h_norm.lower() in e_norm.lower() and ratio >= 0.30:
            return (idx, "OK", "", round(ratio, 2))

    return None


def find_component_class(tag):
    """
    Remonte dans les parents pour trouver une classe audit-component
    """
    parent = tag

    while parent:
        if hasattr(parent, "attrs"):
            classes = parent.get("class", [])
            for c in classes:
                if c.startswith("audit-component"):
                    return c
        parent = parent.parent

    return None


# =========================
# PIPELINE PRINCIPAL
# =========================
def find_closest_match(html_item: dict, excel_wordings: list, matched_excel_idx: set,
                       min_sim: float = 0.55):
    """
    Cherche le wording Excel le plus similaire au texte HTML, meme en dessous du
    seuil OK. Utilise pour les KO 'Wording differs' afin d'afficher le wording
    attendu en face du wording HTML incorrect.

    - Cherche d'abord via normalize() standard
    - Puis via normalize_currency() en fallback
    - Retourne (idx, sim) si la meilleure similarite >= min_sim, sinon None.
    """
    
    h_text = html_item["text"]
    found_class = html_item.get("component_class")

    h_norm = normalize(h_text)
    h_curr = normalize_currency(h_text)
    if not h_norm:
        return None

    best_idx = None
    best_sim = 0.0

    for idx, ex in enumerate(excel_wordings):

        
        expected_class = ex.get("Technical")

        if found_class and expected_class:
            if found_class != expected_class:
                continue


        # On inclut les cellules deja matchees : le "Not in HTML" peut pointer
        # vers une cellule deja matchee par un autre element HTML.
        e_norm = ex["norm"]
        e_curr = ex["curr"]

        sim = difflib.SequenceMatcher(None, h_norm.lower(), e_norm.lower()).ratio()

        # Containment partiel (meme ratio relache)
        if len(h_norm) >= 8 and len(e_norm) >= 8:
            ratio = min(len(h_norm), len(e_norm)) / max(len(h_norm), len(e_norm))
            if ratio >= 0.25 and (h_norm.lower() in e_norm.lower() or
                                   e_norm.lower() in h_norm.lower()):
                sim = max(sim, 0.70)

        # Fallback devise
        if e_curr and h_curr:
            sim2 = difflib.SequenceMatcher(None, h_curr.lower(), e_curr.lower()).ratio()
            sim = max(sim, sim2)

        if sim > best_sim:
            best_sim = sim
            best_idx = idx

    if best_idx is not None and best_sim >= min_sim:
        return {
            "idx": best_idx,
            "similarity": best_sim,
            "class_match": True
        }

    return None


def _make_row(ex, html_item, html_text, status, error, sim, match_type):
    crit = "" if status == "OK" else ("Medium" if error == "Typo" else "High")
    expected_class = ex.get("Technical")
    found_class = html_item.get("component_class")

    return {
        "Component Name": ex["Component"],
        "Element": ex["Element"],
        "Expected Class": expected_class,
        "HTML Class": found_class,
        "Excel Wording": ex["Wording"],
        "HTML Wording": html_text,
        "Status": status,
        "Error Type": error,
        "Similarity": f"{round(sim, 2)}%",
        "Criticality": crit,
        "Match Type": match_type,
        "Occurrences": ""  # placeholder
    }


def run_wording_qa(html_texts, excel_wordings):
    results = []
    
    from collections import defaultdict

    matched_excel_counts = defaultdict(int)

    html_consumed = set()


    i = 0
    while i < len(html_texts):
        if i in html_consumed:
            i += 1
            continue

        item = html_texts[i]
        h_text = item["text"]

        
        expected_class = ""
        found_class = item.get("component_class", "")
        status = "KO"
        criticality = "High"


        # A) Matching direct
        match = find_best_match(item, excel_wordings, set(matched_excel_counts.keys()))
        if match:
            idx, status, error, sim = match
            results.append(_make_row(excel_wordings[idx], item, h_text,
                                     status, error, sim, "Direct"))
            matched_excel_counts[idx] += 1
            i += 1
            continue

        # B) Agregation (N HTML → 1 Excel)
        agg = find_aggregated_match(html_texts, i, excel_wordings, matched_excel_counts)
        if agg:
            nb, idx, status, error, sim = agg
            agg_text = " ".join(html_texts[i + k]["text"] for k in range(nb))
            agg_types = "/".join(dict.fromkeys(html_texts[i+k]["type"] for k in range(nb)))      
            found_class = item.get("component_class")
            results.append(_make_row(excel_wordings[idx], found_class, agg_text,
                                     status, error, sim, f"Agrege ({nb} blocs)"))
            matched_excel_counts[idx] += 1
            for k in range(nb):
                html_consumed.add(i + k)
            i += nb
            continue

        # C) Split (HTML ⊂ Excel)
        split = find_split_match(h_text, excel_wordings, matched_excel_counts)
        if split:
            idx, status, error, sim = split
            results.append(_make_row(excel_wordings[idx], item, h_text,
                                     "OK", "", sim, "Split (HTML ⊂ Excel)"))
            i += 1
            continue

        # D) Aucun match exact — chercher le wording Excel le plus proche
        closest = find_closest_match(item, excel_wordings, matched_excel_counts)
        if closest:
            
            c_idx = closest["idx"]
            c_sim = closest["similarity"]
            class_match = closest["class_match"]

            ex_closest = excel_wordings[c_idx]
            
            expected_class = ex_closest.get("Technical")
            found_class = item.get("component_class")

            class_match = expected_class == found_class
            
            if not class_match:
                status = "KO"
                error_type = "Wording in wrong component"

            elif c_sim >= 0.85:
                status = "OK"
                error_type = ""

            else:
                status = "KO"
                error_type = "Wording differs"

            
            criticality = (
                ""
                if status == "OK"
                else "High"
            )

            
            if status == "OK":
                match_type = "Closest Match Accepted"
            else:
                match_type = "Closest Match"

            results.append({
                "Component Name": ex_closest["Component"],
                "Element": ex_closest["Element"],
                "Expected Class": expected_class,
                "HTML Class": found_class,
                "Excel Wording": ex_closest["Wording"],
                "HTML Wording": h_text,
                "Status": status,
                "Error Type": error_type,
                "Similarity": f"{round(c_sim * 100)}%",
                "Criticality": criticality,
                "Match Type": match_type
            })
        else:
            results.append({
                "Component Name": "",
                "Element": "",            
                "Expected Class": expected_class or "",
                "HTML Class": found_class or "",
                "Excel Wording": "",
                "HTML Wording": h_text,
                "Status": status,
                "Error Type": "Not in Excel",
                "Similarity": "0%",
                "Criticality": criticality,
                "Match Type": "No match"
            })
        i += 1

    # Excel non couverts : chercher le texte HTML le plus proche pour chaque
    for idx, ex in enumerate(excel_wordings):
        if idx not in matched_excel_counts:
            # Chercher le texte HTML le plus proche (parmi tous, meme deja consommes)     
            e_norm = ex["norm"]
            e_curr = ex["curr"]
            best_h_idx = None
            best_h_sim = 0.0
            for hi, html_item in enumerate(html_texts):
                h_norm = normalize(html_item["text"])
                h_curr = normalize_currency(html_item["text"])
                sim = difflib.SequenceMatcher(None, e_norm.lower(), h_norm.lower()).ratio()
                if len(h_norm) >= 8 and len(e_norm) >= 8:
                    ratio = min(len(h_norm), len(e_norm)) / max(len(h_norm), len(e_norm))
                    if ratio >= 0.25 and (h_norm.lower() in e_norm.lower() or
                                           e_norm.lower() in h_norm.lower()):
                        sim = max(sim, 0.70)
                if e_curr and h_curr:
                    sim2 = difflib.SequenceMatcher(None, e_curr.lower(), h_curr.lower()).ratio()
                    sim = max(sim, sim2)
                if sim > best_h_sim:
                    best_h_sim = sim
                    best_h_idx = hi

            if best_h_idx is not None and best_h_sim >= 0.55:
                closest_html = html_texts[best_h_idx]
                results.append({
                    "Component Name": ex["Component"],
                    "Element": ex["Element"],
                    "Excel Wording": ex["Wording"],
                    "HTML Wording": closest_html["text"],
                    "Status": "KO",
                    "Error Type": "Wording differs",
                    "Similarity": f"{round(best_h_sim * 100)}%",
                    "Criticality": "High",
                    "Match Type": "Closest match"
                })
            else:
                results.append({
                    "Component Name": ex["Component"],
                    "Element": ex["Element"],
                    "Excel Wording": ex["Wording"],
                    "HTML Wording": "",
                    "Status": "KO",
                    "Error Type": "Not in HTML",
                    "Similarity": "0%",
                    "Criticality": "Low",
                    "Match Type": "No match"
                })

    # Deduplication des KO : supprimer les doublons HTML+Excel identiques
    # (peut arriver quand un HTML non matche et un Excel non matche pointent l'un vers l'autre)
    seen_pairs = set()
    deduped = []
    for r in results:
        key = (str(r.get("HTML Wording", "")), str(r.get("Excel Wording", "")))
        if r["Status"] == "KO" and key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(r)
    results = deduped


    total = len(results)
    ok = sum(1 for r in results if r["Status"] == "OK")

    
    


    for r in results:
        for idx, count in matched_excel_counts.items():
            if r["Excel Wording"] == excel_wordings[idx]["Wording"]:
                r["Occurrences"] = count


    print("\n" + "=" * 70)
    print(f"✅ Total: {total} | OK: {ok} | KO: {total-ok} | "
          f"Score: {round((ok/total)*100, 2) if total else 0}%")
    print("=" * 70)
    return results


def run_url_qa(html_texts, excel_wordings):
    raise NotImplementedError("URL QA not implemented yet")

def run_qa(html_file=None, excel_file=None, keyword="", aem_url=None, username=None, password=None, mode="wording"):
    
    # ✅ Si URL AEM fournie → priorité
    if aem_url:

        html_content = fetch_html_from_aem(aem_url, username, password)

        os.makedirs("temp", exist_ok=True)
        html_file = "temp/aem_input.html"

        save_html_to_temp(html_content, html_file)


    print("\n" + "=" * 70)
    print("🔎 QA EMAIL - HTML → Excel (text-contribution)")
    print("=" * 70)

    # 1. Charger HTML
    html_texts = load_and_extract_html_texts(html_file)

    # 2. Charger Excel
    sheet, excel_wordings = load_excel_wordings(excel_file, keyword)

    # 3. Choisir le mode
    if mode == "wording":
        results = run_wording_qa(html_texts, excel_wordings)

    elif mode == "url":
        results = run_url_qa(html_texts, excel_wordings)

    else:
        raise ValueError("Mode inconnu")

    # 4. Générer le rapport
    report_path = save_report(results, sheet)

    # ✅ IMPORTANT : retourner quelque chose
    return results, report_path, sheet




# =========================
# RAPPORT EXCEL
# =========================
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
                cell = ws_target.cell(r, c, safe_val)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                col_name = columns[c - 1]
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


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    try:
        run_qa(HTML_FILE, EXCEL_FILE, CAMPAIGN_KEYWORD)
    except Exception as e:
        print(f"\n❌ Erreur inattendue : {e}")
        import traceback
        traceback.print_exc()