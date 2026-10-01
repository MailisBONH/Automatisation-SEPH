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
    assign_translation_ids,
    build_expected_wordings_from_translations,
    extract_texts_from_template,
    extract_translation_tags,
    get_email_template_html,
    get_mock_rendered_html,
    get_translation_map,
    list_email_templates,
    list_template_locales,
    run_translation_based_qa,
)
from qa.text_utils import normalize


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
        assert "{% translation header_title %}" in html
        assert "{% endtranslation %}" in html


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

    def test_default_mock_has_no_aem_markers_so_extraction_is_empty(self, monkeypatch):
        # Le mock par defaut (_MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS) est le
        # template SOURCE avec tags de traduction Liquid - pas de marqueur
        # AEM (class="text-contribution"/"audit-component-*"), comme un vrai
        # template Braze n'en aurait pas. Pas une erreur de branchement :
        # voir run_translation_based_qa() pour la piste de QA retenue.
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)

        results = extract_texts_from_template("mock-template-seq1-non-loyal")

        assert results == []

    def test_extraction_still_works_when_aem_markers_are_present(self, monkeypatch):
        # Si un template (Braze ou autre) portait malgre tout ces marqueurs,
        # le moteur d'extraction les traiterait sans aucune modification -
        # preuve que le branchement loader -> qa.html_extraction reste
        # correct independamment de la question Liquid/traduction.
        html_with_markers = """
        <html><body>
        <div class="text-contribution audit-component-header">
            <h1>Hello, your points are waiting</h1>
        </div>
        </body></html>
        """
        monkeypatch.setattr(
            braze_loader, "get_email_template_html", lambda template_id: html_with_markers
        )

        results = extract_texts_from_template("whatever-id")

        assert results == [{
            "text": "Hello, your points are waiting",
            "tag": "h1",
            "type": "H1",
            "component_class": "audit-component-header",
        }]


class TestExtractTranslationTags:
    """extract_translation_tags : parsing des tags Liquid natifs Braze
    `{% translation id %}...{% endtranslation %}` depuis le HTML SOURCE
    (non rendu) d'un template."""

    def test_extracts_ids_and_source_text_in_order(self):
        raw = (
            "<h1>{% translation header_title %}Hello{% endtranslation %}</h1>"
            "<p>{% translation body_text %}World{% endtranslation %}</p>"
        )
        tags = extract_translation_tags(raw)
        assert tags == [
            {"translation_id": "header_title", "source_text": "Hello"},
            {"translation_id": "body_text", "source_text": "World"},
        ]

    def test_no_tags_yields_empty_list(self):
        assert extract_translation_tags("<h1>Hello</h1>") == []

    def test_empty_input_yields_empty_list(self):
        assert extract_translation_tags("") == []
        assert extract_translation_tags(None) == []

    def test_source_text_whitespace_is_collapsed(self):
        raw = "{% translation id_0 %}  Hello   \n   World  {% endtranslation %}"
        assert extract_translation_tags(raw) == [{"translation_id": "id_0", "source_text": "Hello World"}]

    def test_default_mock_raw_template_has_four_tags_in_order(self):
        tags = extract_translation_tags(braze_loader._MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS)
        assert [t["translation_id"] for t in tags] == [
            "header_title", "intro_text", "offer_title", "cta_label",
        ]


class TestLocalesAndTranslationMapMockMode:
    def test_list_template_locales_returns_static_mock_data(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)
        calls = []
        monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1))

        locales = list_template_locales("mock-template-seq1-non-loyal")

        assert calls == []
        assert {l["uuid"] for l in locales} == {"locale-fr-fr", "locale-de-de"}

    def test_get_translation_map_returns_static_mock_data(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)

        tmap = get_translation_map("mock-template-seq1-non-loyal", "locale-fr-fr")

        assert tmap["header_title"] == "Joyeux anniversaire !"

    def test_get_translation_map_unknown_combination_returns_empty_dict(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)

        assert get_translation_map("unknown-template", "locale-fr-fr") == {}

    def test_get_mock_rendered_html_returns_html_for_known_locale(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)

        html = get_mock_rendered_html("locale-fr-fr")

        assert "Joyeux anniversaire" in html

    def test_get_mock_rendered_html_outside_mock_mode_returns_empty_string(self, monkeypatch):
        # Pas d'equivalent Braze reel a ce helper de demonstration (voir sa
        # docstring) : hors mode mock, rien a simuler.
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", False)

        assert get_mock_rendered_html("locale-fr-fr") == ""


class TestLocalesAndTranslationMapRealApiMocked:
    def _configure(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", False)
        monkeypatch.setattr(braze_loader, "BRAZE_API_KEY", "fake-api-key")
        monkeypatch.setattr(braze_loader, "BRAZE_REST_ENDPOINT", "https://rest.fake-01.braze.com")

    def test_list_template_locales_calls_the_expected_endpoint(self, monkeypatch):
        self._configure(monkeypatch)
        captured = {}

        def _get(url, headers, params, timeout):
            captured["url"] = url
            captured["params"] = params
            return _FakeResponse(200, {"locales": [{"uuid": "real-locale"}]})

        monkeypatch.setattr(requests, "get", _get)

        locales = list_template_locales("real-template-id")

        assert locales == [{"uuid": "real-locale"}]
        assert captured["url"] == "https://rest.fake-01.braze.com/templates/email/translations/"
        assert captured["params"] == {"template_id": "real-template-id"}

    def test_get_translation_map_calls_the_expected_endpoint(self, monkeypatch):
        self._configure(monkeypatch)
        captured = {}

        def _get(url, headers, params, timeout):
            captured["url"] = url
            captured["params"] = params
            return _FakeResponse(200, {"translation_map": {"id_0": "real text"}})

        monkeypatch.setattr(requests, "get", _get)

        tmap = get_translation_map("real-template-id", "real-locale-id")

        assert tmap == {"id_0": "real text"}
        assert captured["url"] == "https://rest.fake-01.braze.com/templates/translations/email"
        assert captured["params"] == {"template_id": "real-template-id", "locale_id": "real-locale-id"}

    def test_get_translation_map_http_error_raises(self, monkeypatch):
        self._configure(monkeypatch)
        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(403, text="forbidden"))

        try:
            get_translation_map("real-template-id", "real-locale-id")
            assert False, "BrazeAPIError attendue"
        except BrazeAPIError as exc:
            assert "403" in str(exc)


class TestBuildExpectedWordingsFromTranslations:
    def test_uses_translation_map_value_when_present(self):
        tags = [{"translation_id": "header_title", "source_text": "Default text"}]
        tmap = {"header_title": "Texte traduit"}

        wordings = build_expected_wordings_from_translations(tags, tmap)

        assert len(wordings) == 1
        w = wordings[0]
        assert w["Wording"] == "Texte traduit"
        assert w["Technical"] == "header_title"
        assert w["Component"] == "header_title"
        assert w["Element"] == "header_title"
        assert w["norm"] == normalize("Texte traduit")
        assert w["Sheet"] == "Braze translations"

    def test_falls_back_to_source_text_when_translation_missing(self):
        tags = [{"translation_id": "header_title", "source_text": "Default text"}]

        wordings = build_expected_wordings_from_translations(tags, {})

        assert wordings[0]["Wording"] == "Default text"

    def test_empty_tags_yields_empty_list(self):
        assert build_expected_wordings_from_translations([], {"a": "b"}) == []


class TestAssignTranslationIds:
    def test_assigns_ids_positionally(self):
        html_texts = [{"text": "A", "component_class": None}, {"text": "B", "component_class": None}]
        tags = [{"translation_id": "id_a"}, {"translation_id": "id_b"}]

        aligned = assign_translation_ids(html_texts, tags)

        assert [i["component_class"] for i in aligned] == ["id_a", "id_b"]

    def test_does_not_mutate_the_input_list(self):
        html_texts = [{"text": "A", "component_class": None}]
        tags = [{"translation_id": "id_a"}]

        assign_translation_ids(html_texts, tags)

        assert html_texts[0]["component_class"] is None

    def test_extra_html_items_beyond_tag_count_keep_component_class_none(self):
        html_texts = [{"text": "A", "component_class": None}, {"text": "Extra", "component_class": None}]
        tags = [{"translation_id": "id_a"}]

        aligned = assign_translation_ids(html_texts, tags)

        assert aligned[1]["component_class"] is None

    def test_fewer_html_items_than_tags_is_safe(self):
        html_texts = [{"text": "A", "component_class": None}]
        tags = [{"translation_id": "id_a"}, {"translation_id": "id_b"}]

        aligned = assign_translation_ids(html_texts, tags)

        assert len(aligned) == 1
        assert aligned[0]["component_class"] == "id_a"


class TestRunTranslationBasedQa:
    """Integration bout-en-bout : template source (tags) + HTML rendu +
    translation_map -> reutilisation de qa.wording_qa.run_wording_qa, memes
    colonnes/criticite que le pipeline AEM (voir qa.matching.make_row)."""

    def test_end_to_end_via_get_email_template_html(self, monkeypatch):
        # Regression : un premier branchement passait
        # _MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS directement en entree, sans
        # jamais verifier que get_email_template_html() (reellement appelee
        # par pages/2_Braze_Loader.py) renvoyait bien CE MEME contenu -
        # get_email_template_html() renvoyait encore l'ancien mock AEM, ce
        # qui donnait 0 tag de traduction trouve et 0/4 OK dans la vraie
        # page, invisible depuis un test qui court-circuitait cet appel.
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)
        template_id = "mock-template-seq1-non-loyal"
        locale_id = "locale-fr-fr"

        raw_template_html = get_email_template_html(template_id)
        tmap = get_translation_map(template_id, locale_id)
        rendered = get_mock_rendered_html(locale_id)

        results = run_translation_based_qa(raw_template_html, rendered, tmap)

        assert len(results) == 4
        assert all(r["Status"] == "OK" for r in results)

    def test_matching_locale_is_all_ok(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)
        tmap = get_translation_map("mock-template-seq1-non-loyal", "locale-fr-fr")
        rendered = get_mock_rendered_html("locale-fr-fr")

        results = run_translation_based_qa(
            braze_loader._MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS, rendered, tmap
        )

        assert len(results) == 4
        assert all(r["Status"] == "OK" for r in results)
        assert {r["Component Name"] for r in results} == {
            "header_title", "intro_text", "offer_title", "cta_label",
        }

    def test_diverging_locale_flags_the_mismatched_field_as_ko(self, monkeypatch):
        # _MOCK_RENDERED_HTML_BY_LOCALE["locale-de-de"] contient une
        # divergence volontaire sur le CTA - preuve que la comparaison
        # detecte un vrai ecart, pas seulement le cas 100% OK.
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)
        tmap = get_translation_map("mock-template-seq1-non-loyal", "locale-de-de")
        rendered = get_mock_rendered_html("locale-de-de")

        results = run_translation_based_qa(
            braze_loader._MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS, rendered, tmap
        )

        by_component = {r["Component Name"]: r for r in results}
        assert by_component["cta_label"]["Status"] == "KO"
        assert by_component["header_title"]["Status"] == "OK"
        assert by_component["intro_text"]["Status"] == "OK"
        assert by_component["offer_title"]["Status"] == "OK"

    def test_empty_translation_map_falls_back_to_source_text_and_stays_ok(self, monkeypatch):
        # Si la locale n'a pas encore de traduction, le rendu "mock" ne
        # correspondrait normalement pas au texte SOURCE (anglais) - ici on
        # verifie plutot le cas degenere ou le rendu EST le texte source
        # (ex. template pas encore traduit, affiche tel quel).
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", True)
        tags = extract_translation_tags(braze_loader._MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS)
        source_rendered_html = "".join(f"<p>{t['source_text']}</p>" for t in tags)

        results = run_translation_based_qa(
            braze_loader._MOCK_RAW_TEMPLATE_WITH_TRANSLATIONS, source_rendered_html, {}
        )

        assert all(r["Status"] == "OK" for r in results)
