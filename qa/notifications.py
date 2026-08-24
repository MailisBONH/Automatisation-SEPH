"""
Notification Teams (webhook entrant) a chaque run archive dans l'historique
(voir home._do_archive) : fonctionnalite de confort, jamais critique pour la
QA elle-meme - une notification manquee (webhook mal configure, Teams
indisponible, reseau coupe...) ne doit jamais faire echouer ou ralentir
visiblement un run, meme philosophie que qa.history.record_run.
"""
import re

import requests

from qa.config import TEAMS_WEBHOOK_URL

_MARKDOWN_SPECIAL_CHARS = re.compile(r"([\\`*_{}\[\]()#+\-!|>~])")


def escape_teams_markdown(text) -> str:
    """Echappe les caracteres markdown significatifs pour le champ `text`
    d'un MessageCard Teams (qui rend un sous-ensemble de markdown, y compris
    la syntaxe de lien `[libelle](url)`) : sans ca, un pays/trigger/sequence
    issu du contenu du classeur Excel (semi-fiable, voir qa.classification -
    un onglet peut porter n'importe quel nom) pourrait injecter du
    formatage ou un lien cliquable trompeur dans le message poste sur le
    canal Teams configure. A appeler sur chaque valeur variable AVANT de
    l'inserer dans une ligne de notification (voir home._do_archive) - pas
    sur le message final, qui melange ces valeurs avec du markdown
    volontaire (les `**...**` ajoutes par l'appelant)."""
    return _MARKDOWN_SPECIAL_CHARS.sub(r"\\\1", str(text or ""))


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
