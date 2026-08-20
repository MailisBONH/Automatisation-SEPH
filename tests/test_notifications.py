"""
Tests de qa.notifications : requests.post est TOUJOURS monkeypatche ici -
aucune requete reseau reelle ne doit avoir lieu dans la suite de tests.
"""
import requests

from qa.notifications import send_teams_notification


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
