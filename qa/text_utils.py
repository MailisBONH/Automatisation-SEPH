import difflib
import re
import html

from qa.config import AEM_PATTERN, VAR_PATTERN, MIN_TEXT_LENGTH


def clean_text(text: str) -> str:
    if not text:
        return ""

    text = html.unescape(text)
    text = AEM_PATTERN.sub("", text)
    text = VAR_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def clean_text_preserving_conditionals(text: str) -> str:
    """
    Comme clean_text(), mais NE retire PAS les blocs <% if %>/<%= %> : ils
    doivent survivre jusqu'a la comparaison pour que
    expand_conditional_variants() puisse les interpreter branche par
    branche (voir qa/matching.py). Utilisee a l'extraction HTML pour le
    texte reellement stocke/compare ; clean_text() reste utilisee en
    parallele uniquement pour la verification de validite (is_valid), qui a
    besoin du texte completement nettoye pour juger s'il s'agit de contenu
    reel plutot que de code technique.
    """
    if not text:
        return ""

    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


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
        r"^<", r"^/\*", r"^http", r"^mailto:", r"^/content/",
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
    # Placeholders entre crochets (ex: [Name], [gift validity end date]) :
    # un seul passage suffit, \[.*?\] retire deja n'importe quel contenu
    # entre crochets, quel qu'il soit.
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"\bXXX\b", "", s)
    s = re.sub(r"Version \d[^:]*:", "", s, flags=re.IGNORECASE)

    # FIX mots AEM colles : 'pointspoint' -> 'points point'

    s = re.sub(r"(points?)(points?)", r"\1 \2", s)
    s = re.sub(r"\b(gifts)(gift)\b", r"\1 \2", s, flags=re.IGNORECASE)


    s = re.sub(r"\s+([.,])", r"\1", s)


    s = " ".join(s.split())


    # HTML
    s = re.sub(r"(?i)<br\s*/?>", " ", s)
    s = html.unescape(s)

    # Caracteres invisibles
    s = s.replace("‎", "").replace("‏", "").replace("\xa0", " ")
    s = " ".join(s.split())
    return s.strip()


_IF_OPEN_PATTERN = re.compile(r"<%\s*if\s*\([^{]*?\)\s*\{\s*%>", re.IGNORECASE)
_IF_ELSE_TOKEN_PATTERN = re.compile(r"<%\s*\}\s*else\s*\{\s*%>", re.IGNORECASE)
_IF_CLOSE_TOKEN_PATTERN = re.compile(r"<%\s*\}\s*%>", re.IGNORECASE)
# Alternation des 3 marqueurs ci-dessus, pour parcourir sequentiellement les
# tokens qui suivent un "<% if %>" - voir _find_conditional_block. Les 3
# patterns divergent tous des le caractere suivant "<%\s*", donc l'ordre des
# branches ne cree aucune ambiguite (jamais deux alternatives ne matchent a
# la meme position).
_CONDITIONAL_TOKEN_PATTERN = re.compile(
    "(?P<OPEN>{open})|(?P<ELSE>{else_})|(?P<CLOSE>{close})".format(
        open=_IF_OPEN_PATTERN.pattern, else_=_IF_ELSE_TOKEN_PATTERN.pattern, close=_IF_CLOSE_TOKEN_PATTERN.pattern
    ),
    re.DOTALL | re.IGNORECASE,
)


def _find_conditional_block(text: str):
    """
    Trouve le PREMIER bloc <% if(...) {%> ... <% } [else {%> ...] %> de
    `text`, en respectant l'imbrication : un if/else imbrique DANS la
    branche if (ou else) du bloc englobant n'est plus confondu avec le
    else/la fermeture de ce bloc englobant (voir le "depth" ci-dessous).

    Retourne (start, if_body, else_body_ou_None, end) - end etant la
    position juste apres la fermeture du bloc TROUVE (pas celle d'un bloc
    imbrique) - ou None si aucun bloc bien forme n'est trouve. Le corps
    d'un bloc imbrique n'est PAS lui-meme resolu ici : expand_conditional_variants
    le retrouvera comme bloc a part entiere au round suivant, une fois la
    substitution de CE bloc appliquee.
    """
    m_open = _IF_OPEN_PATTERN.search(text)
    if not m_open:
        return None

    depth = 0
    if_end = None
    else_start = None
    pos = m_open.end()

    while pos <= len(text):
        m = _CONDITIONAL_TOKEN_PATTERN.search(text, pos)
        if not m:
            return None  # bloc mal forme (fermeture manquante) - laisse tel quel

        kind = m.lastgroup
        if kind == "OPEN":
            depth += 1
        elif kind == "ELSE" and depth == 0 and if_end is None:
            if_end = m.start()
            else_start = m.end()
        elif kind == "CLOSE" and depth == 0:
            if if_end is None:
                return (m_open.start(), text[m_open.end():m.start()], None, m.end())
            return (m_open.start(), text[m_open.end():if_end], text[else_start:m.start()], m.end())
        elif kind == "CLOSE":
            depth -= 1
        # Un ELSE rencontre a depth > 0 (ou un second ELSE au meme niveau,
        # improbable en AEM valide) appartient a un bloc imbrique : ignore
        # pour le comptage de profondeur, il sera retrouve comme faisant
        # partie du corps du bloc imbrique.
        pos = m.end()

    return None


def expand_conditional_variants(text: str, max_variants: int = 8) -> list:
    """
    Deplie les blocs conditionnels AEM/Adobe Campaign
    (<% if(...) { %> A <% } else { %> B <% } %>, ou sans else) en une liste
    de variantes de texte brut, une par branche possible.

    Avant cette fonction, normalize() se contentait de supprimer les tags
    <% %> sans comprendre la structure if/else : les deux branches se
    retrouvaient concatenees en une seule phrase incoherente (ex.
    'Dir fehlen noch Punkte' + 'Dir fehlt noch Punkt' -> un texte qui ne
    matche ni l'une ni l'autre). Ici, chaque branche devient sa propre
    variante ; le rendu final n'affichera qu'une seule branche a la fois,
    donc matcher N'IMPORTE LAQUELLE des variantes doit compter comme un
    match. Gere aussi bien les conditions sequentielles (plusieurs blocs
    l'un apres l'autre) que VRAIMENT imbriquees (un if/else a l'interieur
    d'une branche d'un autre) - voir _find_conditional_block - via une
    nouvelle passe de resolution par round (jusqu'a 6 rounds) : chaque round
    ne resout que le bloc le plus EXTERIEUR de chaque variante, le bloc
    imbrique restant dans le corps substitue est retrouve comme bloc a part
    entiere au round suivant.

    Retourne [text] (liste a un seul element) si aucun bloc conditionnel
    n'est trouve - le cas courant, sans aucun cout supplementaire.
    """
    if not text or "<%" not in text:
        return [text or ""]

    variants = [text]
    for _ in range(6):
        next_variants = []
        any_resolved = False
        for v in variants:
            block = _find_conditional_block(v)
            if not block:
                next_variants.append(v)
                continue
            any_resolved = True
            start, if_body, else_body, end = block
            next_variants.append(v[:start] + if_body + v[end:])
            next_variants.append(v[:start] + (else_body if else_body is not None else "") + v[end:])
        variants = next_variants[:max_variants]
        if not any_resolved:
            break

    deduped = []
    for v in variants:
        if v not in deduped:
            deduped.append(v)
    return deduped


def clean_display_text(raw_text: str, reference_text: str = None) -> str:
    """
    Version affichable d'un texte extrait, sans aucune trace de code AEM
    (variables <%= %>, blocs <% if %>...) : utilisee pour les colonnes
    "HTML Wording"/"Excel Wording" des rapports. La comparaison elle-meme
    (qa.matching.compare) continue de se faire sur le texte brut - ce
    nettoyage est purement cosmetique, pour ne jamais montrer de code a
    l'utilisateur.

    Si raw_text contient un bloc conditionnel, plusieurs branches sont
    possibles : la branche la plus proche de reference_text est choisie
    (ex: le wording Excel matche), plutot que de toutes les concatener en
    une phrase incoherente. Sans reference_text, la 1ere branche est prise.
    """
    variants = expand_conditional_variants(raw_text)
    if len(variants) == 1 or not reference_text:
        return clean_text(variants[0])

    ref_norm = normalize(reference_text).lower()
    best = variants[0]
    best_sim = -1.0
    for v in variants:
        sim = difflib.SequenceMatcher(None, normalize(v).lower(), ref_norm).ratio()
        if sim > best_sim:
            best_sim = sim
            best = v
    return clean_text(best)


def similarity_ratio(a: str, b: str, case_sensitive: bool = False) -> float:
    """
    Similarite caractere par caractere entre deux textes (0.0-1.0), via
    difflib.SequenceMatcher - factorise ici pour eviter que qa.matching
    (comparaison principale + _similarity_with_boosts) et qa.url_qa
    (_char_similarity) redefinissent chacun la meme logique (minuscules +
    cas particulier des chaines vides).

    Insensible a la casse par defaut (les deux textes sont mis en
    minuscules) : c'est le comportement de TOUS les appels existants
    remplaces par cette fonction. Une difference de casse pure est deja
    detectee separement et plus tot dans qa.matching.compare (KO_CASE_MISMATCH,
    sur les textes normalises avant meme d'atteindre ce calcul de
    similarite) - passer case_sensitive=True reste possible pour un futur
    appelant qui aurait besoin de preserver la casse ici meme.
    """
    a, b = str(a or ""), str(b or "")
    if not case_sensitive:
        a, b = a.lower(), b.lower()
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


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
