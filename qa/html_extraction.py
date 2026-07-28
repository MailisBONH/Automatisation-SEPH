import html

import requests
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup

from qa.config import DEBUG_MODE
from qa.text_utils import clean_text, clean_template_code, get_type, is_valid, normalize


# =========================
# RECONSTRUCTION VIEW-SOURCE
# =========================
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
# EXTRACTION HTML
# =========================
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

            if not text or not is_valid(text):
                continue
            # Ignore les liens AEM techniques
            if text.strip().lower() == "edit":
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

        if block_text.strip().lower() == "edit":
            continue

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
