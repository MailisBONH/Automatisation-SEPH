"""
Chargement des templates email Braze via l'API REST Braze, en remplacement
de l'upload manuel de fichier HTML (AEM) - maquette / point de depart pour
la migration vers Braze (branche `braze`), tant qu'aucun acces reel (compte,
cle API, export de template) n'est encore disponible.

Braze expose les templates email via l'API Templates
(https://www.braze.com/docs/api/endpoints/templates/email_templates/) :
- GET /templates/email/list  -> liste des templates (id, nom, tags, date de
  modification), pour retrouver l'id d'un template sans le connaitre a l'avance.
- GET /templates/email/info  -> contenu HTML brut (+ sujet, tags) d'un
  template precis, sans avoir besoin d'un envoi/preview reel.

## Strategie retenue : le systeme de traduction natif Braze comme referentiel

Un vrai export de template Braze (voir discussion du 2026-10-01) confirme
qu'il n'y a PAS de convention de classe CSS equivalente a `audit-component-*`
(AEM) - les templates sont construits par glisser-deposer de blocs, sans
marquage metier. Plutot que de deviner un referentiel de substitution, la
piste retenue est le systeme de **traduction natif** de Braze :
https://www.braze.com/docs/user_guide/message_building_by_channel/email/using_locales

Les redacteurs entourent chaque texte a traduire d'un tag Liquid avec un id
semantique choisi par eux : `{% translation header_title %}Texte{% endtranslation %}`.
Braze expose ensuite, par template et par locale, un `translation_map`
(id -> texte attendu dans cette langue) via des endpoints dedies :
- GET /templates/email/translations/source?template_id=...      -> translation_map SOURCE (texte par defaut du template)
- GET /templates/email/translations/?template_id=...             -> liste des locales/traductions disponibles pour ce template
- GET /templates/translations/email?template_id=...&locale_id=... -> translation_map d'UNE locale precise
Permission requise : `templates.translations.get`.

*** IMPORTANT : ces 3 endpoints sont en EARLY ACCESS chez Braze (pas
disponibles par defaut sur un compte standard) - a demander explicitement a
l'interlocuteur/account manager Braze de Sephora avant de pouvoir les
appeler en reel. Les chemins/noms de champs ci-dessus viennent de la
documentation publique Braze (recherche web du 2026-10-01), PAS d'un appel
reel : a reverifier des que l'acces sera accorde (voir fonctions `list_template_locales`/
`get_translation_map` ci-dessous, clairement marquees "reponse non confirmee"). ***

Ce `translation_map` a EXACTEMENT la forme du referentiel Excel actuel
(id de traduction -> wording attendu), ce qui permet de reutiliser tel quel
le moteur de comparaison existant (qa.wording_qa.run_wording_qa) sans le
modifier - voir build_expected_wordings_from_translations() et
run_translation_based_qa() plus bas, qui font le pont entre les deux. Le
seul maillon manquant : associer chaque texte du HTML REELLEMENT RENDU
(sans tag `{% translation %}`, deja remplace par le texte final) a l'id de
traduction correspondant - fait ici par ALIGNEMENT POSITIONNEL (meme ordre
d'apparition dans le template source et dans le rendu), voir
assign_translation_ids() et son avertissement sur cette hypothese non
verifiee.

Points a valider des qu'un acces reel sera disponible (cf. differences avec
le pipeline AEM actuel, voir qa/html_extraction.py et qa/text_utils.py) :
- Templating : Braze utilise Liquid ({% if %}/{% endif %}, {{ variable }}),
  pas la syntaxe AEM (<% if %>/<%= %>) geree aujourd'hui par
  qa.text_utils.expand_conditional_variants - une variante Liquid de cette
  fonction sera necessaire pour les wordings qui contiennent un bloc
  conditionnel (non couvert par ce prototype). Plus immediat :
  qa.text_utils.is_valid rejette tout texte dont les caracteres `<>{}[]`
  depassent 5% de sa longueur ("trop technique") - une simple accolade
  Liquid double ({{ }}) suffit a declencher ce filtre sur un texte court ;
  sans impact ici puisque le HTML RENDU (post-Liquid) n'en contient plus,
  mais a garder en tete si un jour on extrait depuis un template non rendu.
- Alignement positionnel (voir assign_translation_ids) : suppose que
  l'ordre et le nombre de textes extraits du HTML rendu correspondent
  exactement a l'ordre et au nombre de tags `{% translation %}` du template
  source - hypothese plausible mais NON VERIFIEE sur un vrai template, vu
  qu'aucun export avec tags de traduction n'a encore ete observe.
- Ancien branchement (extract_texts_from_template, base sur les marqueurs
  AEM) conserve ci-dessous pour reference/tests, mais plus la piste
  principale : un template Braze reel n'a pas ces marqueurs (voir
  tests/test_braze_loader.py).

Config attendue (variables d'environnement, memes conventions que
qa.config.TEAMS_WEBHOOK_URL - absentes par defaut, jamais d'erreur tant que
ce loader n'est pas reellement appele hors mode mock ; toutes centralisees
dans qa/config.py, pas definies ici) :
- BRAZE_API_KEY : cle API REST (scope `templates_email_info`/
  `templates_email_list`/`templates.translations.get` minimum cote Braze).
- BRAZE_REST_ENDPOINT : URL de l'instance Braze (ex.
  "https://rest.iad-01.braze.com") - varie selon la region/le cluster du
  compte, a recuperer aupres de l'equipe qui gere le compte Braze.

Tant que BRAZE_API_KEY/BRAZE_REST_ENDPOINT ne sont pas configurees (cas par
defaut aujourd'hui), BRAZE_MOCK_MODE reste actif : list_email_templates()
et get_email_template_html() retournent des donnees d'exemple statiques
plutot que d'appeler l'API - permet de brancher et tester le reste du
pipeline sans dependre d'un acces Braze reel, en attendant la migration.
"""
import re

import requests

from qa.config import BRAZE_API_KEY, BRAZE_MOCK_MODE, BRAZE_REQUEST_TIMEOUT, BRAZE_REST_ENDPOINT
from qa.html_extraction import extract_texts_from_html, extract_texts_in_order
from qa.text_utils import clean_text, expand_conditional_variants, normalize, normalize_currency
from qa.wording_qa import run_wording_qa

_MOCK_TEMPLATES = [
    {
        "email_template_id": "mock-template-seq1-non-loyal",
        "template_name": "SEQ 1 NON LOYAL (mock)",
        "tags": ["mock", "hugplan"],
        "updated_at": "2026-01-01T00:00:00Z",
    },
    {
        "email_template_id": "mock-template-seq1-loyal",
        "template_name": "SEQ 1 LOYAL (mock)",
        "tags": ["mock", "hugplan"],
        "updated_at": "2026-01-01T00:00:00Z",
    },
]

# Template SOURCE (non rendu) RETOURNE PAR get_email_template_html() EN MODE
# MOCK : avec tags de traduction Liquid natifs Braze
# (`{% translation id %}...{% endtranslation %}`), utilise par le flux
# "traduction native" plus bas (extract_translation_tags,
# run_translation_based_qa). Inspire du vrai template "Birthday seq1 gold"
# partage le 2026-10-01 : memes themes (anniversaire, points de fidelite,
# CTA), simplifie. PAS de marqueur AEM (class="text-contribution"/
# "audit-component-*") : un vrai template Braze n'en a pas (voir
# limitations en tete de module) - extract_texts_from_template() (ancienne
# piste, conservee pour reference) y renvoie donc une liste vide par
# defaut desormais, voir tests/test_braze_loader.py.
_MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS = """<html>
<body>
  <h1>{% translation header_title %}Joyeux anniversaire !{% endtranslation %}</h1>
  <p>{% translation intro_text %}Nous sommes ravis de vous souhaiter un tres bon anniversaire.{% endtranslation %}</p>
  <h2>{% translation offer_title %}Profitez de votre cadeau{% endtranslation %}</h2>
  <a href="https://www.sephora.fr/mock-cta">{% translation cta_label %}En profiter{% endtranslation %}</a>
</body>
</html>"""

# Locales disponibles pour ce template - mock de
# `GET /templates/email/translations/` (voir docstring du module,
# endpoint EARLY ACCESS, structure non confirmee en reel).
_MOCK_LOCALES = [
    {"uuid": "locale-fr-fr", "name": "Français (France)", "country": "FR", "language": "fr", "locale_key": "fr-fr"},
    {"uuid": "locale-de-de", "name": "Deutsch (Deutschland)", "country": "DE", "language": "de", "locale_key": "de-de"},
]

# translation_map par (template_id, locale_id) - mock de
# `GET /templates/translations/email?template_id=...&locale_id=...`.
# La locale "locale-de-de" porte une difference VOLONTAIRE sur
# "cta_label" (par rapport a _MOCK_RENDERED_HTML_BY_LOCALE plus bas) pour
# demontrer qu'une vraie divergence est detectee en KO, pas seulement le
# cas "tout est OK".
_MOCK_TRANSLATION_MAPS = {
    ("mock-template-seq1-non-loyal", "locale-fr-fr"): {
        "header_title": "Joyeux anniversaire !",
        "intro_text": "Nous sommes ravis de vous souhaiter un tres bon anniversaire.",
        "offer_title": "Profitez de votre cadeau",
        "cta_label": "En profiter",
    },
    ("mock-template-seq1-non-loyal", "locale-de-de"): {
        "header_title": "Alles Gute zum Geburtstag!",
        "intro_text": "Wir wunschen Ihnen von Herzen einen schonen Geburtstag.",
        "offer_title": "Geniessen Sie Ihr Geschenk",
        "cta_label": "Jetzt nutzen",
    },
}

# HTML RENDU (post-Liquid, ce qu'un destinataire recevrait reellement) par
# locale - PAS un appel Braze reel : aucun endpoint Templates ne fournit un
# rendu par locale, ce serait plutot l'export d'une campagne/Canvas live
# (voir qa.braze_loader module docstring). Pur confort de demonstration
# pour la page pages/2_Braze_Loader.py, pour illustrer run_translation_based_qa
# sans dependre d'un vrai envoi. La version "locale-de-de" contient une
# divergence volontaire sur le CTA ("Jetzt einlosen" au lieu de
# "Jetzt nutzen" attendu par _MOCK_TRANSLATION_MAPS) pour prouver que la
# comparaison detecte un vrai ecart, pas seulement des cas 100% OK.
_MOCK_RENDERED_HTML_BY_LOCALE = {
    "locale-fr-fr": """<html><body>
        <h1>Joyeux anniversaire !</h1>
        <p>Nous sommes ravis de vous souhaiter un tres bon anniversaire.</p>
        <h2>Profitez de votre cadeau</h2>
        <a href="https://www.sephora.fr/mock-cta">En profiter</a>
    </body></html>""",
    "locale-de-de": """<html><body>
        <h1>Alles Gute zum Geburtstag!</h1>
        <p>Wir wunschen Ihnen von Herzen einen schonen Geburtstag.</p>
        <h2>Geniessen Sie Ihr Geschenk</h2>
        <a href="https://www.sephora.fr/mock-cta">Jetzt einlosen</a>
    </body></html>""",
}


class BrazeAPIError(RuntimeError):
    """Erreur retournee par l'API Braze (statut HTTP non-2xx), ou config
    manquante (BRAZE_API_KEY/BRAZE_REST_ENDPOINT absentes) hors mode mock."""


def _require_config():
    if not BRAZE_API_KEY or not BRAZE_REST_ENDPOINT:
        raise BrazeAPIError(
            "BRAZE_API_KEY et/ou BRAZE_REST_ENDPOINT non configurees "
            "(mode mock desactive via BRAZE_MOCK_MODE, mais aucun acces API "
            "reel disponible)."
        )


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {BRAZE_API_KEY}",
        "Content-Type": "application/json",
    }


def _get(url: str, params: dict) -> dict:
    """GET authentifie vers l'API Braze, avec gestion d'erreur centralisee -
    partagee par list_email_templates/get_email_template_html pour ne pas
    dupliquer ce bloc a chaque appel (voir aussi
    qa.notifications.send_teams_notification, qui gere la meme classe
    d'erreurs reseau pour un POST best-effort : ici on RELEVE l'erreur au
    lieu de l'avaler, cette page d'exploration veut voir n'importe quel
    echec plutot que le masquer).

    Retourne le corps JSON deja parse (dict). Leve BrazeAPIError pour toute
    defaillance - statut HTTP non-2xx (comme avant), erreur reseau (timeout,
    connexion refusee, DNS...) ou reponse 2xx dont le corps n'est pas du
    JSON valide (ex. page d'erreur HTML renvoyee par un proxy/passerelle
    intermediaire) : ces deux derniers cas levaient auparavant une exception
    requests/json brute, jamais interceptee par les `except BrazeAPIError`
    des appelants (voir pages/2_Braze_Loader.py), donc jamais affichee
    proprement a l'utilisateur."""
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=BRAZE_REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise BrazeAPIError(f"Impossible de joindre l'API Braze : {exc}") from exc

    if not (200 <= resp.status_code < 300):
        raise BrazeAPIError(f"Braze API error ({resp.status_code}): {resp.text[:500]}")

    try:
        return resp.json()
    except ValueError as exc:
        raise BrazeAPIError(f"Réponse Braze illisible (pas du JSON valide) : {resp.text[:500]}") from exc


def list_email_templates(tag: str = None) -> list:
    """
    Liste les templates email disponibles cote Braze (id, nom, tags, date de
    modification), pour permettre de choisir un template sans en connaitre
    l'id a l'avance - equivalent Braze du menu deroulant d'onglets Excel
    (voir qa.excel_loader.list_wording_sheet_choices) ou de campagne AEM.

    `tag` filtre optionnellement sur un tag Braze (ex. le nom de campagne) -
    TOUJOURS cote client (apres reception), y compris en mode API reelle :
    l'API Braze (GET /templates/email/list) ne documente que
    limit/offset/modified_after/modified_before comme parametres de requete
    (https://www.braze.com/docs/api/endpoints/templates/email_templates/get_list_email_templates),
    aucun filtre par tag - un parametre "tags" envoye a cet endpoint serait
    vraisemblablement ignore silencieusement par Braze, renvoyant la liste
    complete NON filtree plutot qu'une erreur, ce qui masquerait le
    probleme. Filtrer cote client, comme deja fait en mode mock, evite de
    reposer sur cette capacite serveur non confirmee.

    En mode mock (BRAZE_MOCK_MODE, actif par defaut) : utilise
    `_MOCK_TEMPLATES` comme source, sans appel reseau.
    """
    if BRAZE_MOCK_MODE:
        templates = list(_MOCK_TEMPLATES)
    else:
        _require_config()
        templates = _get(f"{BRAZE_REST_ENDPOINT}/templates/email/list", params={}).get("templates", [])

    if tag:
        return [t for t in templates if tag in t.get("tags", [])]
    return templates


def get_email_template_html(email_template_id: str) -> str:
    """
    Retourne le HTML brut (body) d'un template email Braze - equivalent
    Braze de qa.html_extraction.load_raw_html, mais la source est l'API
    plutot qu'un fichier uploade sur disque.

    Pas de reconstruction "view-source" ici : specifique aux exports AEM
    (voir qa.html_extraction.is_view_source_document), Braze retourne
    directement le HTML du template tel quel via l'API.

    En mode mock (BRAZE_MOCK_MODE, actif par defaut) : retourne
    `_MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS` (tags de traduction Liquid natifs,
    voir docstring du module), sans appel reseau, quel que soit
    `email_template_id`.
    """
    if BRAZE_MOCK_MODE:
        return _MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS

    _require_config()
    return _get(
        f"{BRAZE_REST_ENDPOINT}/templates/email/info",
        params={"email_template_id": email_template_id},
    ).get("body", "")


def extract_texts_from_template(email_template_id: str) -> list:
    """
    Recupere le HTML d'un template Braze (get_email_template_html) et le
    fait passer par le meme moteur d'extraction que le pipeline AEM
    (qa.html_extraction.extract_texts_from_html) - branchement minimal pour
    verifier que la plomberie loader -> extraction fonctionne de bout en
    bout, meme si le moteur d'extraction lui-meme reste specifique aux
    marqueurs AEM pour l'instant (voir limitations en tete de module).

    Sur un template Braze reel (pas de class="text-contribution" ni
    "audit-component-*"), le resultat est vraisemblablement une liste vide :
    ce n'est pas une erreur de branchement, c'est la preuve que
    qa.html_extraction devra evoluer (ou qu'un autre mode d'extraction,
    "texte seul", devra etre ajoute) avant de pouvoir QA un vrai template
    Braze - voir tests/test_braze_loader.py. C'est d'ailleurs le cas du mock
    par defaut (`_MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS`, qui n'a pas ces
    marqueurs) : cette fonction y renvoie [] en mode mock. Voir plutot
    run_translation_based_qa() pour la piste de QA Braze retenue (systeme de
    traduction natif, pas de marqueur de composant requis).
    """
    raw_html = get_email_template_html(email_template_id)
    return extract_texts_from_html(raw_html)


# =========================
# TRADUCTION NATIVE BRAZE (prototype)
# =========================
# Voir "Strategie retenue" dans la docstring du module pour le contexte -
# ce bloc remplace le referentiel Excel par le translation_map natif Braze,
# tant pour la source des wordings attendus que pour le matching par
# "composant" (ici : l'id de traduction, aligne positionnellement).

TRANSLATION_TAG_PATTERN = re.compile(
    r"\{%\s*translation\s+([\w\-]+)\s*%\}(.*?)\{%\s*endtranslation\s*%\}",
    re.DOTALL,
)


def extract_translation_tags(raw_html: str) -> list:
    """
    Parse les tags Liquid `{% translation <id> %}...{% endtranslation %}`
    du HTML SOURCE (non rendu) d'un template Braze - ce que
    `/templates/email/info` renvoie tant que le template n'a pas ete
    fusionne avec les donnees d'un destinataire (voir
    https://www.braze.com/docs/user_guide/message_building_by_channel/email/using_locales).

    Retourne, dans l'ORDRE D'APPARITION, une liste de
    {"translation_id": id, "source_text": texte par defaut} - ce texte
    source sert de repli si aucune traduction n'existe pour une locale
    donnee (voir build_expected_wordings_from_translations).

    L'id est choisi par le redacteur et RECOMMANDE semantique (ex.
    "header_title") par la doc Braze, mais pas garanti : un id non renomme
    peut rester auto-numerote (ex. "id_0", vu dans un exemple de reponse
    Braze pour un autre endpoint de traduction) - aucune hypothese n'est
    faite ici sur le format de l'id, juste capture tel quel.
    """
    return [
        {"translation_id": m.group(1), "source_text": clean_text(m.group(2))}
        for m in TRANSLATION_TAG_PATTERN.finditer(raw_html or "")
    ]


def list_template_locales(template_id: str) -> list:
    """
    Liste les locales disponibles pour un template - equivalent de
    `GET /templates/email/translations/` (endpoint EARLY ACCESS chez
    Braze, voir docstring du module : la cle `"locales"` ci-dessous pour le
    chemin API reel n'est PAS confirmee par un appel reel, juste la
    meilleure lecture possible de la documentation publique - a ajuster des
    que l'acces sera obtenu).

    En mode mock : retourne une copie de `_MOCK_LOCALES`, quel que soit
    `template_id`.
    """
    if BRAZE_MOCK_MODE:
        return list(_MOCK_LOCALES)

    _require_config()
    return _get(
        f"{BRAZE_REST_ENDPOINT}/templates/email/translations/",
        params={"template_id": template_id},
    ).get("locales", [])


def get_translation_map(template_id: str, locale_id: str) -> dict:
    """
    Retourne le `translation_map` (id de traduction -> texte attendu) pour
    un template et une locale donnes - equivalent de
    `GET /templates/translations/email?template_id=...&locale_id=...`
    (endpoint EARLY ACCESS chez Braze, voir docstring du module : la cle
    `"translation_map"` ci-dessous est confirmee par la doc publique pour
    l'endpoint "source" voisin, mais PAS verifiee par un appel reel sur cet
    endpoint precis).

    En mode mock : retourne une copie de
    `_MOCK_TRANSLATION_MAPS[(template_id, locale_id)]`, ou {} si cette
    combinaison n'existe pas dans le mock (jamais d'erreur - voir
    build_expected_wordings_from_translations, qui se rabat alors sur le
    texte source de chaque tag).
    """
    if BRAZE_MOCK_MODE:
        return dict(_MOCK_TRANSLATION_MAPS.get((template_id, locale_id), {}))

    _require_config()
    return _get(
        f"{BRAZE_REST_ENDPOINT}/templates/translations/email",
        params={"template_id": template_id, "locale_id": locale_id},
    ).get("translation_map", {})


def get_mock_rendered_html(locale_id: str) -> str:
    """
    HTML tel qu'un destinataire de cette locale le recevrait reellement
    (post-Liquid) - PUR CONFORT DE DEMONSTRATION pour pages/2_Braze_Loader.py,
    n'existe PAS cote API Braze : aucun endpoint Templates ne rend un
    template pour une locale donnee, ce serait l'export d'une campagne/Canvas
    EFFECTIVEMENT envoyee (voir GET /campaigns/details, hors scope de ce
    prototype). Utilise uniquement en mode mock ; appeler cette fonction
    hors mode mock n'a pas de sens et renvoie toujours "" (rien a simuler en
    mode reel - la page doit alors demander un vrai HTML envoye par l'appelant).
    """
    if not BRAZE_MOCK_MODE:
        return ""
    return _MOCK_RENDERED_HTML_BY_LOCALE.get(locale_id, "")


def build_expected_wordings_from_translations(translation_tags: list, translation_map: dict) -> list:
    """
    Combine les tags de traduction du template source (ordre + id, voir
    extract_translation_tags) avec le translation_map d'une locale (voir
    get_translation_map) pour produire une liste de "wordings attendus"
    dans EXACTEMENT la meme forme que qa.excel_loader.load_excel_wordings
    (Component/Element/Wording/Technical/norm/curr/norm_variants/
    curr_variants/Sheet) - un remplacement direct du referentiel Excel,
    reutilisable tel quel par qa.wording_qa.run_wording_qa SANS modifier ce
    moteur.

    `Technical` = l'id de traduction lui-meme : c'est ce qui permet a
    run_wording_qa de faire un matching "Direct" fiable, a condition que les
    items HTML passes en entree portent le MEME id en `component_class`
    (voir assign_translation_ids, qui l'assigne par ALIGNEMENT POSITIONNEL -
    un template Braze n'a aucune classe CSS equivalente a `audit-component-*`
    a cette etape).

    Repli sur le texte SOURCE du tag (`source_text`) si la locale n'a pas de
    traduction pour cet id (absente de translation_map) - plutot que
    produire un wording vide ou planter : un id non encore traduit reste un
    cas a signaler (le HTML rendu pour cette locale montrera alors
    vraisemblablement un ecart face a ce texte source), pas une raison
    d'ignorer la ligne.

    Pas d'expansion Liquid ({% if %}) ici, contrairement aux blocs
    conditionnels AEM (expand_conditional_variants ne comprend que la
    syntaxe <% %> - voir limitations en tete de module) : un wording
    contenant un bloc conditionnel Liquid sera traite comme un texte brut,
    accolades incluses.
    """
    wordings = []
    for tag in translation_tags:
        translation_id = tag["translation_id"]
        wording = translation_map.get(translation_id) or tag["source_text"]
        variants = expand_conditional_variants(wording)
        norm_variants = [normalize(v) for v in variants]
        curr_variants = [normalize_currency(v) for v in variants]
        wordings.append({
            "Component": translation_id,
            "Element": translation_id,
            "Wording": wording,
            "Technical": translation_id,
            "norm": norm_variants[0],
            "curr": curr_variants[0],
            "norm_variants": norm_variants,
            "curr_variants": curr_variants,
            "Sheet": "Braze translations",
        })
    return wordings


def assign_translation_ids(html_texts: list, translation_tags: list) -> list:
    """
    Aligne POSITIONNELLEMENT (meme ordre d'apparition, voir
    qa.html_extraction.extract_texts_in_order) chaque texte HTML rendu avec
    l'id de traduction correspondant du template source, et le pose en
    `component_class` - substitut du marquage `audit-component-*` (AEM),
    absent des templates Braze. Ne modifie pas `html_texts` en place
    (retourne une copie de chaque item).

    HYPOTHESE NON VERIFIEE (aucun template Braze reel avec tags de
    traduction observe a ce jour) : l'ordre et le nombre de textes extraits
    du HTML RENDU correspondent exactement a l'ordre et au nombre de tags
    `{% translation %}` du template SOURCE. Si les deux listes n'ont pas la
    meme longueur, l'alignement s'arrete a la plus courte des deux (les
    items HTML en trop gardent `component_class=None`, traites comme
    "Component class not found" par qa.wording_qa plutot que silencieusement
    ignores ou faussement associes a un mauvais id).
    """
    ids_in_order = [t["translation_id"] for t in translation_tags]
    aligned = []
    for i, item in enumerate(html_texts):
        new_item = dict(item)
        if i < len(ids_in_order):
            new_item["component_class"] = ids_in_order[i]
        aligned.append(new_item)
    return aligned


def run_translation_based_qa(raw_template_html: str, rendered_html: str, translation_map: dict) -> list:
    """
    Orchestration bout-en-bout du flux "traduction native" : extrait les
    tags de traduction du template source, extrait les textes du HTML
    rendu dans l'ordre (qa.html_extraction.extract_texts_in_order), les
    aligne positionnellement avec ces id (assign_translation_ids), construit
    le referentiel attendu a partir du translation_map
    (build_expected_wordings_from_translations), puis reutilise TEL QUEL le
    moteur de comparaison existant (qa.wording_qa.run_wording_qa) - memes
    regles de matching/scoring/criticite que le pipeline AEM (voir le
    README : strategies Direct/Agrege/Split/Closest match).

    Retourne la liste de lignes de resultat (meme forme que celle du
    pipeline AEM, voir qa.matching.make_row) - un Status "OK"/"KO" par
    wording, exploitable telle quelle par pages/2_Braze_Loader.py ou un
    futur qa.report.save_report.
    """
    translation_tags = extract_translation_tags(raw_template_html)
    rendered_items = extract_texts_in_order(rendered_html)
    aligned_items = assign_translation_ids(rendered_items, translation_tags)
    expected_wordings = build_expected_wordings_from_translations(translation_tags, translation_map)
    return run_wording_qa(aligned_items, expected_wordings)
