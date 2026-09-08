"""
Historique local des scores de QA, pour visualiser l'evolution d'une
sequence/campagne d'un run a l'autre. Stocke dans une base SQLite (stdlib
sqlite3, pas de serveur a gerer) a output/history.db - non versionne, comme
le reste de output/.

Avant cette migration, l'historique etait stocke en JSON Lines
(output/history.jsonl) : chaque renommage/fusion/suppression de sequence
devait alors relire et reecrire le fichier entier (voir git history), ce qui
devient progressivement plus lent a mesure que l'historique grossit sur
plusieurs annees/pays. SQLite regle ca (UPDATE/DELETE cibles) sans rien
ajouter a installer. Voir migrate_legacy_jsonl_history() pour la migration
(unique, automatique, jamais destructive) d'une installation existante.
"""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

from qa.config import HISTORY_RETENTION_DAYS, OUTPUT_DIR, REGRESSION_ALERT_THRESHOLD

HISTORY_PATH = os.path.join(OUTPUT_DIR, "history.db")

# Emplacement de l'ancien historique JSONL (avant la migration vers SQLite,
# voir migrate_legacy_jsonl_history) - jamais ecrit par le code actuel, lu
# uniquement pour la migration unique d'une installation existante.
LEGACY_HISTORY_PATH = os.path.join(OUTPUT_DIR, "history.jsonl")

# Streamlit sert chaque session utilisateur dans un thread du meme process :
# un RLock (pas un Lock simple - record_run appelle prune_history, qui doit
# pouvoir re-acquerir le verrou depuis le meme thread) protege les
# operations d'ecriture contre une ecriture concurrente entre deux sessions
# qui archiveraient au meme moment. SQLite gere deja son propre verrouillage
# au niveau fichier, mais ce verrou applicatif evite les erreurs
# "database is locked" en serialisant les acces depuis ce process.
_LOCK = threading.RLock()

# Sentinelle distincte de tout resultat reel (y compris None) pour que
# record_run puisse detecter un echec d'ecriture via _with_connection sans
# ambiguite (voir plus bas) - _with_connection retourne `default` sur erreur,
# et un simple None serait indistinguable d'un succes qui ne retourne rien.
_FAILED = object()

# "trigger" est un mot reserve SQL (CREATE TRIGGER) : la colonne s'appelle
# trigger_name en base pour eviter d'avoir a le quoter partout, mais la clef
# exposee par l'API Python reste "trigger" (voir _row_to_entry) - purement
# un detail de stockage interne, invisible de tous les appelants existants.
# Index sur (country, trigger_name, sequence) : load_sequence_history/
# find_regression filtrent desormais directement en SQL sur ce triplet
# (voir plus bas) plutot que de charger toute la table puis filtrer en
# Python - indispensable pour que ce filtre reste rapide sur un historique
# de plusieurs annees couvrant de nombreux pays/triggers/sequences (voir
# README). Index sur `sheet` pour la meme raison cote load_history(sheet=...)
# (mini-graphique par onglet affiche apres chaque comparaison, home.py).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    sheet TEXT NOT NULL,
    mode TEXT NOT NULL,
    total INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    ko INTEGER NOT NULL,
    score REAL NOT NULL,
    country TEXT,
    trigger_name TEXT,
    sequence TEXT,
    error_types TEXT,
    tested_by TEXT,
    report_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_sheet ON runs(sheet);
CREATE INDEX IF NOT EXISTS idx_runs_classification ON runs(country, trigger_name, sequence);
"""

# Colonnes/placeholders de l'INSERT dans `runs`, partages entre record_run
# (une ligne, conn.execute) et migrate_legacy_jsonl_history (plusieurs
# lignes, conn.executemany) pour ne pas dupliquer cette liste de 13 colonnes
# a deux endroits - seule la construction du/des tuple(s) de valeurs reste
# propre a chaque appelant (kwargs normalises pour l'un, entry.get(...) pour
# l'autre), execute() et executemany() acceptant tous deux ce meme SQL.
_INSERT_RUN_SQL = (
    "INSERT INTO runs (timestamp, sheet, mode, total, ok, ko, score, "
    "country, trigger_name, sequence, error_types, tested_by, report_path) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _ensure_report_path_column(conn: sqlite3.Connection) -> None:
    """Ajoute la colonne report_path a une base existante creee avant son
    introduction (voir record_run/delete_run) - `CREATE TABLE IF NOT EXISTS`
    dans _SCHEMA ne modifie pas une table qui existe deja. Idempotent (ne
    fait rien si la colonne est deja presente) et bon marche (une seule
    requete PRAGMA en plus, pas de scan de la table), appelable sans
    condition a chaque connexion (voir _with_connection) - meme principe que
    l'ajout, en son temps, des colonnes error_types/tested_by, faite alors
    directement dans _SCHEMA parce que la base ne contenait encore aucune
    donnee reelle a cette epoque du projet."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "report_path" not in columns:
        conn.execute("ALTER TABLE runs ADD COLUMN report_path TEXT")


def _connect(path: str) -> sqlite3.Connection:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn


def _backup_path(path: str) -> str:
    return path + ".bak"


def _refresh_backup(path: str) -> None:
    """Copie `path` vers son fichier `.bak` juste apres une ecriture reussie
    (voir _with_connection) : permet a load_history de retomber sur la
    derniere version connue-saine si le fichier principal devient illisible
    entre deux ecritures (ex. synchronisation OneDrive interrompue en plein
    ecriture, connue pour corrompre des fichiers SQLite ouverts - ce projet
    vit justement dans un dossier synchronise). Best-effort : une erreur ici
    ne doit jamais faire echouer l'ecriture qu'elle suit."""
    try:
        shutil.copy2(path, _backup_path(path))
    except OSError:
        pass


def _restore_from_backup(path: str) -> bool:
    """Ecrase `path` avec le contenu de son `.bak` s'il existe. Utilise par
    _with_connection avant une ecriture sur une base tronquee a 0 octet (voir
    plus bas) : repartir de la derniere version connue-saine avant d'inserer
    evite que la ligne inseree ensuite se retrouve seule dans une base
    "fraiche", que _refresh_backup ecraserait alors par-dessus la sauvegarde
    qui contenait l'historique reel. Retourne True si une sauvegarde
    existait et a ete restauree avec succes."""
    backup = _backup_path(path)
    if not os.path.exists(backup):
        return False
    try:
        shutil.copy2(backup, path)
        return True
    except OSError:
        return False


def _with_connection(path: str, fn, default=None, mutates: bool = False):
    """Ouvre une connexion SQLite sur `path`, execute `fn(conn)` sous
    `_LOCK`, et retourne son resultat - ou `default` si la moindre erreur
    survient (SQLite ou OS : fichier verrouille/corrompu par une
    synchronisation OneDrive, permissions insuffisantes, etc.). Centralise
    la gestion d'erreur de toutes les fonctions d'historique qui touchent la
    base, pour qu'aucune ne rate l'OSError que `_connect` peut lever (voir
    son os.makedirs) comme ne le faisait auparavant que record_run.
    `mutates=True` rafraichit la sauvegarde .bak apres succes (voir
    _refresh_backup) pour les fonctions qui modifient la base plutot que la
    lisent seulement.

    Garde-fou special pour un fichier tronque a 0 octet (ex. synchronisation
    OneDrive interrompue en plein ecriture) : sqlite3.connect() suivi de
    `CREATE TABLE IF NOT EXISTS` reussirait silencieusement sur un fichier
    vide (SQLite y voit une base neuve, pas une erreur), ce qui serait
    indistinguable d'une base valide qui se trouve juste vide. Sans ce
    garde-fou, une lecture y verrait [] au lieu de retomber sur .bak (voir
    _select_entries, qui ne se replie que sur une erreur), et une ecriture
    qui suivrait (mutates=True) inserterait sa ligne dans cette base
    quasi-vide puis rafraichirait .bak avec elle - detruisant de facon
    permanente la derniere sauvegarde connue-saine. On traite donc ce cas
    comme une erreur de lecture (`default`), et pour une ecriture on
    restaure d'abord .bak (s'il existe) avant de continuer, plutot que de
    laisser l'ecriture repartir de zero puis ecraser la sauvegarde."""
    with _LOCK:
        if os.path.exists(path) and os.path.getsize(path) == 0:
            if not mutates:
                return default
            if os.path.exists(_backup_path(path)) and not _restore_from_backup(path):
                # Sauvegarde presente mais restauration impossible (ex.
                # permissions insuffisantes) : ne surtout pas continuer sur
                # cette base vide, ce qui ecraserait ensuite la sauvegarde
                # saine avec un quasi-rien (voir _refresh_backup ci-dessous).
                return default
        try:
            with contextlib.closing(_connect(path)) as conn:
                conn.executescript(_SCHEMA)
                _ensure_report_path_column(conn)
                result = fn(conn)
        except (sqlite3.Error, OSError):
            return default
        if mutates:
            _refresh_backup(path)
        return result


def _parse_timestamp(timestamp: str) -> datetime:
    return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")


def _row_to_entry(row: sqlite3.Row) -> dict:
    """Reconstruit le dict expose par l'API (meme forme qu'avant la
    migration SQLite) a partir d'une ligne de la table `runs` - les cles
    optionnelles (country/trigger/sequence/error_types/tested_by) ne sont
    presentes que si elles ont une valeur, comme le faisait record_run en
    ne les ecrivant dans le JSON qu'a cette condition."""
    entry = {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "sheet": row["sheet"],
        "mode": row["mode"],
        "total": row["total"],
        "ok": row["ok"],
        "ko": row["ko"],
        "score": row["score"],
    }
    if row["country"]:
        entry["country"] = row["country"]
    if row["trigger_name"]:
        entry["trigger"] = row["trigger_name"]
    if row["sequence"]:
        entry["sequence"] = row["sequence"]
    if row["error_types"]:
        entry["error_types"] = json.loads(row["error_types"])
    if row["tested_by"]:
        entry["tested_by"] = row["tested_by"]
    if row["report_path"]:
        entry["report_path"] = row["report_path"]
    return entry


def _load_legacy_jsonl(path: str) -> list:
    """Relit un fichier a l'ancien format JSONL (une entree JSON par ligne),
    en ignorant les lignes corrompues - meme tolerance que l'ancien
    load_history. Utilise uniquement par migrate_legacy_jsonl_history()."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def migrate_legacy_jsonl_history(legacy_path: str = LEGACY_HISTORY_PATH, path: str = HISTORY_PATH) -> bool:
    """
    Migration unique et idempotente de l'ancien historique JSONL vers la
    nouvelle base SQLite : n'agit que si `legacy_path` existe ENCORE et que
    `path` n'existe pas deja (sinon deja migre, ou nouvelle installation
    sans aucun historique) - reimporte alors chaque entree telle quelle
    (tested_by absent sur toutes, cette colonne n'existait pas avant), puis
    renomme l'ancien fichier en ".migrated" (jamais supprime, une purge
    manuelle reste possible mais jamais automatique).

    A appeler au demarrage de chaque page qui touche l'historique (voir
    home.py et pages/1_Historique.py) plutot qu'a l'interieur de chaque
    fonction de ce module : appeler ici, avant tout record_run/lecture,
    garantit que la migration a lieu avant qu'un premier appel ne cree une
    base vide a `path` (ce qui ferait passer ce garde-fou a False et
    abandonnerait silencieusement l'historique existant). Idempotent et bon
    marche (un seul os.path.exists) une fois migre : peut etre appele sans
    condition a chaque rerun, comme migrate_legacy_loyalty_sequences.

    Le garde-fou (`path` pas encore migre) et l'import sont faits sous le
    meme `_LOCK` : Streamlit sert chaque session dans un thread du meme
    process, donc deux sessions qui chargeraient l'app au tout premier
    instant apres une mise a jour (avant que `path` n'existe) pourraient
    sinon passer toutes les deux le garde-fou avant que l'une n'ait cree la
    base, et importer chacune tout l'historique legacy en double (aucune
    contrainte UNIQUE ne s'y opposerait). Verifier ET ecrire sous le meme
    verrou empeche ce scenario.

    Retourne True si une migration a effectivement eu lieu (pour informer
    l'utilisateur, voir pages/1_Historique.py), False sinon.
    """
    with _LOCK:
        if os.path.exists(path) or not os.path.exists(legacy_path):
            return False

        entries = _load_legacy_jsonl(legacy_path)

        with contextlib.closing(_connect(path)) as conn:
            conn.executescript(_SCHEMA)
            conn.executemany(
                _INSERT_RUN_SQL,
                (
                    (
                        entry.get("timestamp"), entry.get("sheet"), entry.get("mode"),
                        entry.get("total"), entry.get("ok"), entry.get("ko"), entry.get("score"),
                        entry.get("country"), entry.get("trigger"), entry.get("sequence"),
                        json.dumps(entry["error_types"], ensure_ascii=False) if entry.get("error_types") else None,
                        entry.get("tested_by"), entry.get("report_path"),
                    )
                    for entry in entries
                ),
            )
            conn.commit()
        _refresh_backup(path)

    try:
        os.rename(legacy_path, legacy_path + ".migrated")
    except OSError:
        pass

    return True


def record_run(sheet: str, mode: str, total: int, ok: int, ko: int, score: float,
                country: str = None, trigger: str = None, sequence: str = None,
                error_types: dict = None, tested_by: str = None, report_path: str = None,
                path: str = HISTORY_PATH) -> None:
    """Ajoute une ligne d'historique pour ce run. N'echoue jamais bruyamment :
    l'historique est une fonctionnalite de confort, pas critique pour la QA.

    country/trigger/sequence ne sont renseignes que lors de l'archivage
    manuel confirme par l'utilisateur (voir qa.archive et home._do_archive) :
    ce sont ces entrees-la (et seulement celles-ci) qui alimentent la page
    Historique par pays/trigger/sequence. L'appel "brut" fait par qa.report
    a chaque run (sans ces champs) continue d'alimenter le mini-graphique
    par onglet affiche immediatement apres une comparaison.

    error_types (optionnel, {"Error Type": count}) : repartition des KO de ce
    run par type d'erreur, fournie par home._do_archive. tested_by
    (optionnel, nom de l'utilisateur qui a lance/archive ce run, voir
    home._do_archive) : comme country/trigger/sequence/error_types, stocke
    seulement si fourni - les entrees anterieures a ces fonctionnalites n'ont
    pas ces colonnes renseignees. report_path (optionnel, chemin du rapport
    Excel archive pour CE run precis, voir qa.archive.archive_report /
    qa.combined_report.build_combined_report) : permet a delete_run() de
    supprimer aussi le fichier correspondant quand un run precis est retire
    de l'historique (voir pages/1_Historique.py) - absent sur les entrees
    anterieures a cette fonctionnalite, qui restent supprimables de
    l'historique mais sans nettoyage de fichier associe."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error_types_json = json.dumps(dict(error_types), ensure_ascii=False) if error_types else None

    def _insert(conn):
        conn.execute(
            _INSERT_RUN_SQL,
            (
                timestamp, sheet, mode, total, ok, ko, score,
                country or None, trigger or None, sequence or None,
                error_types_json, tested_by or None, report_path or None,
            ),
        )
        conn.commit()

    # prune_history ne doit s'executer qu'apres un insert reussi (sinon rien
    # de nouveau a purger) - _FAILED (distinct de tout resultat reel, y
    # compris None) permet de le detecter via la valeur retournee par
    # _with_connection plutot que via une exception (celle-ci est deja
    # avalee par _with_connection lui-meme).
    if _with_connection(path, _insert, default=_FAILED, mutates=True) is not _FAILED:
        prune_history(path=path)


def _select_entries(path: str, where_sql: str, params: tuple) -> list:
    """Execute `SELECT * FROM runs {where_sql} ORDER BY id` et retourne les
    entrees resultantes (voir _row_to_entry), avec repli sur la sauvegarde
    .bak si le fichier principal est illisible/corrompu (ex. synchronisation
    OneDrive interrompue en plein ecriture - ce projet vit dans un dossier
    synchronise) : seule la perte simultanee du fichier principal ET de sa
    sauvegarde retourne une liste vide (best-effort, comme le reste de ce
    module : ne doit jamais faire planter l'app). `where_sql` est toujours
    l'une des quelques clauses litterales ecrites dans ce module (jamais
    construite depuis une entree utilisateur) - seuls les `params` portent
    des valeurs variables, liees via des `?` parametres."""
    if not os.path.exists(path):
        return []

    def _select(conn):
        rows = conn.execute(f"SELECT * FROM runs {where_sql} ORDER BY id", params).fetchall()
        return [_row_to_entry(row) for row in rows]

    entries = _with_connection(path, _select)
    if entries is not None:
        return entries

    backup = _backup_path(path)
    if os.path.exists(backup):
        entries = _with_connection(backup, _select)
        if entries is not None:
            return entries

    return []


def load_history(sheet: str = None, path: str = HISTORY_PATH) -> list:
    """Retourne les runs enregistres (tous, ou filtres sur sheet), dans
    l'ordre chronologique (ordre d'insertion). Liste vide si la base
    n'existe pas encore ou est illisible/corrompue (voir _select_entries)."""
    if sheet is None:
        return _select_entries(path, "", ())
    return _select_entries(path, "WHERE sheet = ?", (sheet,))


def load_classified_history(path: str = HISTORY_PATH) -> list:
    """Runs archives manuellement (voir record_run) : ceux qui portent un
    pays + trigger + sequence. Filtre en SQL (indexe, voir _SCHEMA) plutot
    que de charger toute la table (y compris les runs "bruts" jamais
    affiches sur la page Historique) puis filtrer en Python."""
    return _select_entries(
        path, "WHERE country IS NOT NULL AND trigger_name IS NOT NULL AND sequence IS NOT NULL", ()
    )


def build_history_tree(path: str = HISTORY_PATH, entries: list = None) -> dict:
    """
    { pays: { trigger: { sequence: [entries, tries par timestamp croissant] } } }
    Utilise par la page Historique pour la navigation par dossiers et pour
    tracer l'evolution du score/pourcentage de bugs d'une sequence.

    `entries` (optionnel) : liste deja chargee (voir load_classified_history)
    a regrouper, pour eviter une deuxieme lecture complete de la base quand
    l'appelant a deja les entrees classees sous la main (voir
    pages/1_Historique.py, qui a par ailleurs besoin de la liste a plat pour
    les statistiques d'erreurs). Si omis, chargee ici comme avant."""
    tree = {}
    for entry in entries if entries is not None else load_classified_history(path):
        (tree
            .setdefault(entry["country"], {})
            .setdefault(entry["trigger"], {})
            .setdefault(entry["sequence"], [])
            .append(entry))
    return tree


def load_sequence_history(country: str, trigger: str, sequence: str, path: str = HISTORY_PATH) -> list:
    """Historique (tous les runs archives) d'une sequence precise, tries
    chronologiquement. Filtre directement en SQL (indexe sur
    (country, trigger_name, sequence), voir _SCHEMA) plutot que de charger
    tout l'historique classe puis filtrer en Python - decisif ici puisque
    find_regression appelle cette fonction pour CHAQUE item archive (voir
    home._do_archive), potentiellement plusieurs fois par clic sur
    Comparer (mode combine/batch), sur un historique qui peut couvrir
    plusieurs annees et de nombreux pays/triggers/sequences (voir README)."""
    return _select_entries(
        path, "WHERE country = ? AND trigger_name = ? AND sequence = ?", (country, trigger, sequence)
    )


def aggregate_error_types(entries: list) -> dict:
    """Additionne les compteurs error_types (voir record_run) de plusieurs
    entrees archivees en un seul dict {Error Type: count total} - utilise
    par la page Historique pour la vue d'ensemble des types d'erreur les
    plus frequents. Les entrees sans error_types (runs anterieurs a cette
    fonctionnalite, ou combines sans propre compteur) sont ignorees
    silencieusement plutot que de lever une erreur."""
    totals = defaultdict(int)
    for e in entries:
        for error_type, count in (e.get("error_types") or {}).items():
            totals[error_type] += count
    return dict(totals)


def error_type_monthly_trend(entries: list) -> list:
    """Agrege les error_types de plusieurs entrees par mois calendaire
    (a partir des 7 premiers caracteres du timestamp, "YYYY-MM"), pour
    tracer l'evolution des types d'erreur dans le temps (page Historique).
    Retourne une liste de {period: "YYYY-MM", error_type, count}, triee
    chronologiquement par period."""
    buckets = defaultdict(lambda: defaultdict(int))
    for e in entries:
        error_types = e.get("error_types") or {}
        if not error_types:
            continue
        period = e["timestamp"][:7]
        for error_type, count in error_types.items():
            buckets[period][error_type] += count

    rows = []
    for period in sorted(buckets):
        for error_type, count in buckets[period].items():
            rows.append({"period": period, "error_type": error_type, "count": count})
    return rows


_EXPORT_COLUMNS = [
    "timestamp", "country", "trigger", "sequence", "mode", "total", "ok", "ko", "score",
    "error_types", "tested_by",
]


def _history_export_dataframe(entries: list):
    """
    Prepare une DataFrame exportable (voir export_history_csv/
    export_history_excel) a partir d'entrees d'historique classees (voir
    load_classified_history). error_types (dict, voir record_run) est
    serialise en JSON dans sa propre colonne plutot qu'aplati en colonnes
    dynamiques - le jeu de types d'erreur varie d'une ligne a l'autre, un
    aplatissement produirait une colonne differente par type d'erreur
    rencontre sur l'ensemble de l'historique.
    """
    rows = [
        {
            "timestamp": e.get("timestamp", ""),
            "country": e.get("country", ""),
            "trigger": e.get("trigger", ""),
            "sequence": e.get("sequence", ""),
            "mode": e.get("mode", ""),
            "total": e.get("total", ""),
            "ok": e.get("ok", ""),
            "ko": e.get("ko", ""),
            "score": e.get("score", ""),
            "error_types": json.dumps(e.get("error_types") or {}, ensure_ascii=False),
            "tested_by": e.get("tested_by", ""),
        }
        for e in entries
    ]
    return pd.DataFrame(rows, columns=_EXPORT_COLUMNS)


def export_history_csv(entries: list) -> bytes:
    """
    Exporte une liste d'entrees d'historique (voir load_classified_history)
    en CSV, pour analyse externe (Excel, Power BI...) au-dela des
    graphiques de la page Historique. Encodage utf-8-sig (avec BOM) : sans
    lui, Excel (notamment en configuration française) interprete le
    fichier avec le mauvais jeu de caracteres a l'ouverture directe et
    affiche les accents corrompus (ex. "Ã©" au lieu de "é") - piege courant
    d'un export CSV en français.
    """
    return _history_export_dataframe(entries).to_csv(index=False).encode("utf-8-sig")


def export_history_excel(entries: list) -> bytes:
    """
    Classeur Excel (.xlsx) equivalent a export_history_csv - pas de piege
    d'encodage/delimiteur puisque le format est natif, et coherent avec le
    reste de l'outil qui produit toujours des rapports .xlsx.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        _history_export_dataframe(entries).to_excel(writer, index=False, sheet_name="Historique")
    return buffer.getvalue()


def find_regression(country: str, trigger: str, sequence: str, mode: str, current_score: float,
                     threshold: float = REGRESSION_ALERT_THRESHOLD, path: str = HISTORY_PATH) -> dict:
    """Compare current_score au dernier score archive pour (country, trigger,
    sequence, mode) - a appeler AVANT record_run du run courant, sinon le
    run se comparerait a lui-meme (il serait deja la "derniere" entree).
    Filtre par mode : un score wording et un score url ne sont pas
    comparables entre eux, meme sequence ou pas.

    Retourne {"previous_score", "current_score", "delta"} si le score a
    baisse d'au moins `threshold` points de pourcentage, sinon None (pas
    d'historique prealable pour ce mode, ou pas de regression)."""
    entries = [e for e in load_sequence_history(country, trigger, sequence, path=path) if e.get("mode") == mode]
    if not entries:
        return None

    previous_score = entries[-1]["score"]
    delta = current_score - previous_score
    if delta <= -threshold:
        return {"previous_score": previous_score, "current_score": current_score, "delta": delta}
    return None


def rewrite_classification(old_country: str, old_trigger: str, old_sequence: str,
                            new_country: str, new_trigger: str, new_sequence: str,
                            path: str = HISTORY_PATH) -> None:
    """Renomme (ou fusionne) une sequence classee dans l'historique : toutes
    les entrees portant (old_country, old_trigger, old_sequence) sont
    reecrites vers (new_country, new_trigger, new_sequence). Utilise depuis
    la page Historique pour corriger une mauvaise detection automatique, ou
    fusionner un doublon (ex: une sequence normalisee differemment avant que
    le recoupement ne soit possible, voir qa.classification). No-op si la
    base n'existe pas encore (rien a renommer)."""
    if not os.path.exists(path):
        return

    def _update(conn):
        conn.execute(
            "UPDATE runs SET country = ?, trigger_name = ?, sequence = ? "
            "WHERE country = ? AND trigger_name = ? AND sequence = ?",
            (new_country, new_trigger, new_sequence, old_country, old_trigger, old_sequence),
        )
        conn.commit()

    _with_connection(path, _update, mutates=True)


def prune_history(path: str = HISTORY_PATH, retention_days: int = HISTORY_RETENTION_DAYS,
                   now: datetime = None) -> None:
    """
    Purge l'historique classe (pays+trigger+sequence), sequence par
    sequence independamment, sans jamais perdre le point de comparaison le
    plus utile :
    - >= 2 entrees des `retention_days` derniers jours : on garde toutes
      les entrees recentes, on supprime les plus vieilles.
    - exactement 1 entree recente : on la garde, plus la derniere entree
      juste avant elle (meme si elle a plus de `retention_days` jours),
      pour toujours garder un point de comparaison avant/apres.
    - aucune entree recente (sequence pas testee depuis plus de
      `retention_days` jours) : on garde seulement les 2 dernieres entrees
      au total, peu importe leur date.
    Les entrees non classees sont toujours conservees telles quelles.
    Appelee automatiquement par record_run, mais peut aussi etre invoquee
    directement (ex: nettoyage manuel). No-op si la base n'existe pas
    encore.
    """
    if not os.path.exists(path):
        return

    now = now or datetime.now()
    cutoff = now - timedelta(days=retention_days)

    def _prune(conn):
        rows = conn.execute(
            "SELECT id, timestamp, country, trigger_name, sequence FROM runs "
            "WHERE country IS NOT NULL AND trigger_name IS NOT NULL AND sequence IS NOT NULL"
        ).fetchall()

        groups = defaultdict(list)
        for row in rows:
            groups[(row["country"], row["trigger_name"], row["sequence"])].append(row)

        drop_ids = set()
        for group_rows in groups.values():
            group_rows.sort(key=lambda r: _parse_timestamp(r["timestamp"]))
            recent = [r for r in group_rows if _parse_timestamp(r["timestamp"]) >= cutoff]

            if len(recent) >= 2:
                keep_ids = {r["id"] for r in recent}
            elif len(recent) == 1:
                pos = group_rows.index(recent[0])
                keep_ids = {r["id"] for r in group_rows[max(0, pos - 1):pos + 1]}
            else:
                keep_ids = {r["id"] for r in group_rows[-2:]}

            drop_ids.update({r["id"] for r in group_rows} - keep_ids)

        if drop_ids:
            conn.executemany("DELETE FROM runs WHERE id = ?", [(i,) for i in drop_ids])
            conn.commit()

    _with_connection(path, _prune, mutates=True)


def delete_classification(country: str, trigger: str, sequence: str, path: str = HISTORY_PATH) -> None:
    """Supprime de l'historique toutes les entrees classees sous (country,
    trigger, sequence). Ne touche pas aux rapports Excel archives (voir
    qa.archive.delete_sequence_dir) : les deux sont a appeler ensemble pour
    supprimer une sequence entierement depuis la page Historique. No-op si
    la base n'existe pas encore."""
    if not os.path.exists(path):
        return

    def _delete(conn):
        conn.execute(
            "DELETE FROM runs WHERE country = ? AND trigger_name = ? AND sequence = ?",
            (country, trigger, sequence),
        )
        conn.commit()

    _with_connection(path, _delete, mutates=True)


def delete_run(run_id: int, path: str = HISTORY_PATH) -> str:
    """Supprime UNE SEULE entree d'historique par son id (voir _row_to_entry,
    qui expose desormais `id`) - contrairement a delete_classification qui
    supprime toute une sequence, cible un run precis (ex: une comparaison
    archivee par erreur - composants generiques oublies, mauvais fichier
    upload... - qui fausserait sinon le score "precedent" compare a chaque
    nouvel archivage, les stats d'erreurs agregees et le graphique
    d'evolution de cette sequence) sans toucher au reste de son historique.

    Retourne le `report_path` enregistre pour ce run (voir record_run), ou
    None si l'id est introuvable ou si ce run est anterieur a l'ajout de
    report_path - a l'appelant de supprimer ce fichier du disque si
    non-None (voir qa.archive.delete_report_file) : ce module ne touche
    jamais au systeme de fichiers, seulement a la base (meme separation que
    delete_classification/qa.archive.delete_sequence_dir). No-op (retourne
    None) si la base n'existe pas encore."""
    if not os.path.exists(path):
        return None

    def _delete(conn):
        row = conn.execute("SELECT report_path FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        return row["report_path"]

    return _with_connection(path, _delete, mutates=True)
