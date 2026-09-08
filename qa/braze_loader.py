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

Points a valider des qu'un acces reel sera disponible (cf. differences avec
le pipeline AEM actuel, voir qa/html_extraction.py et qa/text_utils.py) :
- Templating : Braze utilise Liquid ({% if %}/{% endif %}, {{ variable }}),
  pas la syntaxe AEM (<% if %>/<%= %>) geree aujourd'hui par
  qa.text_utils.expand_conditional_variants - une variante Liquid de cette
  fonction sera necessaire avant de brancher ce loader sur qa/matching.py.
  Plus immediat : qa.text_utils.is_valid rejette deja tout texte dont les
  caracteres `<>{}[]` depassent 5% de sa longueur ("trop technique") - une
  simple accolade Liquid double ({{ }}) suffit a declencher ce filtre sur un
  texte court, avant meme la question de l'expansion conditionnelle (voir
  commentaire au-dessus de _MOCK_HTML plus bas, ou ce mock a ete simplifie
  pour cette raison).
- Composants techniques : le matching HTML<->Excel actuel s'appuie sur des
  classes CSS `audit-component-*` (AEM, voir
  qa.html_extraction.find_component_class). Aucune convention equivalente
  n'est connue cote Braze pour l'instant - a confirmer sur un vrai template
  exporte. Sans marqueur equivalent, le matching par composant (etapes
  Direct/Agrege/Split de qa/matching.py) devra probablement se rabattre sur
  un mode "texte seul" pour les templates Braze.
- Branchement fait via extract_texts_from_template() ci-dessous, qui appelle
  qa.html_extraction.extract_texts_from_html (variante de
  load_and_extract_html_texts qui accepte du HTML deja en memoire plutot
  qu'un chemin de fichier). Le moteur d'extraction lui-meme n'est PAS
  adapte a Braze : il ne trouve un texte a comparer que si le HTML porte
  les memes marqueurs AEM (class="text-contribution", "audit-component-*"),
  ce que _MOCK_HTML ci-dessous fait exprès pour demontrer le branchement -
  un vrai template Braze n'aura tres probablement PAS ces marqueurs, et
  extract_texts_from_template() y renverra alors une liste vide (voir
  tests/test_braze_loader.py, pas un bug de ce loader).

Config attendue (variables d'environnement, memes conventions que
qa.config.TEAMS_WEBHOOK_URL - absentes par defaut, jamais d'erreur tant que
ce loader n'est pas reellement appele hors mode mock) :
- BRAZE_API_KEY : cle API REST (scope `templates_email_info`/
  `templates_email_list` minimum cote Braze).
- BRAZE_REST_ENDPOINT : URL de l'instance Braze (ex.
  "https://rest.iad-01.braze.com") - varie selon la region/le cluster du
  compte, a recuperer aupres de l'equipe qui gere le compte Braze.

Tant que BRAZE_API_KEY/BRAZE_REST_ENDPOINT ne sont pas configurees (cas par
defaut aujourd'hui), BRAZE_MOCK_MODE reste actif : list_email_templates()
et get_email_template_html() retournent des donnees d'exemple statiques
plutot que d'appeler l'API - permet de brancher et tester le reste du
pipeline sans dependre d'un acces Braze reel, en attendant la migration.
"""
import os

import requests

from qa.html_extraction import extract_texts_from_html

BRAZE_API_KEY = os.environ.get("BRAZE_API_KEY", "").strip()
BRAZE_REST_ENDPOINT = os.environ.get("BRAZE_REST_ENDPOINT", "").strip().rstrip("/")

# Actif par defaut (pas de cle/endpoint configures = pas d'acces reel
# possible) : peut etre force a "0"/"false" une fois BRAZE_API_KEY et
# BRAZE_REST_ENDPOINT renseignes, pour reellement appeler l'API.
BRAZE_MOCK_MODE = os.environ.get("BRAZE_MOCK_MODE", "1").strip().lower() not in ("0", "false", "")

REQUEST_TIMEOUT = 15  # secondes - meme convention que qa.notifications.send_teams_notification

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

# PORTE VOLONTAIREMENT les memes marqueurs AEM (class="text-contribution"/
# "audit-component-*") que le HTML actuel, pour que
# extract_texts_from_template() ci-dessous produise un resultat non vide et
# demontre concretement le branchement loader -> extraction dans la page
# pages/2_Braze_Loader.py. Un vrai template Braze n'aura tres probablement
# PAS ces marqueurs (voir limitations en tete de module) :
# tests/test_braze_loader.py couvre separement ce cas (HTML sans aucun
# marqueur -> liste vide), pas simule par CE mock precis.
#
# Pas de syntaxe Liquid ({{ }}/{% %}) dans les textes ci-dessous, contrairement
# a une premiere version de ce mock : qa.text_utils.is_valid rejette un texte
# ou les caracteres techniques (<>{}[]) depassent 5% de sa longueur ("trop
# technique pour etre un wording reel") - une accolade Liquid double ({{/}})
# suffit a declencher ce filtre sur un texte court. C'est une LIMITATION
# SUPPLEMENTAIRE decouverte en testant ce loader (en plus de Liquid non
# supporte par expand_conditional_variants et de l'absence de convention
# "audit-component-*" cote Braze, voir plus haut) : un vrai template Braze
# qui utilise Liquid directement dans un wording risque de se faire rejeter
# ici avant meme la comparaison, pas seulement mal comparer.
_MOCK_HTML = """<html>
<body>
  <div class="text-contribution audit-component-header">
    <h1>Hello, your points are waiting</h1>
  </div>
  <div class="text-contribution audit-component-body">
    <p>You have 25 points available.</p>
    <a href="https://www.sephora.fr/mock-cta">Shop now</a>
  </div>
</body>
</html>"""


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


def list_email_templates(tag: str = None) -> list:
    """
    Liste les templates email disponibles cote Braze (id, nom, tags, date de
    modification), pour permettre de choisir un template sans en connaitre
    l'id a l'avance - equivalent Braze du menu deroulant d'onglets Excel
    (voir qa.excel_loader.list_wording_sheet_choices) ou de campagne AEM.

    `tag` filtre optionnellement sur un tag Braze (ex. le nom de campagne).

    En mode mock (BRAZE_MOCK_MODE, actif par defaut) : retourne
    `_MOCK_TEMPLATES`, sans appel reseau.
    """
    if BRAZE_MOCK_MODE:
        if tag:
            return [t for t in _MOCK_TEMPLATES if tag in t.get("tags", [])]
        return list(_MOCK_TEMPLATES)

    _require_config()

    params = {"tags": tag} if tag else {}
    resp = requests.get(
        f"{BRAZE_REST_ENDPOINT}/templates/email/list",
        headers=_headers(),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    if not (200 <= resp.status_code < 300):
        raise BrazeAPIError(f"Braze API error ({resp.status_code}): {resp.text[:500]}")

    return resp.json().get("templates", [])


def get_email_template_html(email_template_id: str) -> str:
    """
    Retourne le HTML brut (body) d'un template email Braze - equivalent
    Braze de qa.html_extraction.load_raw_html, mais la source est l'API
    plutot qu'un fichier uploade sur disque.

    Pas de reconstruction "view-source" ici : specifique aux exports AEM
    (voir qa.html_extraction.is_view_source_document), Braze retourne
    directement le HTML du template tel quel via l'API.

    En mode mock (BRAZE_MOCK_MODE, actif par defaut) : retourne `_MOCK_HTML`,
    sans appel reseau, quel que soit `email_template_id`.
    """
    if BRAZE_MOCK_MODE:
        return _MOCK_HTML

    _require_config()

    resp = requests.get(
        f"{BRAZE_REST_ENDPOINT}/templates/email/info",
        headers=_headers(),
        params={"email_template_id": email_template_id},
        timeout=REQUEST_TIMEOUT,
    )
    if not (200 <= resp.status_code < 300):
        raise BrazeAPIError(f"Braze API error ({resp.status_code}): {resp.text[:500]}")

    return resp.json().get("body", "")


def extract_texts_from_template(email_template_id: str) -> list:
    """
    Recupere le HTML d'un template Braze (get_email_template_html) et le
    fait passer par le meme moteur d'extraction que le pipeline AEM
    (qa.html_extraction.extract_texts_from_html) - branchement minimal pour
    verifier que la plomberie loader -> extraction fonctionne de bout en
    bout, meme si le moteur d'extraction lui-meme reste specifique aux
    marqueurs AEM pour l'instant (voir limitations en tete de module).

    Sur un template Braze reel (pas de class="text-contribution" ni
    "audit-component-*"), le resultat sera vraisemblablement une liste vide :
    ce n'est pas une erreur de branchement, c'est la preuve que
    qa.html_extraction devra evoluer (ou qu'un autre mode d'extraction,
    "texte seul", devra etre ajoute) avant de pouvoir QA un vrai template
    Braze - voir tests/test_braze_loader.py. _MOCK_HTML, lui, porte ces
    marqueurs volontairement : en mode mock, cette fonction renvoie donc un
    resultat non vide, pour demontrer concretement le branchement.
    """
    raw_html = get_email_template_html(email_template_id)
    return extract_texts_from_html(raw_html)
