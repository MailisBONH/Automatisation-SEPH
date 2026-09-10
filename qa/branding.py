"""
Habillage visuel "Sephora friendly" partage par les 3 pages de l'app
(home.py, pages/1_Historique.py, pages/2_Braze_Loader.py) : palette
noir / blanc / dore et typographie elegante, sans logo officiel integre
(aucun asset de marque fourni - identite visuelle generaliste Sephora,
voir README). Volontairement limite au "chrome" de l'appli (sidebar de
navigation, titres, boutons) : les couleurs semantiques propres a
Streamlit (vert OK / rouge KO sur les tableaux et messages, orange
avertissement) sont laissees intactes, elles portent un sens fonctionnel
independant du design et ne doivent jamais preter a confusion.

La navigation entre pages utilise la sidebar NATIVE de Streamlit
(app.py, st.navigation(position="sidebar")) plutot qu'un menu maison en
HTML/CSS par-dessus une navigation masquee - deux tentatives precedentes
(boutons encadres en haut a droite, puis liens fusionnes au bas d'un
bandeau) ont ete jugees peu naturelles malgre les retouches. La sidebar
est le pattern multipage attendu d'une app Streamlit ; render_sidebar_logo()
y place le wordmark "SEPHORA" via st.logo() (emplacement dedie de
Streamlit en tete de sidebar, AU-DESSUS de la liste de pages generee
automatiquement - contrairement a du contenu ajoute via `with st.sidebar:`
depuis une page, qui atterrit apres coup, sous cette liste).

Theme adaptatif clair/sombre : ce module ne force AUCUNE couleur de fond
ni de texte globale sur le CONTENU PRINCIPAL, et .streamlit/config.toml ne
definit volontairement AUCUNE section [theme] (voir son commentaire) - la
moindre cle de theme personnalisee (meme `primaryColor` seul) fige le
theme resultant sur la base claire de Streamlit et desactive son
adaptation automatique au reglage clair/sombre du systeme/navigateur de la
personne, verifie empiriquement sur cette version. Le contenu principal
suit donc nativement ce reglage. La SIDEBAR fait exception assumee : son
fond reste noir en permanence, dans les deux themes (identite de marque),
d'ou un choix explicite de texte blanc/dore dessus plutot que de compter
sur la couleur de texte automatique du theme (qui, elle, changerait de
sens sur un fond force). Seul l'accent dore des radios/cases a cocher du
contenu principal (normalement pilote par `primaryColor`, indisponible
ici) est recree en CSS pur via `:has()`, en best-effort : sur un
navigateur qui ne supporte pas `:has()` (tres ancien), ces widgets
retombent simplement sur l'accent rouge par defaut de Streamlit plutot que
de casser quoi que ce soit.
"""
from pathlib import Path

import streamlit as st

_BLACK = "#111111"
_GOLD = "#B8975A"

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Marcellus&family=Jost:wght@300;400;500;600&display=swap');

html {{
    color-scheme: light dark;
}}
html, body, [class*="css"] {{
    font-family: 'Jost', 'Helvetica Neue', Arial, sans-serif;
}}

h1, h2, h3 {{
    font-family: 'Marcellus', Georgia, serif;
    letter-spacing: 0.01em;
}}

.sephora-page-title {{
    font-family: 'Marcellus', Georgia, serif;
    letter-spacing: 0.02em;
    padding-bottom: 0.6rem;
    margin-bottom: 1.5rem;
    border-bottom: 2px solid {_GOLD};
}}

/* Sidebar de navigation native (voir app.py) : fond noir permanent (les
deux themes), donc texte/icones clairs fixes ici plutot que la couleur
automatique du theme - voir docstring. */
[data-testid="stSidebar"] {{
    background-color: {_BLACK};
}}
[data-testid="stSidebar"] * {{
    color: #f5f0e8;
}}
[data-testid="stSidebarHeader"] {{
    padding-top: 1rem;
}}
[data-testid="stSidebarNav"] a[aria-current="page"] {{
    background-color: {_GOLD}26 !important;
    border-right: 3px solid {_GOLD};
}}
[data-testid="stSidebarNav"] a[aria-current="page"] * {{
    color: {_GOLD} !important;
    font-weight: 600;
}}

/* Fond noir + bordure doree quel que soit le theme clair/sombre ambiant
   du contenu principal (jamais de fond blanc/gris fige) : le bouton reste
   lisible et bien delimite sur les deux. */
div[data-testid="stButton"] button,
div[data-testid="stFormSubmitButton"] button,
div[data-testid="stDownloadButton"] button {{
    background-color: {_BLACK};
    color: #ffffff;
    border: 1px solid {_GOLD};
    border-radius: 2px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    font-size: 0.85rem;
    font-weight: 500;
}}
div[data-testid="stButton"] button:hover,
div[data-testid="stFormSubmitButton"] button:hover,
div[data-testid="stDownloadButton"] button:hover {{
    background-color: {_GOLD};
    border-color: {_GOLD};
    color: {_BLACK};
}}

/* Accent dore des radios/cases a cocher cochees (a defaut de
   `primaryColor`, voir docstring) - cible le disque/la case visible
   (rendus par BaseWeb en div/span, l'input natif reste invisible), pas
   l'input lui-meme. */
label[data-baseweb="radio"]:has(input:checked) > div:first-child,
label[data-baseweb="radio"]:has(input:checked) > div:first-child > div {{
    background-color: {_GOLD} !important;
    border-color: {_GOLD} !important;
}}
label[data-baseweb="checkbox"]:has(input:checked) > span:first-child {{
    background-color: {_GOLD} !important;
    border-color: {_GOLD} !important;
}}
</style>
"""


def inject_theme() -> None:
    """A appeler juste apres st.set_page_config(), avant tout autre
    contenu Streamlit - injecte le CSS partage (police, sidebar, boutons,
    titre de page). Streamlit re-affiche ce <style> a chaque run (aucun
    etat a nettoyer, pas d'effet de bord a repeter l'appel)."""
    st.markdown(_CSS, unsafe_allow_html=True)


_LOGO_PATH = str(Path(__file__).resolve().parent.parent / "assets" / "sephora_wordmark.svg")


def render_sidebar_logo() -> None:
    """Wordmark "SEPHORA" (SVG maison, aucun logo officiel fourni - voir
    docstring du module) dans l'emplacement dedie de Streamlit en tete de
    sidebar (st.logo(), voir app.py, st.navigation(position="sidebar")) -
    rendu AU-DESSUS de la liste des 3 pages generee automatiquement, pas
    en dessous. A appeler une fois par page, comme inject_theme()."""
    st.logo(_LOGO_PATH)


def render_page_title(title: str) -> None:
    """Titre de page en tete du contenu principal (remplace st.title()) :
    police de marque et fine ligne doree, sans repeter le wordmark
    "SEPHORA" deja porte par la sidebar (voir render_sidebar_logo())."""
    st.markdown(f'<h1 class="sephora-page-title">{title}</h1>', unsafe_allow_html=True)
