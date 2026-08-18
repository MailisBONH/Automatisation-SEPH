import html

from bs4 import BeautifulSoup

from qa.config import DEBUG_MODE, LEGAL_MENTION_CLASS_KEYWORDS, MIN_TEXT_LENGTH
from qa.text_utils import clean_text, clean_text_preserving_conditionals, get_type, is_valid, normalize


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
    soup = BeautifulSoup(raw, "lxml")
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

    if DEBUG_MODE and title_to_component:
        print(f"🗺️  {len(title_to_component)} titre(s) générique(s) résolu(s) vers un composant")

    return title_to_component


_EXTRACTABLE_TAGS = ("h1", "h2", "h3", "p", "span", "a", "b", "strong", "em", "i", "small", "li")
# Tags de pure emphase visuelle (gras/italique/petit texte) : jamais une
# entree Excel distincte en pratique, a la difference d'un <h3> imbrique
# dans un <a> (titre + CTA = 2 roles semantiques differents, cf. plus bas).
_INLINE_EMPHASIS_TAGS = {"b", "strong", "em", "i", "small"}


def _has_extractable_ancestor_within(el, boundary) -> bool:
    """Vrai si el a un ancetre parmi _EXTRACTABLE_TAGS strictement entre lui
    et boundary (exclu) - borne la remontee au bloc text-contribution
    courant, pour ne jamais matcher un ancetre sans rapport plus haut dans
    la page (ex: un lien englobant tout un composant)."""
    parent = el.parent
    while parent is not None and parent is not boundary:
        if getattr(parent, "name", None) and parent.name.lower() in _EXTRACTABLE_TAGS:
            return True
        parent = parent.parent
    return False


def extract_texts_from_textcontribution(soup: BeautifulSoup, title_to_component: dict) -> list:
    """
    Extraction depuis les <div class="text-contribution">.

    FIX DEDUPLICATION : suppression de la deduplication par inclusion.
    L'ancienne logique eliminait les elements enfants inclus dans un parent plus long
    (ex: <h3> inclus dans le texte du <a> parent). Cela causait des pertes
    d'elements qui sont pourtant des entrees distinctes dans l'Excel.
    On conserve maintenant TOUS les elements dans leur ordre d'apparition,
    SAUF les tags de pure emphase (_INLINE_EMPHASIS_TAGS) imbriques dans un
    autre element deja extractible (ex: <b> a l'interieur d'un <p>) : leur
    contenu est deja entierement couvert par le texte complet de cet
    ancetre, les extraire separement ne fait que dupliquer la comparaison
    contre le meme wording Excel (ou, pire, faire matcher a tort ce court
    fragment isole contre une cellule Excel totalement sans rapport).
    Seule la deduplication par texte exact est maintenue par ailleurs (evite
    vrais doublons).
    """
    results = []
    seen = set()

    blocks = soup.find_all(class_="text-contribution")

    if DEBUG_MODE:
        print(f"🔎 {len(blocks)} bloc(s) 'text-contribution' détecté(s)")

    for block in blocks:

        block_child_texts = []

        for el in block.find_all(True):
            if el.name.lower() not in _EXTRACTABLE_TAGS:
                continue

            if el.name.lower() in _INLINE_EMPHASIS_TAGS and _has_extractable_ancestor_within(el, block):
                continue

            raw_text = el.get_text(" ", strip=True)
            # Le texte stocke/compare garde les blocs <% if %> intacts (voir
            # expand_conditional_variants dans qa/matching.py) ; la
            # verification de validite, elle, se fait sur la version
            # completement nettoyee (sinon un fragment "Dir fehlen noch
            # <% if... %>" serait juge trop "technique" et rejete).
            text = clean_text_preserving_conditionals(raw_text)
            check_text = clean_text(raw_text)

            if not check_text or not is_valid(check_text):
                continue
            # Ignore les liens AEM techniques
            if text.strip().lower() == "edit":
                continue

            if text in seen:
                continue


            component_class = find_component_class(el)
            if component_class is None:
                # Aucun ancetre audit-component : el est peut-etre a
                # l'interieur d'un titre generique (audit-generique-title),
                # dont le composant se resout via la sequence DOM plutot
                # qu'une classe directe (voir build_title_to_component_map).
                # Fait ici, AVANT d'ajouter le texte a `seen`, sinon la
                # boucle "Traitement des audit-generique-title" plus bas
                # trouverait ce texte deja vu et ne le retraiterait jamais -
                # le laissant resolu a None pour toujours.
                component_class = find_generic_title_class(el, title_to_component)


            # Ignore les liens individuels des mentions legales/footer : leur
            # texte est deja couvert par le paragraphe complet qui les
            # englobe (extrait separement via le fallback texte-brut-du-bloc).
            if (
                el.name.lower() == "a"
                and component_class
                and any(kw in component_class.lower() for kw in LEGAL_MENTION_CLASS_KEYWORDS)
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
            block_child_texts.append(text)

        # Fallback : texte nu du bloc. Ne contribue que si son contenu n'est
        # pas deja entierement couvert par les elements du bloc extraits
        # ci-dessus (ex: 2 <p> voisins, chacun deja son propre item ; leur
        # simple concatenation ne doit pas redevenir un 3e "wording" a
        # comparer, ce qui produirait un doublon du meme ecart).
        raw_block_text = block.get_text(" ", strip=True)
        block_text = clean_text_preserving_conditionals(raw_block_text)
        block_check_text = clean_text(raw_block_text)

        if block_text.strip().lower() == "edit":
            continue

        remaining = normalize(block_text)
        for child_text in block_child_texts:
            remaining = remaining.replace(normalize(child_text), " ")
        remaining = " ".join(remaining.split())
        fully_covered_by_children = len(remaining) < MIN_TEXT_LENGTH

        if (
            block_text
            and is_valid(block_check_text)
            and block_text not in seen
            and not fully_covered_by_children
        ):
            item = {
                "text": block_text,
                "tag": "div",
                "type": "Text",
                "component_class": find_component_class(block) or find_generic_title_class(block, title_to_component)
            }
            results.append(item)
            seen.add(block_text)

        # --- Traitement des audit-generique-title ---
    titles = soup.find_all(class_="audit-generique-title")

    for title_tag in titles:
        # Cas standard : le titre est un wrapper (div, td, ...) contenant un
        # ou plusieurs h1/h2/span/etc. Cas leaf : la classe
        # audit-generique-title est posee directement sur le tag de texte
        # (ex. <h2 class="audit-generique-title">...</h2>, sans wrapper) -
        # title_tag.find_all(True) ne renvoie alors AUCUN descendant (il ne
        # s'inclut jamais lui-meme), et ce titre ne serait jamais traite.
        # On retombe sur title_tag lui-meme dans ce cas.
        descendants = [
            el for el in title_tag.find_all(True)
            if el.name.lower() in {"h1", "h2", "h3", "p", "span", "b", "strong"}
        ]
        candidates = descendants if descendants else [title_tag]

        for el in candidates:
            if el.name.lower() not in {"h1", "h2", "h3", "p", "span", "b", "strong"}:
                continue

            raw_title_text = el.get_text(" ", strip=True)
            text = clean_text_preserving_conditionals(raw_title_text)
            check_text = clean_text(raw_title_text)

            if not check_text or not is_valid(check_text):
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
        print(f"✔ {len(results)} textes prêts pour la comparaison")

    return results


def load_raw_html(html_path: str) -> str:
    """
    Lit le fichier HTML sur disque et reconstruit le HTML reel si le fichier
    fourni est une vue 'view-source' AEM. Utilise a la fois par l'extraction
    de wording (apres nettoyage) et par la QA d'URL (sur le HTML brut).
    """
    with open(html_path, "r", encoding="utf-8") as f:
        raw = f.read()

    if is_view_source_document(raw):
        if DEBUG_MODE:
            print("🧩 Fichier 'view-source' AEM detecte → reconstruction du HTML reel…")
        raw = extract_real_html_from_viewsource(raw)

    return raw


def load_and_extract_html_texts(html_path: str) -> list:
    # Pas de nettoyage AEM avant le parsing : BeautifulSoup traite les blocs
    # <% if %>/<%= %> comme du texte litteral (ce ne sont pas des tags HTML
    # valides), donc les stripper ici detruirait la structure if/else avant
    # meme que le DOM existe - avant expand_conditional_variants ait sa
    # chance de la voir a la comparaison (voir qa/matching.py).
    #
    # Parser "lxml" (pas "html.parser") : implemente les regles de fermeture
    # implicite du HTML5 (ex: un <p> imbrique dans un autre <p> referme
    # automatiquement le premier, comme le ferait un vrai navigateur/AEM).
    # "html.parser" garde la structure litteralement telle qu'ecrite dans le
    # HTML source, meme invalide - un <p>A<p>B</p></p> y devient un <p>
    # contenant un autre <p>, dont le get_text() combine A et B en un seul
    # texte fantome qui ne correspond a rien de reellement rendu.
    raw = load_raw_html(html_path)

    soup = BeautifulSoup(raw, "lxml")


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


def find_generic_title_class(tag, title_to_component: dict):
    """
    Remonte dans les parents (comme find_component_class) a la recherche
    d'un ancetre - ou tag lui-meme - portant la classe audit-generique-title,
    puis resout sa classe composant via title_to_component (voir
    build_title_to_component_map). La cle utilisee est le texte COMPLET de
    cet ancetre (get_text sur l'element qui porte la classe), exactement
    comme la map a ete construite - jamais le texte partiel d'un descendant
    isole, qui ne correspondrait a aucune entree de la map.
    """
    parent = tag

    while parent:
        if hasattr(parent, "attrs"):
            classes = parent.get("class", [])
            if "audit-generique-title" in classes:
                text_norm = normalize(parent.get_text(" ", strip=True))
                return title_to_component.get(text_norm)
        parent = parent.parent

    return None


def extract_anchor_links(raw_html: str) -> list:
    """
    Extrait chaque lien <a href="..."> du HTML brut avec la classe du
    composant englobant (audit-component-xxx, via find_component_class),
    pour la QA URL (voir qa.url_qa.run_url_qa).

    Les liens mailto: sont ignores : ce sont des adresses de contact/support,
    jamais des URL marketing destinees a etre trackees dans le referentiel
    UTM - les inclure ferait ressortir chaque lien mailto comme "Unexpected
    URL (not in Excel)" a chaque rapport, sans aucune valeur.
    """
    soup = BeautifulSoup(raw_html or "", "lxml")

    return [
        {"href": a["href"], "component_class": find_component_class(a) or ""}
        for a in soup.find_all("a", href=True)
        if not a["href"].strip().lower().startswith("mailto:")
    ]
