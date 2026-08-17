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
    s = re.sub(r"\[.*?\]", "", s)


    # Placeholders metier PO
    s = re.sub(r"\[Name\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[[^\]]*\]", "", s)
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


_IF_ELSE_PATTERN = re.compile(
    r"<%\s*if\s*\([^{]*?\)\s*\{\s*%>(.*?)<%\s*\}\s*else\s*\{\s*%>(.*?)<%\s*\}\s*%>",
    re.DOTALL | re.IGNORECASE,
)
_IF_ONLY_PATTERN = re.compile(
    r"<%\s*if\s*\([^{]*?\)\s*\{\s*%>(.*?)<%\s*\}\s*%>",
    re.DOTALL | re.IGNORECASE,
)


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
    match. Gere aussi les conditions sequentielles/imbriquees dans un meme
    texte (une nouvelle passe de resolution par round, jusqu'a 6 rounds).

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
            m = _IF_ELSE_PATTERN.search(v)
            if m:
                any_resolved = True
                next_variants.append(v[:m.start()] + m.group(1) + v[m.end():])
                next_variants.append(v[:m.start()] + m.group(2) + v[m.end():])
                continue
            m = _IF_ONLY_PATTERN.search(v)
            if m:
                any_resolved = True
                next_variants.append(v[:m.start()] + m.group(1) + v[m.end():])
                next_variants.append(v[:m.start()] + v[m.end():])
                continue
            next_variants.append(v)
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
