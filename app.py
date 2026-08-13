"""
Point d'entree de l'app : declare la navigation multipage (v2, st.navigation)
entre l'outil QA (home.py) et la page Historique (pages/1_Historique.py).
La barre de navigation native est masquee (position="hidden") : la page
Historique est accessible via le bouton "📈 Historique" en haut a droite de
home.py.
"""
import streamlit as st

home_page = st.Page("home.py", title="QA Email Tool", icon="📧", default=True)
historique_page = st.Page("pages/1_Historique.py", title="Historique", icon="📈")

pg = st.navigation([home_page, historique_page], position="hidden")
pg.run()
