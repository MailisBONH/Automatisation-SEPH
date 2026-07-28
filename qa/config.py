import re

# =========================
# CONFIGURATION
# =========================
OUTPUT_DIR = "output"

WORDING_COLUMN_NAME = "TEXT EN"           # Colonne texte dans l'Excel
CAMPAIGN_KEYWORD = "HugPlan S1 Bronze"    # Mot-clé pour sélectionner l'onglet dans l'Excel
MIN_TEXT_LENGTH = 3
DEBUG_MODE = True

URL_SHEET_KEYWORD = "UTM"                 # Mot-clé pour repérer l'onglet des URLs trackées
URL_COLUMN_NAME = "<< TRACKED URL >>"     # Colonne des URLs de référence dans cet onglet

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
