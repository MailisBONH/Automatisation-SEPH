import os
import re

# =========================
# CONFIGURATION
# =========================
OUTPUT_DIR = "output"
TEMP_DIR = "temp"          # Fichiers uploades le temps d'une session (voir qa.cleanup, home.py)

# Nombre de rapports Excel les plus recents conserves par type (wording/url/
# combined) dans un meme dossier de sequence - voir qa.archive.archive_report.
KEEP_REPORTS = 2

# Purge automatique de temp/ (voir qa.cleanup) : age au-dela duquel un
# fichier uploade est supprime, et frequence maximale du scan reel (un
# fichier marqueur evite de rescanner le dossier a chaque interaction
# Streamlit, qui reexecute tout le script).
TEMP_MAX_AGE_HOURS = 24
TEMP_CLEANUP_THROTTLE_SECONDS = 3600

# Seuils de couleur pour l'affichage d'un score (%) dans les rapports Excel
# (Summary, recap multi-sequences, rapport combine) - voir
# qa.report.score_fill_color. Au-dela de SCORE_COLOR_GOOD : vert ; entre les
# deux : jaune ; en-dessous de SCORE_COLOR_WARN : rouge.
SCORE_COLOR_GOOD = 100
SCORE_COLOR_WARN = 80

# Detection de wordings dans la mauvaise langue (voir
# qa.diagnostics.find_wrong_language_wordings) : longueur minimale d'un
# texte pour que la detection de langue soit fiable, et nombre minimal
# d'occurrences d'une langue minoritaire avant de la signaler (une detection
# isolee est le plus souvent un faux positif).
LANGUAGE_DETECTION_MIN_TEXT_LENGTH = 30
LANGUAGE_DETECTION_MIN_CLUSTER_SIZE = 2

# Duree de conservation de l'historique classe (pays/trigger/sequence), en
# jours. Voir qa.history.prune_history pour la regle de purge appliquee au
# dela de cette limite.
HISTORY_RETENTION_DAYS = 365

WORDING_COLUMN_NAME = "TRANSLATION"       # Mot-clé (insensible à la casse) de la colonne wording dans l'Excel
CAMPAIGN_KEYWORD = "HugPlan S1 Bronze"    # Mot-clé pour sélectionner l'onglet dans l'Excel
MIN_TEXT_LENGTH = 3

# Active les logs detailles d'extraction (print) affiches dans l'expander
# "Logs de debug" de l'UI. Override possible sans toucher au code via la
# variable d'environnement QA_DEBUG_MODE (ex: "0"/"false" pour desactiver),
# utile pour couper ces logs en environnement partage sans redeployer.
DEBUG_MODE = os.environ.get("QA_DEBUG_MODE", "1").strip().lower() not in ("0", "false", "")

URL_SHEET_KEYWORD = "UTM"                 # Mot-clé pour repérer l'onglet des URLs trackées
URL_COLUMN_NAME = "<< TRACKED URL >>"     # Colonne des URLs de référence dans cet onglet

# Domaines pour lesquels la query string (paramètres UTM/tracking) n'est
# jamais comparée, seule l'URL de base (chemin) compte - voir
# qa.url_utils.is_query_ignored_domain/strip_query. homeios.onelink.me :
# lien court OneLink/deep-link vers l'app - le tracking (UTM/attribution)
# est déjà encodé par OneLink DANS le chemin du lien lui-même (le token
# après /oUpm/...), pas dans une query string classique ; d'éventuels
# paramètres ajoutés en plus sur ce lien sont générés dynamiquement à
# l'envoi et ne sont jamais figés à l'identique dans le référentiel Excel -
# les comparer n'aurait aucune valeur diagnostique.
URL_IGNORE_QUERY_DOMAINS = ("homeios.onelink.me",)

# Reconnaissance des noms de sequence "LOYAL"/"NON LOYAL" (onglets wording)
# et "FID"/"NON FID" (sections de l'onglet UTM) : vit ici (plutot que dans
# qa/classification.py) pour etre partagee sans creer d'import circulaire
# avec qa/excel_loader.py (qui a besoin de detecter l'onglet "frere" d'une
# sequence - voir find_sibling_sequence_sheet - et que classification.py
# importe deja pour sa propre detection pays/trigger).
SEQ_NUMBER_PATTERN = re.compile(r"\bSEQ\s*0*(\d+)\b", re.IGNORECASE)
NON_LOYAL_KEYWORDS = ("NON FID", "NON-FID", "NON LOYAL", "NON-LOYAL")
LOYAL_KEYWORDS = ("FID", "LOYAL")

GENERIC_SHEET_KEYWORD = "GENERIC"         # Mot-clé pour repérer l'onglet des composants génériques

# Mots-clés (insensibles à la casse) identifiant une classe de mentions
# légales / footer, dont on ignore les liens individuels (leur texte est
# deja couvert par le paragraphe complet qui les englobe).
LEGAL_MENTION_CLASS_KEYWORDS = ("legal", "footer")

# Fichiers par defaut pour l'execution standalone (python -m qa.engine)
HTML_FILE = "input/email.html"
EXCEL_FILE = "input/wording.xlsx"

AEM_PATTERN = re.compile(r"<%=?\s*.*?\s*%>", re.DOTALL)
VAR_PATTERN = re.compile(r"\$\{[^}]*\}")

EXCEL_PLACEHOLDER_PATTERN = re.compile(
    r"\[[^\]]+\]"
)

EXCLUDED_QA_ROWS = {
    ("subject", "subject"),
    ("header", "loyalty program name"),
}

# Elements ignores quel que soit leur composant (comparaison sur ELEMENT
# seul, insensible a la casse). "logo" : le texte de reference est le
# alt-text d'une image, jamais extrait/verifie par l'outil (voir
# extract_texts_from_textcontribution, qui ne lit que le texte visible).
EXCLUDED_QA_ELEMENTS = {"logo"}

# =========================
# CLASSEMENT HISTORIQUE (Pays / Trigger / Sequence)
# =========================
# Deduit du CONTENU du classeur (jamais du nom de fichier, pas fiable) :
# - Pays : mot-cle de langue trouve dans le nom de la colonne wording
#   (ex: "TRANSLATION GERMAN" -> Germany), ou domaine des URLs de l'onglet
#   UTM (ex: sephora.de -> Germany) en fallback.
# - Trigger : valeur de la colonne CAMPAIGN de l'onglet UTM (ex: "revamp_inac").
# Ambigu (ex: FRENCH -> France ou Belgique) : la detection reste une
# proposition, toujours modifiable dans le formulaire de confirmation avant
# archivage (voir qa.classification / render_archive_section dans app.py).
LANGUAGE_TO_COUNTRY = {
    "GERMAN": "Germany",
    "FRENCH": "France",
    "DUTCH": "Belgium",
    "FLEMISH": "Belgium",
    "ITALIAN": "Italy",
    "SPANISH": "Spain",
    "PORTUGUESE": "Portugal",
    "POLISH": "Poland",
    "SWEDISH": "Sweden",
    "DANISH": "Denmark",
    "NORWEGIAN": "Norway",
    "FINNISH": "Finland",
    "GREEK": "Greece",
    "CZECH": "Czechia",
    "SLOVAK": "Slovakia",
    "HUNGARIAN": "Hungary",
    "ROMANIAN": "Romania",
    "CROATIAN": "Croatia",
}

# Trie du suffixe le plus long au plus court a l'usage (ex: "co.uk" avant "uk").
DOMAIN_TO_COUNTRY = {
    "de": "Germany",
    "fr": "France",
    "be": "Belgium",
    "nl": "Netherlands",
    "es": "Spain",
    "it": "Italy",
    "pt": "Portugal",
    "pl": "Poland",
    "se": "Sweden",
    "dk": "Denmark",
    "no": "Norway",
    "fi": "Finland",
    "at": "Austria",
    "ch": "Switzerland",
    "ie": "Ireland",
    "gr": "Greece",
    "cz": "Czechia",
    "sk": "Slovakia",
    "hu": "Hungary",
    "ro": "Romania",
    "hr": "Croatia",
    "co.uk": "United Kingdom",
    "uk": "United Kingdom",
}

UNCLASSIFIED_LABEL = "Non classé"

# =========================
# QA - SEUILS DE SIMILARITE ET TYPES DE KO
# =========================
# Seuils de similarite (difflib.SequenceMatcher, 0-1), centralises pour que
# qa/matching.py et qa/wording_qa.py utilisent toujours la meme valeur.
SIM_NEAR_EXACT = 0.95              # au-dessus : ecart minime (typo/devise quasi-identique)
SIM_FUZZY = 0.80                   # au-dessus : ecart notable mais reconnaissable
SIM_CLOSEST_MATCH_ACCEPT = 0.85    # closest-match de la bonne classe au-dessus de ce seuil -> OK
SIM_CLOSEST_MATCH_MIN = 0.55       # en-dessous, aucun closest-match n'est propose
SIM_SHARED_BRANCH_EXACT = 0.995    # branche if/else "soeur" : n'accepte qu'un match quasi-parfait,
                                    # sinon un vrai ecart (ex: ponctuation) sur cette branche precise
                                    # serait masque a tort (voir run_wording_qa)

# Noms des types de KO (colonne "Error Type" des rapports). Chaque nom
# designe un seul cas precis, pour eviter qu'un meme libelle recouvre des
# situations de gravite differente (ex: l'ancien "Typo" melangeait 95-99%
# et 80-95% de similarite ; l'ancien "Wording differs" melangeait une vraie
# faute de wording et un wording jamais trouve dans le HTML).
KO_TEXT_MISSING = "Text missing"                                # une des deux chaines est vide, l'autre non
KO_CASE_MISMATCH = "Case mismatch"                               # memes mots, casse differente
KO_MINOR_TYPO = "Minor typo"                                     # similarite >= SIM_NEAR_EXACT
KO_FUZZY_MISMATCH = "Fuzzy mismatch"                             # similarite entre SIM_FUZZY et SIM_NEAR_EXACT
KO_CURRENCY_FORMAT_MISMATCH = "Currency format mismatch"         # meme montant, presentation/devise differente
KO_CONTENT_MISMATCH = "Content mismatch"                         # texte substantiellement different
KO_WRONG_COMPONENT = "Wording in wrong component"                # wording trouve, mais dans une autre classe technique
KO_COMPONENT_CLASS_NOT_FOUND = "Component class not found in HTML"  # texte matche par similarite, mais aucune classe audit-component detectee sur l'element HTML (impossible de verifier le composant)
KO_WORDING_MISMATCH = "Wording mismatch"                         # bonne classe technique, texte trop different (55-85%)
KO_WORDING_RELOCATED = "Wording relocated (similar text found)"  # wording Excel introuvable, mais texte proche trouve ailleurs
KO_UNEXPECTED_WORDING = "Unexpected wording (not in Excel)"      # texte HTML sans aucune correspondance Excel
KO_MISSING_WORDING = "Missing wording (not in HTML)"             # wording Excel jamais retrouve dans le HTML
KO_URL_PARAM_MISMATCH = "URL parameter mismatch (order/encoding)"  # meme destination, ordre/encodage different
KO_URL_NOT_FOUND = "URL not found"                                # aucune correspondance dans le HTML
KO_UNEXPECTED_URL = "Unexpected URL (not in Excel)"               # lien HTML jamais reference dans le referentiel Excel
KO_URL_WRONG_COMPONENT = "URL in wrong component"                 # URL trouvee, mais dans une autre classe technique
KO_URL_CLASS_NOT_FOUND = "URL component class not found in HTML"  # URL trouvee, mais aucune classe audit-component detectee autour du lien (impossible de verifier le composant)

# Note informative (colonne "Error Type"/"Raison" du rapport URL) posee sur
# une ligne OK, pas un KO : un "Exact (parametres ignores)" (domaine de
# URL_IGNORE_QUERY_DOMAINS) peut afficher un pourcentage de similarite bas
# alors que le match est valide, puisque seul le chemin compte pour ces
# domaines - sans cette note, le pourcentage bas parait a tort suspect.
INFO_URL_QUERY_IGNORED = "Domaine à query ignorée : % bas normal (seul le chemin est comparé, pas la query)"

# Echelle de criticite par type de KO, basee sur la gravite reelle du cas
# plutot que sur le chemin de code qui l'a produit (avant cette table,
# "Case mismatch" ressortait en High et "Typo" en Medium, soit l'inverse
# de la gravite percue par une equipe QA).
CRITICALITY_BY_ERROR_TYPE = {
    KO_CASE_MISMATCH: "Low",
    KO_MINOR_TYPO: "Low",
    KO_MISSING_WORDING: "Low",
    KO_FUZZY_MISMATCH: "Medium",
    KO_CURRENCY_FORMAT_MISMATCH: "Medium",
    KO_WORDING_MISMATCH: "Medium",
    KO_WORDING_RELOCATED: "Medium",
    KO_COMPONENT_CLASS_NOT_FOUND: "Medium",
    KO_URL_PARAM_MISMATCH: "Medium",
    KO_UNEXPECTED_URL: "Medium",
    KO_URL_CLASS_NOT_FOUND: "Medium",
    KO_TEXT_MISSING: "High",
    KO_CONTENT_MISMATCH: "High",
    KO_WRONG_COMPONENT: "High",
    KO_UNEXPECTED_WORDING: "High",
    KO_URL_NOT_FOUND: "High",
    KO_URL_WRONG_COMPONENT: "High",
}
DEFAULT_KO_CRITICALITY = "High"  # filet de securite si un Error Type n'est pas dans la table ci-dessus

# Ordre de gravite (du plus au moins urgent), utilise par qa/report.py pour
# trier les KO par criticite reelle plutot que par ordre alphabetique
# ("High" < "Low" < "Medium" alphabetiquement, ce qui n'a pas de sens ici).
CRITICALITY_ORDER = ["High", "Medium", "Low", ""]
