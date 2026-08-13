"""
Historique local des scores de QA, pour visualiser l'evolution d'une
sequence/campagne d'un run a l'autre. Stocke en JSON Lines (un run par
ligne) dans output/history.jsonl - non versionne, comme le reste de output/.
"""
import json
import os
from datetime import datetime

from qa.config import OUTPUT_DIR

HISTORY_PATH = os.path.join(OUTPUT_DIR, "history.jsonl")


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
