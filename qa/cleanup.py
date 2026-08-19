"""
Nettoyage automatique de temp/ : les fichiers Excel/HTML uploades sont
isoles par session (voir home.py) mais jamais supprimes spontanement,
d'ou une accumulation indefinie d'une session a l'autre. Ce module purge
les fichiers plus vieux qu'un certain age, avec un throttling (fichier
marqueur) pour que l'appel puisse rester inconditionnel dans l'app malgre
le fait que Streamlit reexecute tout le script a chaque interaction.
"""
import glob
import os
import time

from qa.config import TEMP_CLEANUP_THROTTLE_SECONDS, TEMP_DIR, TEMP_MAX_AGE_HOURS

MAX_AGE_HOURS = TEMP_MAX_AGE_HOURS
THROTTLE_SECONDS = TEMP_CLEANUP_THROTTLE_SECONDS  # ne relance le scan reel qu'une fois par heure au plus


def cleanup_old_temp_files(max_age_hours: float = None, temp_dir: str = None) -> int:
    """Supprime les fichiers de temp_dir plus vieux que max_age_hours (les
    fichiers caches, ex: le marqueur de throttling, sont ignores). Retourne
    le nombre de fichiers supprimes. N'echoue jamais bruyamment : un fichier
    verrouille ou deja supprime entre-temps est simplement ignore.

    Defauts resolus a l'appel (pas au chargement du module) : contrairement
    a un defaut de parametre classique, monkeypatcher qa.cleanup.MAX_AGE_HOURS/
    TEMP_DIR affecte alors bien un appel sans argument explicite - meme
    principe que OUTPUT_DIR dans qa.archive/qa.report."""
    max_age_hours = MAX_AGE_HOURS if max_age_hours is None else max_age_hours
    temp_dir = TEMP_DIR if temp_dir is None else temp_dir

    if not os.path.isdir(temp_dir):
        return 0

    cutoff = time.time() - max_age_hours * 3600
    removed = 0

    for path in glob.glob(os.path.join(temp_dir, "*")):
        if os.path.basename(path).startswith("."):
            continue
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass

    return removed


def cleanup_old_temp_files_throttled(max_age_hours: float = None, temp_dir: str = None) -> None:
    """Comme cleanup_old_temp_files, mais ne scanne reellement le dossier
    qu'une fois par heure au plus (via un fichier marqueur) : a appeler sans
    condition au demarrage de l'app, sans se soucier du fait que Streamlit
    reexecute tout le script a chaque clic."""
    max_age_hours = MAX_AGE_HOURS if max_age_hours is None else max_age_hours
    temp_dir = TEMP_DIR if temp_dir is None else temp_dir

    marker = os.path.join(temp_dir, ".last_cleanup")
    try:
        if os.path.exists(marker) and (time.time() - os.path.getmtime(marker)) < THROTTLE_SECONDS:
            return
        os.makedirs(temp_dir, exist_ok=True)
        cleanup_old_temp_files(max_age_hours, temp_dir)
        with open(marker, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass
