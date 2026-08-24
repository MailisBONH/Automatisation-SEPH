import html
import urllib.parse

from qa.config import URL_IGNORE_QUERY_DOMAINS


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

    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        # URL malformee (ex: IPv6 invalide dans le netloc) - urlparse()
        # lui-meme echoue, comme deja gere dans is_query_ignored_domain().
        # Retourne le meilleur repli disponible (decode/nettoye, non
        # reconstruit) plutot que de faire planter tout le run pour un seul
        # lien casse dans le HTML : deux occurrences du meme lien malforme
        # produisent quand meme la meme chaine, suffisant pour comparaison/
        # dedoublonnage.
        return url

    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    sorted_params = sorted(params)

    rebuilt_query = urllib.parse.urlencode(sorted_params)

    if rebuilt_query:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{rebuilt_query}"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def is_query_ignored_domain(url: str) -> bool:
    """
    True si le domaine de url fait partie de URL_IGNORE_QUERY_DOMAINS (voir
    qa/config.py) : pour ces domaines (ex. homeios.onelink.me), le lien
    court lui-meme contient deja le tracking/attribution (encode par le
    service dans le chemin, ex. le token apres /oUpm/...) - une eventuelle
    query string ajoutee en plus est generee dynamiquement a l'envoi et
    jamais figee a l'identique dans le referentiel Excel. La comparer
    n'aurait aucune valeur diagnostique : seule l'URL de base (chemin)
    compte, voir strip_query().
    """
    if not url:
        return False
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return False
    if not netloc:
        return False
    return any(netloc == d or netloc.endswith("." + d) for d in URL_IGNORE_QUERY_DOMAINS)


def strip_query(url: str) -> str:
    """
    URL de base (schema + domaine + chemin), sans sa query string du tout -
    contrairement a normalize_url() qui la conserve (juste retriee/decodee).
    Utilisee uniquement pour les domaines de URL_IGNORE_QUERY_DOMAINS, ou
    les parametres de tracking ne sont pas comparables (voir
    is_query_ignored_domain()).
    """
    if not url:
        return ""

    url = html.unescape(url)
    url = urllib.parse.unquote(url)
    url = url.strip()

    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return url
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
