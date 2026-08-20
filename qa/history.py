"""
Historique local des scores de QA, pour visualiser l'evolution d'une
sequence/campagne d'un run a l'autre. Stocke en JSON Lines (un run par
ligne) dans output/history.jsonl - non versionne, comme le reste de output/.
"""
import io
import json
import os
import threading
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

from qa.config import HISTORY_RETENTION_DAYS, OUTPUT_DIR, REGRESSION_ALERT_THRESHOLD

HISTORY_PATH = os.path.join(OUTPUT_DIR, "history.jsonl")

# Streamlit sert chaque session utilisateur dans un thread du meme process :
# un RLock (pas un Lock simple - record_run appelle prune_history, qui doit
# pouvoir re-acquerir le verrou depuis le meme thread) protege les
# operations de lecture-modification-ecriture sur HISTORY_PATH contre une
# ecriture concurrente entre deux sessions qui archiveraient au meme moment.
_LOCK = threading.RLock()


def _parse_timestamp(timestamp: str) -> datetime:
    return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")


def record_run(sheet: str, mode: str, total: int, ok: int, ko: int, score: float,
                country: str = None, trigger: str = None, sequence: str = None,
                error_types: dict = None, path: str = HISTORY_PATH) -> None:
    """Ajoute une ligne d'historique pour ce run. N'echoue jamais bruyamment :
    l'historique est une fonctionnalite de confort, pas critique pour la QA.

    country/trigger/sequence ne sont renseignes que lors de l'archivage
    manuel confirme par l'utilisateur (voir qa.archive et
    render_archive_section dans app.py) : ce sont ces entrees-la (et
    seulement celles-ci) qui alimentent la page Historique par pays/trigger/
    sequence. L'appel "brut" fait par qa.report a chaque run (sans ces
    champs) continue d'alimenter le mini-graphique par onglet affiche
    immediatement apres une comparaison.

    error_types (optionnel, {"Error Type": count}) : repartition des KO de ce
    run par type d'erreur, fournie par home._do_archive. Comme country/
    trigger/sequence, stockee seulement si non vide - alimente les stats
    agregees de la page Historique (voir aggregate_error_types/
    error_type_monthly_trend). Les entrees anterieures a cette fonctionnalite
    n'ont pas cette cle."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sheet": sheet,
        "mode": mode,
        "total": total,
        "ok": ok,
        "ko": ko,
        "score": score,
    }
    if country:
        entry["country"] = country
    if trigger:
        entry["trigger"] = trigger
    if sequence:
        entry["sequence"] = sequence
    if error_types:
        entry["error_types"] = dict(error_types)
    with _LOCK:
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            return

        try:
            prune_history(path=path)
        except OSError:
            pass


def load_history(sheet: str = None, path: str = HISTORY_PATH) -> list:
    """Retourne les runs enregistres (tous, ou filtres sur sheet), dans
    l'ordre chronologique. Liste vide si le fichier n'existe pas encore."""
    with _LOCK:
        if not os.path.exists(path):
            return []

        entries = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if sheet is None or entry.get("sheet") == sheet:
                    entries.append(entry)
        return entries


def load_classified_history(path: str = HISTORY_PATH) -> list:
    """Runs archives manuellement (voir record_run) : ceux qui portent un
    pays + trigger + sequence."""
    return [
        e for e in load_history(path=path)
        if e.get("country") and e.get("trigger") and e.get("sequence")
    ]


def build_history_tree(path: str = HISTORY_PATH) -> dict:
    """
    { pays: { trigger: { sequence: [entries, tries par timestamp croissant] } } }
    Utilise par la page Historique pour la navigation par dossiers et pour
    tracer l'evolution du score/pourcentage de bugs d'une sequence.
    """
    tree = {}
    for entry in load_classified_history(path):
        (tree
            .setdefault(entry["country"], {})
            .setdefault(entry["trigger"], {})
            .setdefault(entry["sequence"], [])
            .append(entry))
    return tree


def load_sequence_history(country: str, trigger: str, sequence: str, path: str = HISTORY_PATH) -> list:
    """Historique (tous les runs archives) d'une sequence precise, tries chronologiquement."""
    return [
        e for e in load_classified_history(path)
        if e["country"] == country and e["trigger"] == trigger and e["sequence"] == sequence
    ]


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


_EXPORT_COLUMNS = ["timestamp", "country", "trigger", "sequence", "mode", "total", "ok", "ko", "score", "error_types"]


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


def _rewrite_entries(path: str, transform) -> None:
    """Relit toutes les lignes de path, applique transform(entry) -> entry|None
    a chaque entree deja classee (pays+trigger+sequence), puis reecrit le
    fichier (entree ignoree si transform retourne None). Les lignes non-JSON
    ou non classees sont recopiees telles quelles. No-op si le fichier
    n'existe pas encore."""
    with _LOCK:
        if not os.path.exists(path):
            return

        lines = []
        with open(path, encoding="utf-8") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    lines.append(raw_line)
                    continue

                if entry.get("country") and entry.get("trigger") and entry.get("sequence"):
                    entry = transform(entry)
                    if entry is None:
                        continue

                lines.append(json.dumps(entry, ensure_ascii=False))

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))


def rewrite_classification(old_country: str, old_trigger: str, old_sequence: str,
                            new_country: str, new_trigger: str, new_sequence: str,
                            path: str = HISTORY_PATH) -> None:
    """Renomme (ou fusionne) une sequence classee dans l'historique : toutes
    les entrees portant (old_country, old_trigger, old_sequence) sont
    reecrites vers (new_country, new_trigger, new_sequence). Utilise depuis
    la page Historique pour corriger une mauvaise detection automatique, ou
    fusionner un doublon (ex: une sequence normalisee differemment avant que
    le recoupement ne soit possible, voir qa.classification)."""
    def transform(entry):
        if entry["country"] == old_country and entry["trigger"] == old_trigger and entry["sequence"] == old_sequence:
            entry["country"] = new_country
            entry["trigger"] = new_trigger
            entry["sequence"] = new_sequence
        return entry

    _rewrite_entries(path, transform)


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
    Les entrees non classees et les lignes non-JSON sont toujours
    conservees telles quelles (comme pour rewrite_classification /
    delete_classification). Appelee automatiquement par record_run, mais
    peut aussi etre invoquee directement (ex: nettoyage manuel).
    """
    with _LOCK:
        if not os.path.exists(path):
            return

        now = now or datetime.now()
        cutoff = now - timedelta(days=retention_days)

        with open(path, encoding="utf-8") as f:
            raw_lines = [line.strip() for line in f if line.strip()]

        parsed = []
        for raw_line in raw_lines:
            try:
                parsed.append((raw_line, json.loads(raw_line)))
            except json.JSONDecodeError:
                parsed.append((raw_line, None))

        groups = defaultdict(list)
        for i, (_, entry) in enumerate(parsed):
            if entry and entry.get("country") and entry.get("trigger") and entry.get("sequence"):
                groups[(entry["country"], entry["trigger"], entry["sequence"])].append(i)

        drop = set()
        for indices in groups.values():
            indices.sort(key=lambda i: parsed[i][1]["timestamp"])
            recent = [i for i in indices if _parse_timestamp(parsed[i][1]["timestamp"]) >= cutoff]

            if len(recent) >= 2:
                keep = set(recent)
            elif len(recent) == 1:
                pos = indices.index(recent[0])
                keep = set(indices[max(0, pos - 1):pos + 1])
            else:
                keep = set(indices[-2:])

            drop.update(set(indices) - keep)

        if not drop:
            return

        kept_lines = [raw_line for i, (raw_line, _) in enumerate(parsed) if i not in drop]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(kept_lines) + ("\n" if kept_lines else ""))


def delete_classification(country: str, trigger: str, sequence: str, path: str = HISTORY_PATH) -> None:
    """Supprime de l'historique toutes les entrees classees sous (country,
    trigger, sequence). Ne touche pas aux rapports Excel archives (voir
    qa.archive.delete_sequence_dir) : les deux sont a appeler ensemble pour
    supprimer une sequence entierement depuis la page Historique."""
    def transform(entry):
        if entry["country"] == country and entry["trigger"] == trigger and entry["sequence"] == sequence:
            return None
        return entry

    _rewrite_entries(path, transform)
