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
    HISTORY_PATH,
    aggregate_error_types,
    build_history_tree,
    delete_classification,
    error_type_monthly_trend,
    export_history_csv,
    export_history_excel,
    load_classified_history,
    load_sequence_history,
    migrate_legacy_jsonl_history,
    rewrite_classification,
)
from qa.report_diff import diff_uploaded_reports, load_last_two_reports_diff

st.set_page_config(page_title="Historique QA", layout="wide")

st.title("📈 Historique QA")
st.page_link("home.py", label="⬅️ Retour à l'outil QA")

MODE_LABELS = {"wording": "📝 Wording", "url": "🔗 URL", "combined": "🧩 Combiné"}

if "history_nav" not in st.session_state:
    st.session_state["history_nav"] = {"country": None, "trigger": None, "sequence": None}

nav = st.session_state["history_nav"]

MAX_RECENT_SEQUENCES = 5
if "recent_sequences" not in st.session_state:
    # Liste de (country, trigger, sequence), la plus recente en tete - voir
    # _record_recent_sequence et les boutons "Recemment consultees" plus
    # bas, pour revenir directement a une sequence deja regardee plus tot
    # dans la session sans re-cliquer les 3 niveaux de dossiers a chaque fois.
    st.session_state["recent_sequences"] = []


def _record_recent_sequence(country, trigger, sequence):
    """Enregistre (country, trigger, sequence) en tete de l'historique de
    consultation de la session (sans doublon, borne a MAX_RECENT_SEQUENCES) -
    a appeler des qu'une sequence est effectivement affichee (nav complet)."""
    recent = [k for k in st.session_state["recent_sequences"] if k != (country, trigger, sequence)]
    recent.insert(0, (country, trigger, sequence))
    st.session_state["recent_sequences"] = recent[:MAX_RECENT_SEQUENCES]


def _forget_recent_sequence(country, trigger, sequence):
    """Retire (country, trigger, sequence) de l'historique de consultation de
    la session s'il y figure - a appeler quand cette identite vient d'etre
    renommee/fusionnee ou supprimee, pour ne pas laisser un quicklink mort
    pointer vers un pays/trigger/sequence qui n'existe plus (voir aussi le
    garde-fou de navigation plus bas qui gere le meme cas de figure)."""
    st.session_state["recent_sequences"] = [
        k for k in st.session_state["recent_sequences"] if k != (country, trigger, sequence)
    ]

# Compteur global, incremente uniquement quand un evenement change ce que
# load_classified_history()/build_history_tree() retournerait (renommage/
# fusion, suppression, migration retroactive - voir _bump_history_version
# et tous ses appelants plus bas) - passe en argument a
# _load_history_overview ci-dessous pour que st.cache_data traite un
# changement de version comme une nouvelle cle de cache, sans avoir a
# deviner "la base a-t-elle change" via un mtime de fichier ou un TTL.
if "history_version" not in st.session_state:
    st.session_state["history_version"] = 0


def _bump_history_version():
    """A appeler juste apres toute mutation reussie de l'historique classe
    (rename/fusion, suppression, migration retroactive) - voir le
    commentaire au-dessus de l'initialisation de history_version.
    Sans cet appel, _load_history_overview continuerait de servir un arbre
    perime indefiniment (le cache n'a ni TTL ni invalidation automatique)."""
    st.session_state["history_version"] += 1


def _history_db_stat():
    """(mtime_ns, taille) de qa.history.HISTORY_PATH (relatif au cwd courant,
    voir qa.config.OUTPUT_DIR), ou None si la base n'existe pas encore -
    passe a _load_history_overview en plus de history_version pour que TOUTE
    ecriture dans cette base (pas seulement celles faites depuis CETTE page)
    invalide le cache : record_run (archivage d'un nouveau run depuis
    home.py), migrate_legacy_jsonl_history/migrate_legacy_loyalty_sequences,
    ou meme une modification faite depuis un autre onglet/session, ecrivent
    tous dans le meme fichier SQLite et en changent donc le mtime/la taille -
    contrairement a history_version (compteur propre a CETTE page/session),
    qui ne peut par construction detecter que ses propres mutations."""
    if not os.path.exists(HISTORY_PATH):
        return None
    stat = os.stat(HISTORY_PATH)
    return stat.st_mtime_ns, stat.st_size


@st.cache_data(show_spinner=False)
def _load_history_overview(version: int, cwd: str, db_stat):
    """
    Lecture de l'historique classe + construction de l'arbre de navigation
    (voir qa.history.load_classified_history/build_history_tree), mise en
    cache par st.cache_data : sans ce cache, cette lecture SQL + construction
    d'arbre s'executerait a chaque interaction avec N'IMPORTE QUEL widget de
    la page (taper dans la recherche, cocher une case...), Streamlit
    reexecutant tout le script a chaque rerun - alors que le resultat ne
    change en realite que si la base a ete ecrite depuis le dernier rerun.

    Cle de cache a 3 composantes, toutes necessaires :
    - `db_stat` (voir _history_db_stat) : (mtime_ns, taille) du fichier
      SQLite lui-meme - detecte AUTOMATIQUEMENT toute ecriture, qu'elle
      vienne du formulaire de renommage/suppression de cette page, d'un
      archivage depuis home.py, d'une migration retroactive, ou d'une autre
      session/onglet. C'est la garantie de fraicheur reelle : sans elle, une
      mutation qui ne passe pas par _bump_history_version() (voir plus bas)
      resterait invisible tant que ce compteur ne bouge pas.
    - `version` (compteur session_state, voir _bump_history_version) :
      bumpe explicitement juste apres les mutations faites DEPUIS cette page
      (rename/fusion, suppression, migration) - redondant avec db_stat pour
      ces cas precis (l'ecriture SQL change deja le mtime), mais documente
      intentionnellement chaque point de mutation connu de cette page.
    - `cwd` (os.getcwd()) : qa.history.HISTORY_PATH est un chemin RELATIF
      ("output/history.db"), donc 2 sessions avec un repertoire de travail
      different lisent en realite 2 bases differentes - sans cwd dans la
      cle, l'une pourrait recevoir a tort l'arbre en cache de l'autre. Sans
      consequence en usage reel (un seul repertoire de travail par
      deploiement), mais necessaire a l'isolation des tests (chaque test
      tourne dans son propre tmp_path).
    """
    entries = load_classified_history()
    return entries, build_history_tree(entries=entries)


# Migration unique et idempotente de l'ancien historique JSONL vers SQLite
# (voir qa.history.migrate_legacy_jsonl_history) - AVANT toute lecture de
# l'historique sur cette page (build_history_tree ci-dessous, et
# migrate_legacy_loyalty_sequences qui en appelle deja un), sinon ces
# lectures verraient une base neuve vide plutot que l'historique existant.
if migrate_legacy_jsonl_history():
    st.info(
        "🗄️ L'historique a été migré de l'ancien format (fichier JSONL) vers une base SQLite "
        "plus robuste. Aucune donnée perdue : l'ancien fichier est conservé sur disque "
        "(suffixe `.migrated`)."
    )
    # Ce qu'un futur appel a _load_history_overview retournerait vient de
    # changer (toutes les entrees legacy viennent d'etre importees) - sans
    # ce bump, un cache deja chauffe avant la migration (improbable au tout
    # premier rerun, mais pas impossible sur une session qui a deja tourne)
    # continuerait de servir l'arbre d'avant migration.
    _bump_history_version()

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
    # Idem : migrate_legacy_loyalty_sequences vient de reecrire des lignes
    # de classification (rewrite_classification), ce que _load_history_overview
    # doit refleter au prochain appel.
    _bump_history_version()

# Une seule lecture de la base pour les deux usages ci-dessous (l'arbre de
# navigation ET la liste a plat pour les statistiques d'erreurs), mise en
# cache par _load_history_overview (voir sa docstring) : sans `entries=`,
# build_history_tree relirait une deuxieme fois toute la table en interne via
# load_classified_history, et sans le cache, cette lecture+construction
# tournerait a chaque interaction avec un widget de la page (Streamlit
# reexecute tout le script a chaque rerun).
all_classified_entries, tree = _load_history_overview(
    st.session_state["history_version"], os.getcwd(), _history_db_stat()
)

if not tree:
    st.info(
        "Aucun rapport archivé pour l'instant. Depuis la page principale, lancez une comparaison "
        "et elle sera archivée automatiquement (une pop-up peut apparaître si le pays/trigger "
        "n'a pas pu être détecté)."
    )
    st.stop()

# Le pays/trigger/sequence memorise dans nav (session_state) peut ne plus
# exister dans l'arbre qu'on vient de reconstruire (ex: sequence renommee/
# fusionnee ou supprimee via le panneau "Gerer cette sequence" plus bas, ou
# via un autre onglet) - sans ce garde-fou, tree[nav["country"]] etc. plus
# bas leverait un KeyError et planterait toute la page. On retombe alors
# silencieusement au niveau superieur, comme si rien n'etait selectionne a
# ce niveau (et en-dessous).
if nav["country"] not in tree:
    nav["country"], nav["trigger"], nav["sequence"] = None, None, None
elif nav["trigger"] not in tree[nav["country"]]:
    nav["trigger"], nav["sequence"] = None, None
elif nav["sequence"] not in tree[nav["country"]][nav["trigger"]]:
    nav["sequence"] = None


# =========================
# DEFINITIONS (regroupees avant tout rendu, car appelees dans plusieurs
# sections de la page qui s'executent de haut en bas comme un script)
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
                    "Testé par": latest.get("tested_by") or "—",
                })
    return rows


def folder_grid(items, key_prefix):
    """Affiche une grille de boutons "dossier", retourne l'item choisi (ou None)."""
    chosen = None
    cols = st.columns(4)
    for i, item in enumerate(sorted(items)):
        with cols[i % 4]:
            if st.button(f"📁 {item}", key=f"{key_prefix}_{item}", use_container_width=True):
                chosen = item
    return chosen


def _diff_entries_to_df(entries, mode):
    """Convertit une liste d'entrees {"previous", "current"} (voir
    qa.report_diff.diff_reports) en DataFrame affichable. label_col/sub_col
    identifient la ligne (Component/Element en wording, classe attendue/
    section source en url) ; previous ou current peut etre None (ajout/
    suppression), jamais les deux."""
    label_col, sub_col = ("Component Name", "Element") if mode == "wording" else ("Expected Class", "Source Section")
    rows = []
    for entry in entries:
        previous, current = entry["previous"], entry["current"]
        base = current or previous
        rows.append({
            label_col: base.get(label_col, ""),
            sub_col: base.get(sub_col, ""),
            "Statut avant": previous.get("Status", "") if previous else "—",
            "Statut après": current.get("Status", "") if current else "—",
            "Erreur avant": (previous.get("Error Type") or "") if previous else "",
            "Erreur après": (current.get("Error Type") or "") if current else "",
            "Texte avant": (previous.get("HTML Wording") or "") if previous else "",
            "Texte après": (current.get("HTML Wording") or "") if current else "",
        })
    return pd.DataFrame(rows)


def _render_diff_result(mode, diff):
    """
    Affiche un diff deja calcule (voir qa.report_diff.diff_reports) :
    resume chiffre + un tableau par categorie non vide. Partagee entre le
    panneau "2 derniers rapports archives" (render_report_diff) et le
    panneau d'upload manuel (render_manual_report_diff), pour que les deux
    presentent un diff exactement de la meme facon.
    """
    total_changes = sum(len(diff[k]) for k in ("regressed", "fixed", "changed", "added", "removed"))
    unchanged = diff["unchanged_ok"] + diff["unchanged_ko"]
    if not total_changes:
        st.caption(f"Aucun changement entre les deux rapports ({unchanged} ligne(s) identiques).")
        return

    st.caption(
        f"{len(diff['regressed'])} régression(s), {len(diff['fixed'])} correction(s), "
        f"{len(diff['changed'])} changement(s), {len(diff['added'])} ajout(s), "
        f"{len(diff['removed'])} suppression(s) — {unchanged} ligne(s) inchangée(s)."
    )

    sections = [
        ("⚠️ Régressions (était OK, maintenant KO)", diff["regressed"]),
        ("✅ Corrections (était KO, maintenant OK)", diff["fixed"]),
        ("🔄 Changements (KO avant et après, mais différemment)", diff["changed"]),
        ("➕ Nouveaux éléments", diff["added"]),
        ("➖ Éléments disparus", diff["removed"]),
    ]
    for title, category_entries in sections:
        if category_entries:
            st.write(title)
            st.dataframe(_diff_entries_to_df(category_entries, mode), use_container_width=True, hide_index=True)


@st.cache_data(show_spinner=False)
def _cached_report_diff(country: str, trigger: str, sequence: str, mode: str, report_stats: tuple) -> dict:
    """
    Cache de load_last_two_reports_diff par (country, trigger, sequence,
    mode, report_stats) - meme principe de cle que qa.excel_loader.
    _cached_excel_file et home._cached_excel_diagnostics : report_stats (voir
    _report_diff_cache_key) encode le chemin + mtime_ns + taille des (au
    plus 2) rapports Excel actuellement les plus recents pour cette
    combinaison, donc toute rotation (nouveau rapport archive, ancien
    supprime) change forcement report_stats et invalide naturellement cette
    entree de cache - sans avoir a detecter ce changement explicitement.

    st.cache_data plutot que functools.lru_cache (contrairement aux deux
    references ci-dessus, qui vivent dans un module important normalement) :
    cette fonction est definie dans le corps d'une page Streamlit, reexecute
    de A a Z a chaque rerun - un functools.lru_cache reattache directement
    ici serait donc lui-meme redefini (nouvelle fonction, nouveau cache vide)
    a chaque rerun et n'offrirait aucune retention reelle d'un rerun a
    l'autre, contrairement a st.cache_data dont le cache vit hors de l'espace
    de noms du script et survit justement a cette reexecution.
    """
    return load_last_two_reports_diff(country, trigger, sequence, mode)


def _report_diff_cache_key(country: str, trigger: str, sequence: str, mode: str) -> tuple:
    """Chemin absolu + mtime_ns + taille des (au plus 2) rapports actuellement
    les plus recents pour (country, trigger, sequence, mode) - voir
    _cached_report_diff. Recalculee a chaque appel (juste des os.stat sur au
    plus 2 fichiers, negligeable) : c'est justement ce qui permet de detecter
    une rotation (nouveau rapport archive, ancien supprime par KEEP_REPORTS)
    sans avoir a la signaler explicitement depuis qa.archive. os.path.abspath
    (qa.archive.list_reports renvoie des chemins RELATIFS au cwd courant,
    voir qa.config.OUTPUT_DIR) plutot que le chemin relatif brut : 2 process/
    sessions avec un repertoire de travail different ne doivent jamais
    partager une entree de cache juste parce que le meme chemin relatif s'y
    resout par coincidence a un fichier different (constant en usage reel,
    mais chaque test tourne dans son propre tmp_path)."""
    reports = list_reports(country, trigger, sequence, mode=mode)[:2]
    return tuple((os.path.abspath(path), os.stat(path).st_mtime_ns, os.stat(path).st_size) for path in reports)


def render_report_diff(country, trigger, sequence):
    """
    Diff entre les 2 derniers rapports archives (wording et/ou url) d'une
    sequence - voir qa.report_diff.load_last_two_reports_diff. Complementaire
    au score global deja suivi ci-dessus : identifie precisement QUELS
    composants/wordings/URLs ont regresse, ete corriges ou change de nature,
    plutot que juste "le score a baisse de X points". Purement local (relit
    des rapports Excel deja generes sur disque, aucun appel reseau).

    load_last_two_reports_diff (via _cached_report_diff) reparse jusqu'a 2
    classeurs Excel par mode (pandas/openpyxl) - couteux a refaire a chaque
    interaction avec N'IMPORTE QUEL widget de la page (Streamlit reexecute
    tout le script a chaque rerun, meme quand cet expander reste replie),
    donc mis en cache par identite de fichier (mtime+taille, voir
    _report_diff_cache_key) plutot que recalcule ici sans condition.
    """
    any_diff = False
    for mode in ("wording", "url"):
        cache_key = _report_diff_cache_key(country, trigger, sequence, mode)
        diff = _cached_report_diff(country, trigger, sequence, mode, cache_key)
        if diff is None:
            continue
        any_diff = True
        st.markdown(f"**{MODE_LABELS[mode]}**")
        _render_diff_result(mode, diff)

    if not any_diff:
        st.caption("Moins de 2 rapports archivés pour cette séquence — rien à comparer encore.")


def render_manual_report_diff():
    """
    Diff entre 2 rapports Excel quelconques uploades par l'utilisateur -
    contrairement a render_report_diff (limite aux 2 derniers rapports
    archives d'une sequence, eux-memes limites par KEEP_REPORTS), permet de
    comparer n'importe quel rapport garde de cote (au-dela de la retention
    automatique) ou recu d'un tiers.
    """
    mode_label = st.radio("Type de rapport", ["Wording", "URL"], horizontal=True, key="manual_diff_mode")
    manual_mode = "wording" if mode_label == "Wording" else "url"

    col_previous, col_current = st.columns(2)
    with col_previous:
        previous_file = st.file_uploader("Rapport précédent (.xlsx)", type=["xlsx"], key="manual_diff_previous")
    with col_current:
        current_file = st.file_uploader("Rapport actuel (.xlsx)", type=["xlsx"], key="manual_diff_current")

    if not (previous_file and current_file):
        st.caption("Uploadez les 2 rapports à comparer pour lancer la comparaison.")
        return

    try:
        diff = diff_uploaded_reports(previous_file, current_file, manual_mode)
    except ValueError as e:
        st.error(f"❌ {e}")
        return

    _render_diff_result(manual_mode, diff)


with st.expander("📊 Vue d'ensemble (toutes les séquences)", expanded=nav["sequence"] is None):
    overview_df = pd.DataFrame(_overview_rows(tree)).sort_values("Dernier test", ascending=False)

    search = st.text_input(
        "🔍 Rechercher une séquence (pays, trigger ou nom de séquence)",
        key="history_search",
        placeholder="Ex : Germany, revamp_inac, SEQ 1..."
    )

    filtered_df = overview_df
    if search:
        mask = (
            overview_df["Pays"].str.contains(search, case=False, na=False, regex=False)
            | overview_df["Trigger"].str.contains(search, case=False, na=False, regex=False)
            | overview_df["Séquence"].str.contains(search, case=False, na=False, regex=False)
        )
        filtered_df = overview_df[mask]

    st.dataframe(filtered_df, use_container_width=True, hide_index=True)

    if search and filtered_df.empty:
        st.caption("Aucune séquence ne correspond à cette recherche.")
    elif search and len(filtered_df) == 1:
        match = filtered_df.iloc[0]
        if st.button(f"➡️ Aller à {match['Pays']} / {match['Trigger']} / {match['Séquence']}"):
            nav["country"], nav["trigger"], nav["sequence"] = match["Pays"], match["Trigger"], match["Séquence"]
            st.rerun()

    st.caption("Exporter tout l'historique classé (toutes séquences, tous les runs) pour analyse externe :")
    export_cols = st.columns(2)
    with export_cols[0]:
        st.download_button(
            "⬇️ Excel (.xlsx)",
            data=export_history_excel(all_classified_entries),
            file_name="qa_history_export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with export_cols[1]:
        st.download_button(
            "⬇️ CSV",
            data=export_history_csv(all_classified_entries),
            file_name="qa_history_export.csv",
            mime="text/csv",
            use_container_width=True,
        )

# =========================
# COMPARAISON MANUELLE DE 2 RAPPORTS UPLOADES
# =========================
with st.expander("📤 Comparer deux rapports Excel (upload manuel)", expanded=False):
    st.caption(
        "Compare 2 rapports QA quelconques, pas seulement les 2 derniers archivés d'une séquence — "
        "utile pour un rapport gardé de côté au-delà de la rétention automatique, ou reçu d'un tiers."
    )
    render_manual_report_diff()

# =========================
# STATISTIQUES D'ERREURS AGREGEES (toutes sequences, toute la periode)
# =========================
with st.expander("📊 Statistiques des erreurs (toutes séquences)", expanded=False):
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
# RECEMMENT CONSULTEES
# =========================
current = (nav["country"], nav["trigger"], nav["sequence"])
recent_sequences = [k for k in st.session_state["recent_sequences"] if k != current]
if recent_sequences:
    st.caption("🕘 Récemment consultées :")
    recent_cols = st.columns(len(recent_sequences))
    for col, (r_country, r_trigger, r_sequence) in zip(recent_cols, recent_sequences):
        with col:
            if st.button(
                f"📄 {r_country} / {r_trigger} / {r_sequence}",
                key=f"recent_{r_country}_{r_trigger}_{r_sequence}",
                use_container_width=True,
            ):
                nav["country"], nav["trigger"], nav["sequence"] = r_country, r_trigger, r_sequence
                st.rerun()

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
    _record_recent_sequence(country, trigger, sequence)
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
        # tested_by n'existe comme colonne que si au moins une entree l'a
        # (voir qa.history.record_run, colonne absente pour les runs
        # archives avant l'ajout de cette fonctionnalite) - la creer sinon,
        # pour que la selection de colonnes ci-dessous ne leve jamais.
        if "tested_by" not in history_df.columns:
            history_df["tested_by"] = None
        history_df["tested_by"] = history_df["tested_by"].fillna("—")

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
            history_df[["timestamp", "Type", "total", "ok", "ko", "score", "tested_by"]]
                .rename(columns={"score": "% OK", "tested_by": "Testé par"})
                .iloc[::-1],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.caption("Aucun historique de score pour cette séquence.")

    # =========================
    # DIFF ENTRE LES 2 DERNIERS RAPPORTS
    # =========================
    st.divider()
    with st.expander("🔍 Comparer les 2 derniers rapports"):
        st.caption(
            "Détail des changements entre le rapport le plus récent et le précédent — "
            "quels composants/wordings/URLs précis ont régressé, été corrigés, ou changé de nature."
        )
        render_report_diff(country, trigger, sequence)

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
                new_country = st.text_input("Pays", value=country, key="rename_country")
            with col2:
                new_trigger = st.text_input("Trigger", value=trigger, key="rename_trigger")
            with col3:
                new_sequence = st.text_input("Séquence", value=sequence, key="rename_sequence")

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
                # Ce que _load_history_overview retournerait vient de changer
                # (rewrite_classification) - le rerun juste en-dessous doit
                # voir la nouvelle identite, pas un arbre mis en cache avant
                # le renommage.
                _bump_history_version()
                nav["country"], nav["trigger"], nav["sequence"] = new_country, new_trigger, new_sequence
                # L'ancienne identite ne doit pas rester comme quicklink mort
                # dans "Recemment consultees" - on la remplace par la nouvelle.
                _forget_recent_sequence(country, trigger, sequence)
                _record_recent_sequence(new_country, new_trigger, new_sequence)
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
            # Ce que _load_history_overview retournerait vient de changer
            # (delete_classification) - meme raison que dans le formulaire de
            # renommage ci-dessus.
            _bump_history_version()
            nav["country"], nav["trigger"], nav["sequence"] = None, None, None
            # La sequence supprimee ne doit pas rester comme quicklink mort
            # dans "Recemment consultees".
            _forget_recent_sequence(country, trigger, sequence)
            st.success("Séquence supprimée.")
            st.rerun()
