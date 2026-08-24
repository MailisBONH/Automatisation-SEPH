"""
Tests de qa.notifications : requests.post est TOUJOURS monkeypatche ici -
aucune requete reseau reelle ne doit avoir lieu dans la suite de tests.
"""
import requests

from qa.notifications import escape_teams_markdown, send_teams_notification


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class TestSendTeamsNotification:
    def test_no_webhook_configured_is_a_silent_noop(self, monkeypatch):
        calls = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1))

        result = send_teams_notification("Titre", "Texte", webhook_url="")

        assert result is False
        assert calls == []  # aucune requete tentee

    def test_successful_post_returns_true(self, monkeypatch):
        monkeypatch.setattr(requests, "post", lambda url, json, timeout: _FakeResponse(200))

        result = send_teams_notification("Titre", "Texte", webhook_url="https://outlook.office.com/webhook/fake")

        assert result is True

    def test_non_2xx_response_returns_false(self, monkeypatch):
        monkeypatch.setattr(requests, "post", lambda url, json, timeout: _FakeResponse(400))

        result = send_teams_notification("Titre", "Texte", webhook_url="https://outlook.office.com/webhook/fake")

        assert result is False

    def test_network_exception_returns_false(self, monkeypatch):
        def _raise(*a, **k):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr(requests, "post", _raise)

        result = send_teams_notification("Titre", "Texte", webhook_url="https://outlook.office.com/webhook/fake")

        assert result is False

    def test_payload_carries_title_and_text(self, monkeypatch):
        captured = {}

        def _post(url, json, timeout):
            captured.update(json)
            return _FakeResponse(200)

        monkeypatch.setattr(requests, "post", _post)

        send_teams_notification("Mon titre", "Mon texte", webhook_url="https://outlook.office.com/webhook/fake")

        assert captured["title"] == "Mon titre"
        assert captured["text"] == "Mon texte"


class TestEscapeTeamsMarkdown:
    """escape_teams_markdown neutralise les caracteres markdown avant
    d'inserer une valeur variable (pays/trigger/sequence, issue du contenu
    Excel semi-fiable) dans un message Teams - regression test pour la
    faille d'injection markdown/lien trompeur corrigee dans home._do_archive."""

    def test_escapes_link_syntax(self):
        # Sans echappement, "[Cliquez ici](https://phishing.example)" a la
        # place d'un nom de sequence produirait un vrai lien cliquable dans
        # le message Teams poste sur un canal de confiance.
        escaped = escape_teams_markdown("[Cliquez ici](https://phishing.example)")
        assert "[" not in escaped.replace("\\[", "")
        assert "](" not in escaped.replace("\\]", "").replace("\\(", "")
        assert escaped == r"\[Cliquez ici\]\(https://phishing.example\)"

    def test_escapes_bold_and_other_markdown_chars(self):
        assert escape_teams_markdown("**gras** _italique_ `code` ~barre~") == \
            r"\*\*gras\*\* \_italique\_ \`code\` \~barre\~"

    def test_plain_text_is_left_readable(self):
        assert escape_teams_markdown("SEQ 1 NON LOYAL") == "SEQ 1 NON LOYAL"

    def test_none_and_empty_are_treated_as_empty_string(self):
        assert escape_teams_markdown(None) == ""
        assert escape_teams_markdown("") == ""
