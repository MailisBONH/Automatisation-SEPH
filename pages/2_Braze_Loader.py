"""
Page "Braze (exploration)" : demonstrateur visuel pour la migration vers
Braze en cours sur la branche `braze` (voir qa/braze_loader.py pour le
detail des limitations connues - Liquid vs AEM, absence de convention
"audit-component-*" cote Braze).

Volontairement SEPARE du pipeline de comparaison principal (home.py).
Deux etapes :
1-3 : branchement loader -> extraction (qa.html_extraction.extract_texts_from_html),
     preuve de plomberie sans encore de QA complete (pas de referentiel).
4   : prototype de QA complete via le systeme de traduction natif Braze
     (translation_map en guise de referentiel, voir
     qa.braze_loader.run_translation_based_qa) - produit un vrai resultat
     OK/KO, mais repose sur des endpoints Braze en EARLY ACCESS (non
     confirmes en reel) et sur l'hypothese d'alignement positionnel decrite
     dans qa/braze_loader.py.
"""
import contextlib
import io

import pandas as pd
import streamlit as st

from qa.branding import inject_theme, render_page_title, render_sidebar_logo
from qa.braze_loader import (
    BRAZE_MOCK_MODE,
    BRAZE_REST_ENDPOINT,
    BrazeAPIError,
    get_email_template_html,
    get_mock_rendered_html,
    get_translation_map,
    list_email_templates,
    list_template_locales,
    run_translation_based_qa,
)
from qa.html_extraction import extract_texts_from_html


def _run_silencing_debug_prints(fn, *args, **kwargs):
    """Execute fn en redirigeant tout ce qu'elle imprime (stdout) vers un
    buffer en memoire plutot que la console reelle - meme principe que
    home.run_with_debug_capture (sans le besoin de sa variante thread-safe,
    fn ici n'est jamais parallelisee). Necessaire : les print() de debug de
    qa.wording_qa/qa.html_extraction (actives par defaut, voir
    qa.config.DEBUG_MODE) contiennent des emojis qui font planter stdout
    avec une UnicodeEncodeError des que la console/le flux de sortie du
    process Streamlit n'est pas en UTF-8 (observe en conditions reelles sur
    cette page le 2026-10-01, malgre le `sys.stdout.reconfigure(encoding="utf-8")`
    fait par app.py au demarrage - visiblement pas toujours suffisant selon
    la provenance du flux reellement utilise a l'execution). Rediriger vers
    un io.StringIO() contourne completement la question (aucun encodage
    reel n'intervient), plutot que de corriger chaque print() individuel."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


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
        extracted, _ = _run_silencing_debug_prints(extract_texts_from_html, raw_html)
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

st.divider()
st.header("4️⃣ Prototype : QA via le système de traduction natif Braze")
st.caption(
    "Piste retenue pour remplacer le référentiel Excel côté Braze (voir "
    "discussion du 2026-10-01) : au lieu de comparer le HTML à un classeur "
    "Excel, on le compare au `translation_map` natif Braze (id de "
    "traduction → texte attendu par langue). "
    "⚠️ Les endpoints de traduction Braze sont en **early access** (non "
    "confirmés en réel) — voir `qa/braze_loader.py` pour le détail."
)

locale_col, _ = st.columns([2, 3])
with locale_col:
    try:
        locales = list_template_locales(selected_id)
    except BrazeAPIError as e:
        st.error(f"❌ Impossible de lister les locales : {e}")
        locales = []

    if not locales:
        st.info("Aucune locale disponible pour ce template.")
        locale_id = None
    else:
        locale_labels = {l["uuid"]: f"{l.get('name', l['uuid'])} ({l.get('locale_key', l['uuid'])})" for l in locales}
        locale_id = st.selectbox(
            "Locale",
            options=list(locale_labels.keys()),
            format_func=lambda uuid: locale_labels[uuid],
            key="translation_locale",
        )

if locale_id:
    try:
        translation_map = get_translation_map(selected_id, locale_id)
    except BrazeAPIError as e:
        st.error(f"❌ Impossible de récupérer le translation_map : {e}")
        translation_map = {}

    with st.expander(f"🗺️ translation_map pour {locale_labels[locale_id]}", expanded=False):
        if translation_map:
            st.dataframe(
                pd.DataFrame(translation_map.items(), columns=["translation_id", "Wording attendu"]),
                use_container_width=True,
            )
        else:
            st.caption("Aucune traduction trouvée pour cette locale (repli sur le texte source du template).")

    st.markdown(
        "**HTML rendu** (ce qu'un destinataire de cette locale recevrait réellement, "
        "une fois le Liquid résolu) :"
    )
    st.caption(
        "Aucun endpoint Braze ne fournit ce rendu par locale pour un template seul "
        "(ce serait l'export d'une campagne/Canvas réellement envoyée) - en mode "
        "mock, un exemple est pré-rempli ci-dessous ; en mode API réelle, collez le "
        "HTML d'un envoi réel pour cette locale."
    )
    default_rendered = get_mock_rendered_html(locale_id) if BRAZE_MOCK_MODE else ""
    rendered_html_input = st.text_area(
        "HTML rendu", value=default_rendered, height=150, label_visibility="collapsed",
    )

    if st.button("🔍 Comparer au translation_map"):
        try:
            raw_template_html = get_email_template_html(selected_id)
        except BrazeAPIError as e:
            st.error(f"❌ Impossible de récupérer le template source : {e}")
            st.stop()

        results, _ = _run_silencing_debug_prints(
            run_translation_based_qa, raw_template_html, rendered_html_input, translation_map
        )

        if not results:
            st.warning(
                "⚠️ Aucun résultat. Le template source ne contient peut-être aucun tag "
                "`{% translation %}`, ou le HTML rendu est vide."
            )
        else:
            ok = sum(1 for r in results if r["Status"] == "OK")
            total = len(results)
            st.metric("Score", f"{round(ok / total * 100, 1)}%", f"{ok}/{total} OK")
            st.dataframe(pd.DataFrame(results), use_container_width=True)
