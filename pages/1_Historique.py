"""
Page "Historique" : navigation par dossiers Pays > Trigger > Sequence dans
les runs archives manuellement depuis la page principale (bouton
"💾 Enregistrer dans l'historique"). Le dossier final affiche les 2 derniers
rapports Excel conserves sur disque (voir qa.archive, rotation automatique)
et l'evolution du pourcentage de bugs depuis le tout premier run archive
pour cette sequence (voir qa.history, jamais purge).
"""
import os

import pandas as pd
import plotly.express as px
import streamlit as st

from qa.archive import list_reports
from qa.history import build_history_tree, load_sequence_history

st.set_page_config(page_title="Historique QA", layout="wide")

st.title("📈 Historique QA")
st.page_link("home.py", label="⬅️ Retour à l'outil QA")

if "history_nav" not in st.session_state:
    st.session_state["history_nav"] = {"country": None, "trigger": None, "sequence": None}

nav = st.session_state["history_nav"]

tree = build_history_tree()

if not tree:
    st.info(
        "Aucun rapport archivé pour l'instant. Depuis la page principale, lancez une comparaison "
        "puis cliquez sur **💾 Enregistrer dans l'historique** pour qu'elle apparaisse ici."
    )
    st.stop()

# =========================
# FIL D'ARIANE
# =========================
crumb_cols = st.columns([1, 1, 1, 5])

with crumb_cols[0]:
    if st.button("🏠 Pays", use_container_width=True):
        nav["country"] = None
        nav["trigger"] = None
        nav["sequence"] = None
        st.rerun()

with crumb_cols[1]:
    if nav["country"] and st.button(f"📁 {nav['country']}", use_container_width=True):
        nav["trigger"] = None
        nav["sequence"] = None
        st.rerun()

with crumb_cols[2]:
    if nav["trigger"] and st.button(f"📁 {nav['trigger']}", use_container_width=True):
        nav["sequence"] = None
        st.rerun()

st.divider()


def folder_grid(items, key_prefix):
    """Affiche une grille de boutons "dossier", retourne l'item choisi (ou None)."""
    chosen = None
    cols = st.columns(4)
    for i, item in enumerate(sorted(items)):
        with cols[i % 4]:
            if st.button(f"📁 {item}", key=f"{key_prefix}_{item}", use_container_width=True):
                chosen = item
    return chosen


# =========================
# NAVIGATION
# =========================
if nav["country"] is None:
    st.subheader("Choisissez un pays")
    chosen = folder_grid(tree.keys(), "nav_country")
    if chosen:
        nav["country"] = chosen
        st.rerun()

elif nav["trigger"] is None:
    st.subheader(f"{nav['country']} — choisissez un trigger")
    chosen = folder_grid(tree[nav["country"]].keys(), "nav_trigger")
    if chosen:
        nav["trigger"] = chosen
        st.rerun()

elif nav["sequence"] is None:
    st.subheader(f"{nav['country']} / {nav['trigger']} — choisissez une séquence")
    chosen = folder_grid(tree[nav["country"]][nav["trigger"]].keys(), "nav_sequence")
    if chosen:
        nav["sequence"] = chosen
        st.rerun()

else:
    country, trigger, sequence = nav["country"], nav["trigger"], nav["sequence"]
    st.subheader(f"{country} / {trigger} / {sequence}")
    st.caption(
        "Wording et URL d'une même séquence partagent ce dossier et alimentent, "
        "dès que les deux sont disponibles, un rapport combiné."
    )

    MODE_LABELS = {"wording": "📝 Wording", "url": "🔗 URL", "combined": "🧩 Combiné"}

    st.markdown("**Rapports disponibles (les 2 plus récents par type, les précédents sont supprimés automatiquement) :**")
    any_report = False
    for mode, label in MODE_LABELS.items():
        reports = list_reports(country, trigger, sequence, mode=mode)
        if not reports:
            continue
        any_report = True
        st.write(label)
        report_cols = st.columns(len(reports))
        for col, path in zip(report_cols, reports):
            with col:
                with open(path, "rb") as f:
                    st.download_button(
                        f"⬇️ {os.path.basename(path)}",
                        data=f,
                        file_name=os.path.basename(path),
                        key=f"dl_{path}",
                        use_container_width=True
                    )
    if not any_report:
        st.caption("Aucun rapport disponible sur le disque pour cette séquence.")

    entries = load_sequence_history(country, trigger, sequence)

    st.markdown("**Évolution du pourcentage de OK depuis le premier test :**")
    if entries:
        history_df = pd.DataFrame(entries)
        history_df["Type"] = history_df["mode"].map(MODE_LABELS).fillna(history_df["mode"])

        fig = px.line(
            history_df,
            x="timestamp",
            y="score",
            color="Type",
            markers=True,
            title="Évolution du % de OK",
            labels={"timestamp": "Date", "score": "% de OK"}
        )
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(
            history_df[["timestamp", "Type", "total", "ok", "ko", "score"]]
                .rename(columns={"score": "% OK"})
                .iloc[::-1],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.caption("Aucun historique de score pour cette séquence.")
