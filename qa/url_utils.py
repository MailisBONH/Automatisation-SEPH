import html
import re
import urllib.parse

HREF_PATTERN = re.compile(r'href=["\'](.*?)["\']', re.IGNORECASE)


def extract_hrefs(html_content: str) -> list:
    if not html_content:
        return []
    return HREF_PATTERN.findall(html_content)


def normalize_url(url: str) -> str:
    """
    Normalisation fonctionnelle d'une URL : decode les entites HTML et le
    pourcent-encodage, puis retrie les parametres de query. Deux URLs
    normalisees egales pointent vers la meme destination reelle, meme si
    l'ordre ou l'encodage des parametres UTM differe.
    """
    if not url:
        return ""

    url = html.unescape(url)
    url = urllib.parse.unquote(url)
    url = url.strip()

    parsed = urllib.parse.urlparse(url)

    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    sorted_params = sorted(params)

    rebuilt_query = urllib.parse.urlencode(sorted_params)

    if rebuilt_query:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{rebuilt_query}"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
