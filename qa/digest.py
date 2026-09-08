"""
Digest periodique de l'etat de l'historique QA, envoye sur le webhook Teams
configure (voir qa.notifications) - contrairement a la notification envoyee
a CHAQUE archivage (voir home._do_archive, un message par clic sur
"Comparer"), ce digest resume l'ensemble de l'historique classe d'un coup
(les plus grosses regressions entre les 2 derniers runs de chaque sequence/
mode, toutes sequences confondues - voir qa.history.latest_scores_with_delta)
pour reperer une tendance sans avoir a ouvrir l'app.

Pense pour etre invoque par un planificateur externe (ex. le Planificateur de
taches Windows, une tache cron) via `python -m qa.digest`, cette application
Streamlit n'ayant pas de processus serveur permanent qui pourrait declencher
lui-meme un envoi periodique - voir le README pour la configuration. Un
bouton "Envoyer maintenant" dans la page Historique (qui appelle send_digest
directement) permet aussi un envoi ponctuel sans attendre la prochaine
execution planifiee.
"""
import sys

from qa.config import REGRESSION_ALERT_THRESHOLD
from qa.history import HISTORY_PATH, latest_scores_with_delta, load_classified_history
from qa.notifications import escape_teams_markdown, send_teams_notification

MODE_LABELS = {"wording": "Wording", "url": "URL", "combined": "Combiné"}

# Nombre max de regressions detaillees dans le message (au-dela, un seul
# "... et N de plus.") - un digest illisible (des dizaines de lignes) sur un
# historique qui couvre beaucoup de pays/triggers serait contre-productif,
# voir le meme raisonnement pour MAX_RECENT_SEQUENCES/top_types ailleurs
# dans ce projet.
MAX_REGRESSIONS_LISTED = 10


def build_digest_text(entries: list, threshold: float = REGRESSION_ALERT_THRESHOLD) -> str:
    """
    Construit le corps du message Teams a partir de l'historique classe
    (voir qa.history.load_classified_history) : un compte total de
    sequences/modes suivis, puis la liste des MAX_REGRESSIONS_LISTED plus
    grosses regressions (delta <= -threshold entre les 2 derniers runs de
    chaque (pays, trigger, sequence, mode), voir
    qa.history.latest_scores_with_delta - deja triee du pire au moins pire)
    ou une confirmation qu'aucune n'a ete detectee.

    Retourne une chaine vide si `entries` est vide (aucune sequence classee
    du tout) : send_digest() n'envoie alors rien, plutot que de poster un
    message Teams vide de sens sur un historique encore inexistant.
    """
    rows = latest_scores_with_delta(entries)
    if not rows:
        return ""

    regressions = [r for r in rows if r["delta"] is not None and r["delta"] <= -threshold]

    lines = [f"**{len(rows)}** séquence(s)/mode(s) suivi(s)."]
    lines.append("")
    if regressions:
        lines.append(f"⚠️ **{len(regressions)} régression(s) détectée(s)** (delta ≤ -{threshold} pts) :")
        for r in regressions[:MAX_REGRESSIONS_LISTED]:
            mode_label = MODE_LABELS.get(r["mode"], r["mode"])
            lines.append(
                f"- {escape_teams_markdown(r['country'])} / {escape_teams_markdown(r['trigger'])} / "
                f"{escape_teams_markdown(r['sequence'])} ({mode_label}) : "
                f"{r['previous_score']}% → {r['latest_score']}% ({r['delta']:+.1f} pts)"
            )
        if len(regressions) > MAX_REGRESSIONS_LISTED:
            lines.append(f"- … et {len(regressions) - MAX_REGRESSIONS_LISTED} de plus.")
    else:
        lines.append("✅ Aucune régression détectée depuis le dernier run de chaque séquence.")

    return "\n".join(lines)


def send_digest(path: str = HISTORY_PATH) -> bool:
    """Construit et envoie le digest sur le webhook Teams configure (voir
    qa.notifications.send_teams_notification - no-op silencieux si
    QA_TEAMS_WEBHOOK_URL n'est pas definie, comme la notification par run).
    Retourne True si effectivement envoye (webhook configure ET requete
    reussie), False sinon (aucune sequence classee, webhook non configure,
    ou echec reseau - jamais une exception, meme philosophie que le reste
    de qa.history/qa.notifications : le digest est une fonctionnalite de
    confort, jamais critique pour la QA elle-meme)."""
    text = build_digest_text(load_classified_history(path))
    if not text:
        return False
    return send_teams_notification("📊 Digest QA — état de l'historique", text)


def main() -> int:
    """Point d'entree CLI (`python -m qa.digest`), pense pour un
    planificateur externe (voir le README pour la configuration). Affiche un
    message explicite dans les deux cas (envoye, ou non-envoye et pourquoi)
    plutot que de rester silencieux, utile pour verifier qu'une tache
    planifiee fonctionne bien depuis ses propres logs. Passe HISTORY_PATH
    explicitement (au lieu de compter sur le defaut de send_digest, fige a
    la definition de la fonction) pour rester testable via un monkeypatch
    du nom de module."""
    if send_digest(path=HISTORY_PATH):
        print("Digest envoyé sur Teams.")
        return 0
    print("Digest non envoyé (aucune séquence classée, ou QA_TEAMS_WEBHOOK_URL non configurée).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
