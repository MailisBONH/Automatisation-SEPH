"""
Tests de fumee pour pages/2_Braze_Loader.py, via streamlit.testing.v1.AppTest
(voir tests/test_historique_page.py pour le rationnel general) - jusqu'ici
sans aucune couverture, contrairement a home.py et pages/1_Historique.py qui
en ont chacun un.

st.page_link est stubbe pour la meme raison que dans tests/test_historique_page.py
(limitation d'AppTest sur les apps multipage hors de app.py/st.navigation).
"""
import os

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import qa.braze_loader as braze_loader

PAGE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pages", "2_Braze_Loader.py")


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    """st.cache_data (voir _cached_list_email_templates dans
    pages/2_Braze_Loader.py) est un cache GLOBAL au process Python, pas
    isole par instance d'AppTest ni par test - sans le vider avant chaque
    test, un test qui monkeypatche qa.braze_loader.list_email_templates
    (ex. pour simuler une BrazeAPIError) peut recevoir a tort le resultat
    mis en cache par un test precedent utilisant la meme cle de cache
    (tag/mock_mode/endpoint identiques), execute avant lui dans le meme
    process pytest, et voir son propre monkeypatch jamais invoque."""
    st.cache_data.clear()
    yield


def _run_page():
    st.page_link = lambda *a, **k: None
    at = AppTest.from_file(PAGE_PATH)
    at.run()
    return at


class TestMockModeDefault:
    """Comportement par defaut (BRAZE_MOCK_MODE actif, aucune config reelle
    necessaire) : la page doit se charger et fonctionner de bout en bout
    sans acces reseau."""

    def test_loads_without_exception_and_shows_the_mock_mode_warning(self):
        at = _run_page()

        assert not at.exception
        assert any("Mode" in w.value and "mock" in w.value for w in at.warning)

    def test_lists_the_two_mock_templates(self):
        at = _run_page()

        assert not at.exception
        options = at.selectbox[0].options
        assert len(options) == 2

    def test_clicking_load_and_extract_shows_raw_html_and_extracted_texts(self):
        at = _run_page()
        at.button[0].click().run()

        assert not at.exception
        assert any("Hello, your points are waiting" in c.value for c in at.code)
        assert "Textes extraits : 3" in at.subheader[0].value
        rows = at.dataframe[0].value.to_dict("records")
        assert len(rows) == 3
        assert rows[0]["text"] == "Hello, your points are waiting"
        assert rows[0]["component_class"] == "audit-component-header"

    def test_fetches_the_template_html_exactly_once_per_click(self, monkeypatch):
        # Regression : la page appelait get_email_template_html() une
        # premiere fois pour l'affichage du HTML brut, PUIS une seconde
        # fois en interne via extract_texts_from_template() pour
        # l'extraction - deux appels independants pour un seul clic. En
        # mode API reelle, ca double la latence/les appels reseau ; verifie
        # ici que la page appelle bien la fonction une seule fois.
        calls = []
        original = braze_loader.get_email_template_html

        def _counting(*a, **k):
            calls.append(1)
            return original(*a, **k)

        monkeypatch.setattr(braze_loader, "get_email_template_html", _counting)

        at = _run_page()
        at.button[0].click().run()

        assert not at.exception
        assert len(calls) == 1


class TestListTemplatesError:
    def test_a_braze_api_error_listing_templates_shows_an_error_and_stops(self, monkeypatch):
        monkeypatch.setattr(
            braze_loader, "list_email_templates",
            lambda tag=None: (_ for _ in ()).throw(braze_loader.BrazeAPIError("boom")),
        )

        at = _run_page()

        assert not at.exception
        assert any("Impossible de lister les templates" in e.value for e in at.error)
        assert any("Aucun template disponible" in i.value for i in at.info)


class TestLoadTemplateError:
    def test_a_braze_api_error_fetching_html_shows_an_error_and_stops(self, monkeypatch):
        monkeypatch.setattr(
            braze_loader, "get_email_template_html",
            lambda template_id: (_ for _ in ()).throw(braze_loader.BrazeAPIError("boom")),
        )

        at = _run_page()
        at.button[0].click().run()

        assert not at.exception
        assert any("Impossible de recuperer le template" in e.value for e in at.error)
        # L'etape 3 (extraction) ne doit jamais s'afficher : st.stop() a
        # coupe le script juste apres l'erreur de l'etape 2.
        assert not any("Textes extraits" in s.value for s in at.subheader)


class TestRealApiModeIndicator:
    def test_shows_the_real_api_banner_instead_of_the_mock_warning(self, monkeypatch):
        monkeypatch.setattr(braze_loader, "BRAZE_MOCK_MODE", False)
        monkeypatch.setattr(braze_loader, "BRAZE_REST_ENDPOINT", "https://rest.fake-01.braze.com")
        monkeypatch.setattr(braze_loader, "list_email_templates", lambda tag=None: [])

        at = _run_page()

        assert not at.exception
        assert not any("mock" in w.value for w in at.warning)
        assert any("Mode API reelle" in s.value for s in at.success)


class TestNoExtractedText:
    def test_html_without_aem_markers_shows_the_explanatory_warning(self, monkeypatch):
        html_without_markers = "<html><body><h1>Hello</h1></body></html>"
        monkeypatch.setattr(braze_loader, "get_email_template_html", lambda template_id: html_without_markers)

        at = _run_page()
        at.button[0].click().run()

        assert not at.exception
        assert "Textes extraits : 0" in at.subheader[0].value
        assert any("Aucun texte extrait" in w.value for w in at.warning)
