"""
Tests de qa.braze_loader : requests.get est TOUJOURS monkeypatche ici -
aucune requete reseau reelle ne doit avoir lieu dans la suite de tests,
meme quand BRAZE_MOCK_MODE est force a False pour tester le chemin "vraie
API" (voir tests/test_notifications.py pour le meme principe cote Teams).
"""
import requests

import qa.braze_loader as braze_loader
from qa.braze_loader import (
    BrazeAPIError,
    extract_texts_from_template,
    get_email_template_html,
    list_email_templates,
)


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload)

    def json(self):
        return self._payload


class TestMockMode:
    """Comportement par defaut (BRAZE_MOCK_MODE actif) : aucun appel reseau,
    donnees d'exemple statiques - permet de tester le reste du pipeline sans
    acces Braze reel (voir docstring du module)."""

    def test_list_email_templates_returns_static_mock_data(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)
        calls = []
        monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1))

        templates = list_email_templates()

        assert calls == []  # aucune requete tentee
        assert len(templates) == 2
        assert {t["email_template_id"] for t in templates} == {
            "mock-template-seq1-non-loyal",
            "mock-template-seq1-loyal",
        }

    def test_list_email_templates_filters_by_tag(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)

        assert list_email_templates(tag="does-not-exist") == []
        assert len(list_email_templates(tag="mock")) == 2

    def test_get_email_template_html_returns_static_mock_html(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)
        calls = []
        monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1))

        html = get_email_template_html("mock-template-seq1-non-loyal")

        assert calls == []
        assert "text-contribution" in html
        assert "audit-component-header" in html


class TestConfigMissingOutsideMockMode:
    """BRAZE_MOCK_MODE desactive sans BRAZE_API_KEY/BRAZE_REST_ENDPOINT
    configurees : erreur explicite plutot qu'un appel reseau voue a
    echouer silencieusement (URL vide, auth manquante)."""

    def test_list_email_templates_raises(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", False)
        monkeypatch.setattr(braze_loader, "BRAZE_API_KEY", "")
        monkeypatch.setattr(braze_loader, "BRAZE_REST_ENDPOINT", "")

        try:
            list_email_templates()
            assert False, "BrazeAPIError attendue"
        except BrazeAPIError:
            pass

    def test_get_email_template_html_raises(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", False)
        monkeypatch.setattr(braze_loader, "BRAZE_API_KEY", "")
        monkeypatch.setattr(braze_loader, "BRAZE_REST_ENDPOINT", "")

        try:
            get_email_template_html("some-id")
            assert False, "BrazeAPIError attendue"
        except BrazeAPIError:
            pass


class TestRealApiCallsMocked:
    """BRAZE_MOCK_MODE desactive, config presente : requests.get est
    monkeypatche pour simuler l'API Braze sans reseau reel."""

    def _configure(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", False)
        monkeypatch.setattr(braze_loader, "BRAZE_API_KEY", "fake-api-key")
        monkeypatch.setattr(braze_loader, "BRAZE_REST_ENDPOINT", "https://rest.fake-01.braze.com")

    def test_list_email_templates_success(self, monkeypatch):
        self._configure(monkeypatch)
        captured = {}

        def _get(url, headers, params, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            return _FakeResponse(200, {"templates": [{"email_template_id": "real-1"}]})

        monkeypatch.setattr(requests, "get", _get)

        templates = list_email_templates()

        assert templates == [{"email_template_id": "real-1"}]
        assert captured["url"] == "https://rest.fake-01.braze.com/templates/email/list"
        assert captured["headers"]["Authorization"] == "Bearer fake-api-key"
        # Aucun filtre par tag envoye cote serveur (voir docstring de
        # list_email_templates - l'API Braze ne documente pas ce parametre
        # pour cet endpoint) : seuls limit/offset/modified_after/
        # modified_before le sont, donc aucun n'est envoye ici sans etre
        # demande explicitement.
        assert captured["params"] == {}

    def test_list_email_templates_filters_the_real_response_client_side(self, monkeypatch):
        # Regression : un premier essai envoyait {"tags": tag} au serveur et
        # faisait confiance a un filtrage cote Braze non documente/non
        # confirme - la reponse etait alors utilisee telle quelle, sans
        # jamais verifier qu'elle etait effectivement filtree. Le filtrage
        # doit avoir lieu cote client, comme deja fait en mode mock.
        self._configure(monkeypatch)
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers, params, timeout: _FakeResponse(200, {"templates": [
                {"email_template_id": "match", "tags": ["hugplan"]},
                {"email_template_id": "no-match", "tags": ["other"]},
                {"email_template_id": "no-tags-at-all"},
            ]}),
        )

        templates = list_email_templates(tag="hugplan")

        assert [t["email_template_id"] for t in templates] == ["match"]

    def test_list_email_templates_http_error_raises(self, monkeypatch):
        self._configure(monkeypatch)
        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(401, text="unauthorized"))

        try:
            list_email_templates()
            assert False, "BrazeAPIError attendue"
        except BrazeAPIError as exc:
            assert "401" in str(exc)

    def test_get_email_template_html_success(self, monkeypatch):
        self._configure(monkeypatch)
        captured = {}

        def _get(url, headers, params, timeout):
            captured["url"] = url
            captured["params"] = params
            return _FakeResponse(200, {"body": "<html>real</html>"})

        monkeypatch.setattr(requests, "get", _get)

        html = get_email_template_html("real-template-id")

        assert html == "<html>real</html>"
        assert captured["url"] == "https://rest.fake-01.braze.com/templates/email/info"
        assert captured["params"] == {"email_template_id": "real-template-id"}

    def test_get_email_template_html_http_error_raises(self, monkeypatch):
        self._configure(monkeypatch)
        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(500, text="server error"))

        try:
            get_email_template_html("real-template-id")
            assert False, "BrazeAPIError attendue"
        except BrazeAPIError as exc:
            assert "500" in str(exc)


class TestNetworkAndParsingErrorsAreWrappedInBrazeAPIError:
    """Regression : requests.get() n'etait auparavant protege que sur le
    statut HTTP (200 <= code < 300) - une erreur reseau (timeout, connexion
    refusee...) ou une reponse 2xx au corps non-JSON levait une exception
    requests/json brute, jamais interceptee par les `except BrazeAPIError`
    des appelants (voir pages/2_Braze_Loader.py), qui plantait alors toute
    la page au lieu d'afficher une erreur propre."""

    def _configure(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", False)
        monkeypatch.setattr(braze_loader, "BRAZE_API_KEY", "fake-api-key")
        monkeypatch.setattr(braze_loader, "BRAZE_REST_ENDPOINT", "https://rest.fake-01.braze.com")

    def test_connection_error_is_wrapped_for_list_email_templates(self, monkeypatch):
        self._configure(monkeypatch)

        def _raise(*a, **k):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr(requests, "get", _raise)

        try:
            list_email_templates()
            assert False, "BrazeAPIError attendue"
        except BrazeAPIError:
            pass

    def test_timeout_is_wrapped_for_get_email_template_html(self, monkeypatch):
        self._configure(monkeypatch)

        def _raise(*a, **k):
            raise requests.Timeout("boom")

        monkeypatch.setattr(requests, "get", _raise)

        try:
            get_email_template_html("real-template-id")
            assert False, "BrazeAPIError attendue"
        except BrazeAPIError:
            pass

    def test_non_json_2xx_response_is_wrapped(self, monkeypatch):
        self._configure(monkeypatch)

        class _BadJsonResponse:
            status_code = 200
            text = "<html>not json, some proxy error page</html>"

            def json(self):
                raise ValueError("Expecting value: line 1 column 1 (char 0)")

        monkeypatch.setattr(requests, "get", lambda *a, **k: _BadJsonResponse())

        try:
            list_email_templates()
            assert False, "BrazeAPIError attendue"
        except BrazeAPIError as exc:
            assert "JSON" in str(exc) or "json" in str(exc).lower() or "illisible" in str(exc)


class TestExtractTextsFromTemplate:
    """Verifie le branchement loader -> qa.html_extraction.extract_texts_from_html
    de bout en bout (voir extract_texts_from_template)."""

    def test_default_mock_html_is_extracted_successfully(self, monkeypatch):
        # _MOCK_HTML porte volontairement les marqueurs AEM
        # (class="text-contribution"/"audit-component-*"), donc le moteur
        # d'extraction actuel y trouve des textes sans aucune modification -
        # preuve concrete du branchement loader -> qa.html_extraction.
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)

        results = extract_texts_from_template("mock-template-seq1-non-loyal")

        assert results == [
            {
                "text": "Hello, your points are waiting",
                "tag": "h1",
                "type": "H1",
                "component_class": "audit-component-header",
            },
            {
                "text": "You have 25 points available.",
                "tag": "p",
                "type": "Paragraph",
                "component_class": "audit-component-body",
            },
            {
                "text": "Shop now",
                "tag": "a",
                "type": "CTA",
                "component_class": "audit-component-body",
            },
        ]

    def test_html_without_aem_markers_yields_no_extracted_text(self, monkeypatch):
        # Un template Braze REEL n'a tres probablement aucun des marqueurs
        # AEM que le moteur d'extraction recherche (contrairement a
        # _MOCK_HTML, qui les porte exprès pour le test ci-dessus) : ce cas
        # doit rester couvert separement - pas une erreur de branchement,
        # voir la docstring de extract_texts_from_template.
        html_without_markers = """
        <html><body>
        <h1>Hello, your points are waiting</h1>
        <p>You have 25 points available.</p>
        </body></html>
        """
        monkeypatch.setattr(
            braze_loader, "get_email_template_html", lambda template_id: html_without_markers
        )

        results = extract_texts_from_template("whatever-id")

        assert results == []
