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


def record_run(sheet: str, mode: str, total: int, ok: int, ko: int, score: float, path: str = HISTORY_PATH) -> None:
    """Ajoute une ligne d'historique pour ce run. N'echoue jamais bruyamment :
    l'historique est une fonctionnalite de confort, pas critique pour la QA."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sheet": sheet,
        "mode": mode,
        "total": total,
        "ok": ok,
        "ko": ko,
        "score": score,
    }
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
