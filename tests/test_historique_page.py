"""
Tests de fumee pour pages/1_Historique.py, via streamlit.testing.v1.AppTest
(voir tests/test_home.py pour le rationnel general) : jusqu'ici sans aucune
couverture.

st.page_link est stubbe : teste seule (hors de app.py/st.navigation, comme
c'est le cas ici), la page ne peut pas resoudre "home.py" comme une page
valide (limitation du harnais de test AppTest sur les apps multipage, pas
un bug de l'app - en conditions reelles, app.py declare les deux pages via
st.navigation et la resolution fonctionne).
"""
import os

import streamlit as st
from streamlit.testing.v1 import AppTest

from qa.history import load_sequence_history, record_run

PAGE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pages", "1_Historique.py")


def _run_page():
    st.page_link = lambda *a, **k: None
    at = AppTest.from_file(PAGE_PATH)
    at.run()
    return at


class TestEmptyHistory:
    def test_shows_empty_state_message_and_no_exception(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        at = _run_page()
        assert not at.exception
        assert any("Aucun rapport archivé" in info.value for info in at.info)


class TestPopulatedHistory:
    def test_overview_table_lists_the_recorded_sequence(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()

        assert not at.exception
        rows = at.dataframe[0].value.to_dict("records")
        assert len(rows) == 1
        assert rows[0]["Pays"] == "France"
        assert rows[0]["Trigger"] == "revamp_inac"
        assert rows[0]["Séquence"] == "SEQ 1"
        assert rows[0]["% OK"] == 80.0

    def test_navigating_country_trigger_sequence_reaches_the_detail_view(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        assert not at.exception
        assert at.subheader[0].value == "France — choisissez un trigger"

        at.button(key="nav_trigger_revamp_inac").click().run()
        assert not at.exception
        assert at.subheader[0].value == "France / revamp_inac — choisissez une séquence"

        at.button(key="nav_sequence_SEQ 1").click().run()
        assert not at.exception
        assert at.subheader[0].value == "France / revamp_inac / SEQ 1"
        assert any("Aucun rapport disponible" in c.value for c in at.caption)

    def test_breadcrumb_home_button_resets_navigation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()

        at.button[0].click().run()  # "🏠 Pays" (premier bouton du fil d'Ariane)
        assert not at.exception
        assert at.subheader[0].value == "Choisissez un pays"

    def test_renaming_a_sequence_moves_it_in_history(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()

        # Formulaire "Renommer / fusionner" : 3 champs texte (Pays, Trigger,
        # Séquence, dans cet ordre d'apparition) pré-remplis avec les valeurs
        # actuelles - on ne change que le pays.
        at.text_input[0].set_value("Germany")
        at.text_input[1].set_value("revamp_inac")
        at.text_input[2].set_value("SEQ 1")
        # Le bouton "Supprimer" (index suivant) est desactive tant que la
        # case de confirmation n'est pas cochee, mais AppTest ne fait pas
        # respecter cet etat cote client - on cible explicitement le bouton
        # de soumission du formulaire par sa cle plutot que par position.
        submit_button = next(b for b in at.button if b.key and b.key.startswith("FormSubmitter"))
        submit_button.click().run()

        # st.rerun() est appele juste apres st.success() : AppTest suit ce
        # rerun jusqu'a l'etat stable, donc le message de succes (affiche
        # une seule fois, avant le rerun) n'est plus dans l'arbre final - on
        # verifie plutot le resultat concret du renommage, cote navigation
        # et cote historique.
        assert not at.exception
        assert at.subheader[0].value == "Germany / revamp_inac / SEQ 1"
        assert load_sequence_history("Germany", "revamp_inac", "SEQ 1")
        assert not load_sequence_history("France", "revamp_inac", "SEQ 1")
