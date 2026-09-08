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

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"

    # URL sans schema ET sans "//" (ex. une faute de frappe dans le
    # referentiel Excel comme "www.sephora.com/page?utm=a", ou un lien
    # protocol-relatif ecrit sans les "//") : urlparse() met alors tout le
    # domaine+chemin dans .path avec un netloc vide, ce qui produirait plus
    # bas une chaine invalide "://www.sephora.com/page" ne correspondant
    # jamais a la meme URL correctement schemee comparee de l'autre cote -
    # un faux "URL mismatch". On ne touche PAS un chemin commencant par "/"
    # (root-relatif, sans domaine - cas legitimement different, hors
    # portee ici) : seul le cas domaine+chemin colle sans schema est vise.
    if not scheme and not netloc and path and not path.startswith("/"):
        reparsed = urllib.parse.urlparse("//" + path)
        netloc = reparsed.netloc.lower()
        path = reparsed.path or "/"

    # Un netloc connu (cas ci-dessus, ou un lien protocol-relatif "//host/..."
    # dont urlparse() extrait deja correctement netloc/path sans schema) mais
    # sans schema produirait sinon "://host/..." plus bas - jamais egal a la
    # meme URL correctement schemee comparee de l'autre cote.
    if netloc and not scheme:
        scheme = "https"

    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    sorted_params = sorted(params)

    rebuilt_query = urllib.parse.urlencode(sorted_params)

    if rebuilt_query:
        return f"{scheme}://{netloc}{path}?{rebuilt_query}"
    return f"{scheme}://{netloc}{path}"


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
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
