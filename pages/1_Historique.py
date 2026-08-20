"""
Page "Historique" : navigation par dossiers Pays > Trigger > Sequence dans
les runs archives automatiquement depuis la page principale. Le dossier
final affiche les 2 derniers rapports Excel conserves sur disque (voir
qa.archive, rotation automatique) et l'evolution du pourcentage de OK depuis
le tout premier run archive pour cette sequence (voir qa.history, jamais
purge). Une vue d'ensemble recapitule toutes les sequences avant la
navigation par dossiers, et un panneau de gestion permet de corriger une
mauvaise classification (renommer/fusionner) ou de supprimer une sequence.
"""
import os

import pandas as pd
import plotly.express as px
import streamlit as st

from qa.archive import delete_sequence_dir, list_reports, move_sequence_dir
from qa.classification import migrate_legacy_loyalty_sequences
from qa.history import (
    aggregate_error_types,
    build_history_tree,
    delete_classification,
    error_type_monthly_trend,
    load_classified_history,
    load_sequence_history,
    rewrite_classification,
)

st.set_page_config(page_title="Historique QA", layout="wide")

st.title("📈 Historique QA")
st.page_link("home.py", label="⬅️ Retour à l'outil QA")

MODE_LABELS = {"wording": "📝 Wording", "url": "🔗 URL", "combined": "🧩 Combiné"}

if "history_nav" not in st.session_state:
    st.session_state["history_nav"] = {"country": None, "trigger": None, "sequence": None}

nav = st.session_state["history_nav"]

# Reprise retroactive, silencieuse et idempotente : toute sequence encore
# archivee sous un ancien nom qualifie LOYAL/NON LOYAL/FID/NON FID (avant
# l'unification du nommage, voir qa.classification.normalize_sequence_name)
# est fusionnee vers son nom canonique avant meme de construire l'arbre.
migrated = migrate_legacy_loyalty_sequences()
if migrated:
    st.info(
        f"🔀 {len(migrated)} séquence(s) archivée(s) sous un ancien nom LOYAL/NON LOYAL/FID/NON FID "
        "fusionnée(s) automatiquement vers leur nom unifié."
    )

tree = build_history_tree()

if not tree:
    st.info(
        "Aucun rapport archivé pour l'instant. Depuis la page principale, lancez une comparaison "
        "et elle sera archivée automatiquement (une pop-up peut apparaître si le pays/trigger "
        "n'a pas pu être détecté)."
    )
    st.stop()


# =========================
# VUE D'ENSEMBLE
# =========================
def _latest_relevant_entry(entries):
    """Prefere la derniere entree 'combined' (vue la plus complete d'une
    sequence) si elle existe, sinon la derniere entree disponible quel que
    soit son type."""
    combined = [e for e in entries if e.get("mode") == "combined"]
    return combined[-1] if combined else entries[-1]


def _overview_rows(tree):
    rows = []
    for country, triggers in tree.items():
        for trigger, sequences in triggers.items():
            for sequence, entries in sequences.items():
                if not entries:
                    continue
                latest = _latest_relevant_entry(entries)
                rows.append({
                    "Pays": country,
                    "Trigger": trigger,
                    "Séquence": sequence,
                    "Dernier test": latest["timestamp"],
                    "Type": MODE_LABELS.get(latest.get("mode"), latest.get("mode")),
                    "% OK": latest["score"],
                    "Nb tests": len(entries),
                })
    return rows


with st.expander("📊 Vue d'ensemble (toutes les séquences)", expanded=nav["sequence"] is None):
    overview_df = pd.DataFrame(_overview_rows(tree)).sort_values("Dernier test", ascending=False)
    st.dataframe(overview_df, use_container_width=True, hide_index=True)

# =========================
# STATISTIQUES D'ERREURS AGREGEES (toutes sequences, toute la periode)
# =========================
with st.expander("📊 Statistiques des erreurs (toutes séquences)", expanded=False):
    all_classified_entries = load_classified_history()
    error_totals = aggregate_error_types(all_classified_entries)

    if error_totals:
        totals_df = pd.DataFrame(
            sorted(error_totals.items(), key=lambda kv: kv[1], reverse=True),
            columns=["Error Type", "Count"]
        )
        fig_totals = px.bar(
            totals_df, x="Count", y="Error Type", orientation="h",
            title="Types d'erreur les plus fréquents (historique complet)"
        )
        fig_totals.update_yaxes(categoryorder="total ascending")
        st.plotly_chart(fig_totals, use_container_width=True)

        top_types = set(totals_df["Error Type"].head(8))
        trend_rows = [r for r in error_type_monthly_trend(all_classified_entries) if r["error_type"] in top_types]
        if trend_rows:
            trend_df = pd.DataFrame(trend_rows)
            fig_trend = px.line(
                trend_df, x="period", y="count", color="error_type", markers=True,
                title="Évolution mensuelle des types d'erreur les plus fréquents",
                labels={"period": "Mois", "count": "Occurrences", "error_type": "Type d'erreur"}
            )
            st.plotly_chart(fig_trend, use_container_width=True)
    else:
        st.caption(
            "Aucune statistique d'erreur disponible pour l'instant — ce panneau se remplit à partir des "
            "prochains runs archivés (les runs déjà archivés avant cette fonctionnalité n'ont pas de détail "
            "par type d'erreur)."
        )

st.divider()

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

    # =========================
    # GESTION (renommer/fusionner, supprimer)
    # =========================
    st.divider()
    with st.expander("⚙️ Gérer cette séquence"):
        st.caption(
            "Corrige une mauvaise détection automatique ou fusionne un doublon "
            "(ex: une séquence classée sous deux noms différents avant recoupement)."
        )

        with st.form(f"rename_form_{country}_{trigger}_{sequence}"):
            col1, col2, col3 = st.columns(3)
            with col1:
                new_country = st.text_input("Pays", value=country)
            with col2:
                new_trigger = st.text_input("Trigger", value=trigger)
            with col3:
                new_sequence = st.text_input("Séquence", value=sequence)

            renamed = st.form_submit_button("💾 Renommer / fusionner")

        if renamed:
            new_country, new_trigger, new_sequence = new_country.strip(), new_trigger.strip(), new_sequence.strip()
            if not (new_country and new_trigger and new_sequence):
                st.error("Les trois champs sont obligatoires.")
            elif (new_country, new_trigger, new_sequence) == (country, trigger, sequence):
                st.info("Rien à changer.")
            else:
                move_sequence_dir(country, trigger, sequence, new_country, new_trigger, new_sequence)
                rewrite_classification(country, trigger, sequence, new_country, new_trigger, new_sequence)
                nav["country"], nav["trigger"], nav["sequence"] = new_country, new_trigger, new_sequence
                st.success(f"Renommé en **{new_country} / {new_trigger} / {new_sequence}**.")
                st.rerun()

        st.markdown("---")
        st.caption(
            "⚠️ Suppression définitive : les rapports Excel archivés ET l'historique des scores "
            "de cette séquence seront perdus. Cette action est irréversible."
        )
        confirm_delete = st.checkbox(
            "Je confirme vouloir supprimer cette séquence définitivement",
            key=f"confirm_delete_{country}_{trigger}_{sequence}"
        )
        if st.button("🗑️ Supprimer cette séquence", disabled=not confirm_delete):
            delete_sequence_dir(country, trigger, sequence)
            delete_classification(country, trigger, sequence)
            nav["country"], nav["trigger"], nav["sequence"] = None, None, None
            st.success("Séquence supprimée.")
            st.rerun()
