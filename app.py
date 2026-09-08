"""
Point d'entree de l'app : declare la navigation multipage (v2, st.navigation)
entre l'outil QA (home.py) et la page Historique (pages/1_Historique.py).
La barre de navigation native est masquee (position="hidden") : la page
Historique est accessible via le bouton "📈 Historique" en haut a droite de
home.py.
"""
import sys

import streamlit as st

# Certaines consoles Windows utilisent un encodage (cp1252) qui ne sait pas
# encoder les emojis utilises dans les print() de debug (ex: "📄 Rapport..."),
# ce qui fait planter l'appli des qu'un tel print s'execute hors du buffer
# capture par run_with_debug_capture (ex: qa.combined_report, ou qa.report
# lors de l'archivage d'un run en mode batch). On force l'UTF-8 une fois pour
# tout le process, avant que le moindre print ne s'execute.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

home_page = st.Page("home.py", title="QA Email Tool", icon="📧", default=True)
historique_page = st.Page("pages/1_Historique.py", title="Historique", icon="📈")
braze_loader_page = st.Page("pages/2_Braze_Loader.py", title="Braze (exploration)", icon="🧪")

pg = st.navigation([home_page, historique_page, braze_loader_page], position="hidden")
pg.run()
