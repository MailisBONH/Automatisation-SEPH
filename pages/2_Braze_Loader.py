"""
Page "Braze (exploration)" : demonstrateur visuel du branchement
qa.braze_loader -> qa.html_extraction.extract_texts_from_html, pour la
migration vers Braze en cours sur la branche `braze` (voir qa/braze_loader.py
pour le detail des limitations connues - Liquid vs AEM, absence de
convention "audit-component-*" cote Braze).

Volontairement SEPARE du pipeline de comparaison principal (home.py) : sans
acces reel a un template Braze ni referentiel Excel adapte, il n'y a encore
rien a comparer - cette page ne fait que prouver que la recuperation
(mock ou API reelle) et l'extraction fonctionnent, pas une QA complete.
"""
import pandas as pd
import streamlit as st

from qa.branding import inject_theme, render_page_title, render_sidebar_logo
from qa.braze_loader import (
    BRAZE_MOCK_MODE,
    BRAZE_REST_ENDPOINT,
    BrazeAPIError,
    get_email_template_html,
    list_email_templates,
)
from qa.html_extraction import extract_texts_from_html


@st.cache_data(show_spinner=False)
def _cached_list_email_templates(tag: str, mock_mode: bool, endpoint: str):
    """Evite de rappeler l'API Braze (ou de re-filtrer la liste mock) a
    CHAQUE rerun Streamlit (tout widget de la page en declenche un) alors
    que la liste ne change en realite qu'entre deux sessions - meme
    raisonnement que le cache des lectures d'historique sur la page
    Historique (voir pages/1_Historique.py::_load_history_overview).
    `mock_mode`/`endpoint` dans la cle (en plus de `tag`) : sans eux, un
    premier appel en mode mock resterait servi depuis le cache apres avoir
    bascule BRAZE_MOCK_MODE/BRAZE_REST_ENDPOINT (redemarrage de l'app,
    rarissime en usage reel mais gratuit a couvrir)."""
    return list_email_templates(tag=tag or None)

st.set_page_config(page_title="Braze (exploration)", layout="wide")
inject_theme()
render_sidebar_logo()
render_page_title("🧪 Braze (exploration)")

st.caption(
    "Page de test pour la migration vers Braze (branche `braze`) - ne fait "
    "pas encore de QA complete, juste la recuperation + extraction du HTML "
    "d'un template."
)

if BRAZE_MOCK_MODE:
    st.warning(
        "🧩 Mode **mock** actif (`BRAZE_MOCK_MODE`) : aucune requete reseau "
        "reelle, les templates ci-dessous sont des exemples statiques. "
        "Configurez `BRAZE_API_KEY` et `BRAZE_REST_ENDPOINT`, puis "
        "`BRAZE_MOCK_MODE=0`, pour interroger une vraie instance Braze."
    )
else:
    st.success(f"🔌 Mode API reelle actif - endpoint : `{BRAZE_REST_ENDPOINT}`")

st.divider()
st.header("1️⃣ Entrée : identifiant du template Braze")
st.markdown(
    "Contrairement à l'outil AEM (côté **QA Email Tool**, on **uploade un "
    "fichier HTML**), Braze stocke les templates côté serveur : ici, il n'y "
    "a **aucun fichier à fournir**. L'entrée est un **identifiant de "
    "template** (`email_template_id`) - celui qu'on envoie ensuite à l'API "
    "Braze pour qu'elle nous retourne le HTML correspondant."
)
st.caption(
    "La liste ci-dessous (`list_email_templates()`) sert uniquement à "
    "choisir cet identifiant sans avoir à le connaître/copier-coller à "
    "l'avance - en mode mock, ce sont 2 templates d'exemple fixes ; en mode "
    "API réelle, la vraie liste des templates de votre compte Braze."
)

try:
    templates = _cached_list_email_templates(tag="", mock_mode=BRAZE_MOCK_MODE, endpoint=BRAZE_REST_ENDPOINT)
except BrazeAPIError as e:
    st.error(f"❌ Impossible de lister les templates Braze : {e}")
    templates = []

if not templates:
    st.info("Aucun template disponible.")
    st.stop()

template_labels = {t["email_template_id"]: t.get("template_name", t["email_template_id"]) for t in templates}
selected_id = st.selectbox(
    "Template email",
    options=list(template_labels.keys()),
    format_func=lambda tid: f"{template_labels[tid]} ({tid})",
)
st.code(f'email_template_id = "{selected_id}"', language="python")

if st.button("🔍 Charger et extraire"):
    st.divider()
    st.header("2️⃣ Récupération : HTML renvoyé par l'API pour cet identifiant")
    try:
        raw_html = get_email_template_html(selected_id)
    except BrazeAPIError as e:
        st.error(f"❌ Impossible de recuperer le template : {e}")
        st.stop()

    st.caption(
        "Équivalent Braze du fichier HTML uploadé côté AEM - sauf qu'ici, "
        "c'est l'API qui le fournit, pas un upload manuel."
    )
    with st.expander("📄 Voir le HTML brut", expanded=True):
        st.code(raw_html, language="html")

    st.divider()
    st.header("3️⃣ Sortie : textes trouvés par le moteur d'extraction")
    st.caption(
        "Ce HTML est passé dans le même moteur d'extraction que celui "
        "utilisé pour un HTML AEM (`qa.html_extraction.extract_texts_from_html`) "
        "- aucune comparaison à un référentiel Excel n'a lieu ici, voir la "
        "note en haut de page."
    )
    try:
        # extract_texts_from_html(raw_html) directement - PAS
        # qa.braze_loader.extract_texts_from_template(selected_id), qui
        # rappellerait get_email_template_html() une seconde fois pour le
        # meme template deja recupere ci-dessus (ligne 101) : en mode API
        # reelle, ca doublerait latence/appels reseau par clic pour rien,
        # et desynchroniserait potentiellement le HTML affiche de celui
        # reellement extrait si le template changeait entre les deux appels.
        extracted = extract_texts_from_html(raw_html)
    except Exception as e:  # noqa: BLE001 - page d'exploration, on veut voir n'importe quelle erreur d'extraction
        st.error(f"❌ Erreur pendant l'extraction : {e}")
        st.stop()

    st.subheader(f"Textes extraits : {len(extracted)}")

    if not extracted:
        st.warning(
            "⚠️ Aucun texte extrait. Attendu si ce template n'a pas les "
            "marqueurs AEM (`class=\"text-contribution\"`, "
            "`audit-component-*`) que le moteur d'extraction actuel "
            "recherche - voir la docstring de "
            "`qa.braze_loader.extract_texts_from_template` (equivalent a "
            "l'appel ci-dessus, en une seule etape). Pas une erreur "
            "de branchement : la preuve que l'extraction devra evoluer "
            "avant de QA un vrai template Braze."
        )
    else:
        st.dataframe(pd.DataFrame(extracted), use_container_width=True)
