"""
Notification Teams (webhook entrant) a chaque run archive dans l'historique
(voir home._do_archive) : fonctionnalite de confort, jamais critique pour la
QA elle-meme - une notification manquee (webhook mal configure, Teams
indisponible, reseau coupe...) ne doit jamais faire echouer ou ralentir
visiblement un run, meme philosophie que qa.history.record_run.
"""
import requests

from qa.config import TEAMS_WEBHOOK_URL


def send_teams_notification(title: str, text: str, webhook_url: str = TEAMS_WEBHOOK_URL) -> bool:
    """
    Poste une carte simple (format MessageCard, connecteur Office 365) sur
    le webhook entrant Teams configure. Ne fait aucune requete et retourne
    False si aucun webhook n'est configure (TEAMS_WEBHOOK_URL vide, voir
    qa.config - variable d'environnement QA_TEAMS_WEBHOOK_URL) : la
    notification est alors silencieusement desactivee, pas une erreur.
    """
    if not webhook_url:
        return False

    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": title,
        "themeColor": "0076D7",
        "title": title,
        "text": text,
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        return 200 <= resp.status_code < 300
    except requests.RequestException:
        return False
