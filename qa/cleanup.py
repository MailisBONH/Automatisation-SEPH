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

TEMP_DIR = "temp"
MAX_AGE_HOURS = 24
THROTTLE_MARKER = os.path.join(TEMP_DIR, ".last_cleanup")
THROTTLE_SECONDS = 3600  # ne relance le scan reel qu'une fois par heure au plus


def cleanup_old_temp_files(max_age_hours: float = MAX_AGE_HOURS, temp_dir: str = TEMP_DIR) -> int:
    """Supprime les fichiers de temp_dir plus vieux que max_age_hours (les
    fichiers caches, ex: le marqueur de throttling, sont ignores). Retourne
    le nombre de fichiers supprimes. N'echoue jamais bruyamment : un fichier
    verrouille ou deja supprime entre-temps est simplement ignore."""
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


def cleanup_old_temp_files_throttled(max_age_hours: float = MAX_AGE_HOURS, temp_dir: str = TEMP_DIR) -> None:
    """Comme cleanup_old_temp_files, mais ne scanne reellement le dossier
    qu'une fois par heure au plus (via un fichier marqueur) : a appeler sans
    condition au demarrage de l'app, sans se soucier du fait que Streamlit
    reexecute tout le script a chaque clic."""
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
