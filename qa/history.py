"""
Historique local des scores de QA, pour visualiser l'evolution d'une
sequence/campagne d'un run a l'autre. Stocke en JSON Lines (un run par
ligne) dans output/history.jsonl - non versionne, comme le reste de output/.
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

from qa.config import HISTORY_RETENTION_DAYS, OUTPUT_DIR

HISTORY_PATH = os.path.join(OUTPUT_DIR, "history.jsonl")


def _parse_timestamp(timestamp: str) -> datetime:
    return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")


def record_run(sheet: str, mode: str, total: int, ok: int, ko: int, score: float,
                country: str = None, trigger: str = None, sequence: str = None,
                path: str = HISTORY_PATH) -> None:
    """Ajoute une ligne d'historique pour ce run. N'echoue jamais bruyamment :
    l'historique est une fonctionnalite de confort, pas critique pour la QA.

    country/trigger/sequence ne sont renseignes que lors de l'archivage
    manuel confirme par l'utilisateur (voir qa.archive et
    render_archive_section dans app.py) : ce sont ces entrees-la (et
    seulement celles-ci) qui alimentent la page Historique par pays/trigger/
    sequence. L'appel "brut" fait par qa.report a chaque run (sans ces
    champs) continue d'alimenter le mini-graphique par onglet affiche
    immediatement apres une comparaison."""
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


def _rewrite_entries(path: str, transform) -> None:
    """Relit toutes les lignes de path, applique transform(entry) -> entry|None
    a chaque entree deja classee (pays+trigger+sequence), puis reecrit le
    fichier (entree ignoree si transform retourne None). Les lignes non-JSON
    ou non classees sont recopiees telles quelles. No-op si le fichier
    n'existe pas encore."""
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
