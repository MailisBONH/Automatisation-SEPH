import re

# =========================
# CONFIGURATION
# =========================
OUTPUT_DIR = "output"

WORDING_COLUMN_NAME = "TRANSLATION"       # Mot-clé (insensible à la casse) de la colonne wording dans l'Excel
CAMPAIGN_KEYWORD = "HugPlan S1 Bronze"    # Mot-clé pour sélectionner l'onglet dans l'Excel
MIN_TEXT_LENGTH = 3
DEBUG_MODE = True

URL_SHEET_KEYWORD = "UTM"                 # Mot-clé pour repérer l'onglet des URLs trackées
URL_COLUMN_NAME = "<< TRACKED URL >>"     # Colonne des URLs de référence dans cet onglet

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
