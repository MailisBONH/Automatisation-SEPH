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


def clean_template_code(content: str) -> str:
    content = clean_text(content)
    return content


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
