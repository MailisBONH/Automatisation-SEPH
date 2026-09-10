"""
Habillage visuel "Sephora friendly" partage par les 3 pages de l'app
(home.py, pages/1_Historique.py, pages/2_Braze_Loader.py) : palette
noir / blanc / dore et typographie elegante, sans logo officiel integre
(aucun asset de marque fourni - identite visuelle generaliste Sephora,
voir README). Volontairement limite au "chrome" de l'appli (bandeau,
boutons, titres, liens de navigation) : les couleurs semantiques propres a
Streamlit (vert OK / rouge KO sur les tableaux et messages, orange
avertissement) sont laissees intactes, elles portent un sens fonctionnel
independant du design et ne doivent jamais preter a confusion.

Theme adaptatif clair/sombre : ce module ne force AUCUNE couleur de fond
ni de texte globale, et .streamlit/config.toml ne definit volontairement
AUCUNE section [theme] (voir son commentaire) - la moindre cle de theme
personnalisee (meme `primaryColor` seul) fige le theme resultant sur la
base claire de Streamlit et desactive son adaptation automatique au
reglage clair/sombre du systeme/navigateur de la personne, verifie
empiriquement sur cette version. L'app suit donc nativement ce reglage.
Les elements de marque ci-dessous (bandeau, boutons, liens de navigation)
utilisent uniquement des couleurs qui restent lisibles sur un fond clair
COMME sur un fond sombre (noir + dore, jamais de fond blanc/gris clair
fige) - c'est ce qui les rend valables dans les deux themes sans media
query `prefers-color-scheme` dediee. Seul l'accent dore des radios/cases a
cocher (normalement pilote par `primaryColor`, indisponible ici) est
recree en CSS pur via `:has()`, en best-effort : sur un navigateur qui ne
supporte pas `:has()` (tres ancien), ces widgets retombent simplement sur
l'accent rouge par defaut de Streamlit plutot que de casser quoi que ce
soit.
"""
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

.sephora-banner {{
    background-color: {_BLACK};
    margin: -1rem -1rem 1.5rem -1rem;
    padding: 1.1rem 2rem;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    flex-wrap: wrap;
    border-bottom: 2px solid {_GOLD};
}}
.sephora-banner .wordmark {{
    font-family: 'Marcellus', Georgia, serif;
    color: #ffffff;
    font-size: 1.6rem;
    letter-spacing: 0.35em;
    text-transform: uppercase;
}}
.sephora-banner .subtitle {{
    color: {_GOLD};
    font-size: 0.95rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 500;
}}

/* Fond noir + bordure doree quel que soit le theme clair/sombre ambiant
   (jamais de fond blanc/gris fige) : le bouton reste lisible et bien
   delimite sur les deux. */
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

div[data-testid="stPageLink"] a {{
    border: 1px solid {_GOLD} !important;
    border-radius: 2px !important;
}}
div[data-testid="stPageLink"] a:hover {{
    background-color: {_GOLD}33 !important;
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
    contenu Streamlit - injecte le CSS partage (police, bandeau, boutons,
    liens de navigation). Streamlit re-affiche ce <style> a chaque run
    (aucun etat a nettoyer, pas d'effet de bord a repeter l'appel)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_banner(subtitle: str) -> None:
    """Bandeau noir "SEPHORA" en haut de page avec le sous-titre de la
    page courante (ex. "QA Email Tool", "Historique QA") - remplace le
    st.title() brut par un habillage coherent entre les 3 pages."""
    st.markdown(
        f'<div class="sephora-banner">'
        f'<span class="wordmark">Sephora</span>'
        f'<span class="subtitle">{subtitle}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )
