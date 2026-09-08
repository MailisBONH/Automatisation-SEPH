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

import qa.classification
import qa.history
import qa.report_diff
from conftest import fake_incrementing_datetime, make_details_report
from qa.archive import archive_report, delete_sequence_dir, list_reports
from qa.history import delete_classification, load_sequence_history, record_run

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


class TestHistoryExport:
    """Boutons d'export de l'historique complet (voir qa.history.
    export_history_csv/export_history_excel), dans le panneau "Vue
    d'ensemble"."""

    def test_export_buttons_are_present_and_exception_free(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()

        assert not at.exception
        export_buttons = [b for b in at.get("download_button") if b.label in ("⬇️ Excel (.xlsx)", "⬇️ CSV")]
        assert len(export_buttons) == 2


class TestManualReportDiffPanel:
    """Panneau "📤 Comparer deux rapports Excel (upload manuel)" - seul son
    etat par defaut (aucun fichier uploade) est verifiable via AppTest :
    l'upload de fichier lui-meme n'est pas simulable par ce harnais de test
    (meme limitation documentee pour home.py, voir TestHomeAppSmoke)."""

    def test_shows_the_mode_selector_and_upload_prompt_by_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()

        assert not at.exception
        assert at.radio(key="manual_diff_mode").options == ["Wording", "URL"]
        assert any("Uploadez les 2 rapports" in c.value for c in at.caption)


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
        # Séquence) pré-remplis avec les valeurs actuelles - on ne change que
        # le pays. Cibles par clé (pas par position) : la page a par ailleurs
        # un champ de recherche (barre "Vue d'ensemble") qui n'a pas de
        # relation d'ordre garantie avec ce formulaire.
        at.text_input(key="rename_country_France_revamp_inac_SEQ 1").set_value("Germany")
        at.text_input(key="rename_trigger_France_revamp_inac_SEQ 1").set_value("revamp_inac")
        at.text_input(key="rename_sequence_France_revamp_inac_SEQ 1").set_value("SEQ 1")
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

    def test_trigger_and_sequence_breadcrumbs_step_back_one_level_at_a_time(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()

        # Fil d'Ariane "trigger" (2e bouton) : revient au choix de sequence,
        # sans reperdre le pays ni le trigger deja choisis.
        trigger_crumb = next(b for b in at.button if b.label == "📁 revamp_inac")
        trigger_crumb.click().run()
        assert not at.exception
        assert at.subheader[0].value == "France / revamp_inac — choisissez une séquence"

        at.button(key="nav_sequence_SEQ 1").click().run()

        # Fil d'Ariane "pays" (1er bouton nomme, ici "France") : revient au
        # choix de trigger.
        country_crumb = next(b for b in at.button if b.label == "📁 France")
        country_crumb.click().run()
        assert not at.exception
        assert at.subheader[0].value == "France — choisissez un trigger"


class TestOverviewSearchBox:
    """Champ "🔍 Rechercher une séquence" du panneau "Vue d'ensemble" : doit
    rester une recherche texte litterale, jamais interpretee comme une
    regex (voir mask = ...str.contains(..., regex=False) dans
    pages/1_Historique.py) - un utilisateur peut taper des caracteres
    speciaux regex (parenthese non fermee, point, crochet...) sans faire
    planter toute la page."""

    def _seed_two_sequences(self):
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )
        record_run(
            "SEQ 2", "wording", total=10, ok=10, ko=0, score=100.0,
            country="Germany", trigger="welcome", sequence="SEQ 2",
        )

    def test_regex_metacharacters_in_search_do_not_crash_the_page(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_two_sequences()

        at = _run_page()
        # Parenthese non fermee : un regex invalide si interprete comme tel
        # (re.error / pyarrow.lib.ArrowInvalid avant le correctif regex=False).
        at.text_input(key="history_search").set_value("SEQ (1").run()

        assert not at.exception
        rows = at.dataframe[0].value.to_dict("records")
        # Recherche litterale de "SEQ (1" : aucune séquence archivée ne
        # contient cette sous-chaine exacte -> aucun resultat, mais pas de crash.
        assert rows == []

    def test_search_still_behaves_as_a_plain_literal_substring_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_two_sequences()

        at = _run_page()
        at.text_input(key="history_search").set_value("SEQ 1").run()

        assert not at.exception
        rows = at.dataframe[0].value.to_dict("records")
        assert len(rows) == 1
        assert rows[0]["Séquence"] == "SEQ 1"


class TestRecentSequences:
    """Boutons "🕘 Récemment consultées" au-dessus du fil d'Ariane (voir
    _record_recent_sequence) : permettent de revenir directement a une
    sequence deja regardee dans la session, sans re-cliquer les 3 niveaux
    de dossiers a chaque fois."""

    def _seed_two_sequences(self):
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )
        record_run(
            "SEQ 2", "wording", total=10, ok=10, ko=0, score=100.0,
            country="Germany", trigger="welcome", sequence="SEQ 2",
        )

    def _visit(self, at, country, trigger, sequence):
        at.button(key=f"nav_country_{country}").click().run()
        at.button(key=f"nav_trigger_{trigger}").click().run()
        at.button(key=f"nav_sequence_{sequence}").click().run()

    def test_previously_visited_sequence_appears_as_a_recent_quick_link(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_two_sequences()

        at = _run_page()
        self._visit(at, "France", "revamp_inac", "SEQ 1")

        at.button[0].click().run()  # "🏠 Pays" : retour a la racine de la navigation
        self._visit(at, "Germany", "welcome", "SEQ 2")

        assert not at.exception
        assert any("Récemment consultées" in c.value for c in at.caption)
        assert any(b.label == "📄 France / revamp_inac / SEQ 1" for b in at.button)
        # La sequence actuellement affichee n'apparait pas dans sa propre liste.
        assert not any(b.label == "📄 Germany / welcome / SEQ 2" for b in at.button)

    def test_clicking_a_recent_quick_link_navigates_directly_to_it(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_two_sequences()

        at = _run_page()
        self._visit(at, "France", "revamp_inac", "SEQ 1")
        at.button[0].click().run()
        self._visit(at, "Germany", "welcome", "SEQ 2")

        recent_button = next(b for b in at.button if b.label == "📄 France / revamp_inac / SEQ 1")
        recent_button.click().run()

        assert not at.exception
        assert at.subheader[0].value == "France / revamp_inac / SEQ 1"


class TestReportDownloads:
    def test_shows_a_download_button_per_archived_report(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )
        fake_report = tmp_path / "report.xlsx"
        fake_report.write_bytes(b"fake xlsx content")
        archive_report(str(fake_report), "France", "revamp_inac", "SEQ 1", mode="wording")

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()

        assert not at.exception
        assert not any("Aucun rapport disponible" in c.value for c in at.caption)
        # La page a aussi 2 boutons d'export de l'historique complet (Excel/CSV,
        # voir "Vue d'ensemble") - on cible specifiquement le bouton du rapport
        # archive (prefixe "QA_" par qa.archive.archive_report) plutot que de
        # compter tous les download_button de la page.
        download_buttons = [b for b in at.get("download_button") if b.label.startswith("⬇️ QA_")]
        assert len(download_buttons) == 1
        assert list_reports("France", "revamp_inac", "SEQ 1", mode="wording")[0].endswith(
            os.path.basename(download_buttons[0].label.replace("⬇️ ", ""))
        )


class TestRenameFormValidation:
    def _navigate_to_sequence(self, at):
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()
        return next(b for b in at.button if b.key and b.key.startswith("FormSubmitter"))

    def test_blank_field_shows_a_required_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()
        submit_button = self._navigate_to_sequence(at)
        at.text_input(key="rename_sequence_France_revamp_inac_SEQ 1").set_value("")  # Séquence vidée
        submit_button.click().run()

        assert not at.exception
        assert any("obligatoires" in e.value for e in at.error)
        # Rien n'a ete renomme : toujours "SEQ 1" cote historique.
        assert load_sequence_history("France", "revamp_inac", "SEQ 1")

    def test_submitting_unchanged_values_is_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()
        submit_button = self._navigate_to_sequence(at)
        submit_button.click().run()  # aucun champ modifie

        assert not at.exception
        assert any("Rien à changer" in i.value for i in at.info)


class TestDeleteSequence:
    def test_deleting_removes_reports_and_history(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )
        fake_report = tmp_path / "report.xlsx"
        fake_report.write_bytes(b"fake xlsx content")
        archive_report(str(fake_report), "France", "revamp_inac", "SEQ 1", mode="wording")

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()

        at.checkbox(key="confirm_delete_France_revamp_inac_SEQ 1").set_value(True).run()
        delete_button = next(b for b in at.button if b.label == "🗑️ Supprimer cette séquence")
        delete_button.click().run()

        assert not at.exception
        assert not load_sequence_history("France", "revamp_inac", "SEQ 1")
        assert list_reports("France", "revamp_inac", "SEQ 1", mode="wording") == []
        # Plus aucune sequence classee du tout (c'etait la seule) : l'arbre
        # est vide, la page retombe sur l'etat "Aucun rapport archive".
        assert any("Aucun rapport archivé" in i.value for i in at.info)


class TestReportDiffPanel:
    """Panneau "🔍 Comparer les 2 derniers rapports" (voir
    qa.report_diff.load_last_two_reports_diff) - deja visible sans avoir a
    deplier l'expander (AppTest rend l'arbre complet, voir les autres
    classes de ce fichier qui accedent deja au contenu de l'expander
    "Gérer cette séquence" sans le deplier)."""

    def _navigate_to_sequence(self, at):
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()

    def test_fewer_than_two_reports_shows_nothing_to_compare(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )
        report = tmp_path / "report.xlsx"
        make_details_report(report, [{"Status": "OK", "Component Name": "Header", "Element": "Title",
                                       "Source Sheet": "SEQ 1"}])
        archive_report(str(report), "France", "revamp_inac", "SEQ 1", mode="wording")

        at = _run_page()
        self._navigate_to_sequence(at)

        assert not at.exception
        assert any("rien à comparer encore" in c.value for c in at.caption)

    def test_regression_between_the_two_latest_reports_is_shown(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        fake_incrementing_datetime(monkeypatch)
        record_run(
            "SEQ 1", "wording", total=1, ok=0, ko=1, score=0.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        older = tmp_path / "older.xlsx"
        make_details_report(older, [{
            "Component Name": "Header", "Element": "Title", "Source Sheet": "SEQ 1",
            "Status": "OK", "Error Type": "", "HTML Wording": "Bonjour",
        }])
        archive_report(str(older), "France", "revamp_inac", "SEQ 1", mode="wording")

        newer = tmp_path / "newer.xlsx"
        make_details_report(newer, [{
            "Component Name": "Header", "Element": "Title", "Source Sheet": "SEQ 1",
            "Status": "KO", "Error Type": "Content mismatch", "HTML Wording": "Bonsoir",
        }])
        archive_report(str(newer), "France", "revamp_inac", "SEQ 1", mode="wording")

        at = _run_page()
        self._navigate_to_sequence(at)

        assert not at.exception
        assert any("1 régression(s)" in c.value for c in at.caption)
        diff_table = next(df for df in at.dataframe if "Component Name" in df.value.columns)
        assert diff_table.value.iloc[0]["Statut avant"] == "OK"
        assert diff_table.value.iloc[0]["Statut après"] == "KO"

    def test_two_identical_reports_show_no_changes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        fake_incrementing_datetime(monkeypatch)
        record_run(
            "SEQ 1", "wording", total=1, ok=1, ko=0, score=100.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        row = {
            "Component Name": "Header", "Element": "Title", "Source Sheet": "SEQ 1",
            "Status": "OK", "Error Type": "", "HTML Wording": "Bonjour",
        }
        for _ in range(2):
            path = tmp_path / f"report_{_}.xlsx"
            make_details_report(path, [row])
            archive_report(str(path), "France", "revamp_inac", "SEQ 1", mode="wording")

        at = _run_page()
        self._navigate_to_sequence(at)

        assert not at.exception
        assert any("Aucun changement entre les deux rapports" in c.value for c in at.caption)


class TestLegacyMigrationBanner:
    def test_legacy_qualified_sequence_is_migrated_and_reported(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Sequence encore archivee sous un ancien nom qualifie (avant
        # l'unification "SEQ 1" pour wording ET url, voir
        # qa.classification.normalize_sequence_name) : doit etre fusionnee
        # automatiquement des l'ouverture de la page, avec un message.
        record_run(
            "SEQ 1 NON LOYAL", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1 NON LOYAL",
        )

        at = _run_page()

        assert not at.exception
        assert any("fusionnée(s) automatiquement" in i.value for i in at.info)
        assert load_sequence_history("France", "revamp_inac", "SEQ 1")
        assert not load_sequence_history("France", "revamp_inac", "SEQ 1 NON LOYAL")


class TestStaleNavigationFallback:
    """Bug 1 : si history_nav (session_state) pointe encore vers un
    pays/trigger qui a disparu de l'arbre fraichement reconstruit (ex:
    supprime/renomme ailleurs, ou par une autre session, entre deux reruns),
    la page doit retomber silencieusement au niveau de navigation superieur
    plutot que de planter avec un KeyError sur tree[...]."""

    def _seed_two_sequences(self):
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )
        record_run(
            "SEQ 2", "wording", total=10, ok=10, ko=0, score=100.0,
            country="Germany", trigger="welcome", sequence="SEQ 2",
        )

    def test_stale_country_falls_back_to_country_selection_without_crashing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_two_sequences()

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()

        # "France" disparait entierement de l'arbre (supprimee "ailleurs"),
        # mais history_nav en session_state pointe toujours dessus - comme si
        # une autre session/onglet avait supprime cette sequence entre deux
        # reruns de celle-ci.
        delete_sequence_dir("France", "revamp_inac", "SEQ 1")
        delete_classification("France", "revamp_inac", "SEQ 1")
        assert at.session_state["history_nav"]["country"] == "France"

        at.run()

        assert not at.exception
        assert at.subheader[0].value == "Choisissez un pays"
        assert at.session_state["history_nav"] == {"country": None, "trigger": None, "sequence": None}

    def test_stale_trigger_falls_back_to_trigger_selection_without_crashing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )
        record_run(
            "SEQ 2", "wording", total=10, ok=10, ko=0, score=100.0,
            country="France", trigger="other_trigger", sequence="SEQ 2",
        )

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()

        # Seul "revamp_inac" disparait (son unique sequence) - "France" reste
        # valide (il lui reste "other_trigger"), mais history_nav pointe
        # toujours vers le trigger disparu.
        delete_sequence_dir("France", "revamp_inac", "SEQ 1")
        delete_classification("France", "revamp_inac", "SEQ 1")

        at.run()

        assert not at.exception
        assert at.subheader[0].value == "France — choisissez un trigger"
        assert at.session_state["history_nav"] == {"country": "France", "trigger": None, "sequence": None}


class TestRecentSequencesPruning:
    """Bug 2 : renommer/fusionner ou supprimer une sequence ne doit pas
    laisser son ancienne identite comme quicklink mort dans "Recemment
    consultees" (voir _forget_recent_sequence)."""

    def _seed_two_sequences(self):
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )
        record_run(
            "SEQ 2", "wording", total=10, ok=10, ko=0, score=100.0,
            country="Germany", trigger="welcome", sequence="SEQ 2",
        )

    def _visit(self, at, country, trigger, sequence):
        at.button(key=f"nav_country_{country}").click().run()
        at.button(key=f"nav_trigger_{trigger}").click().run()
        at.button(key=f"nav_sequence_{sequence}").click().run()

    def test_deleting_a_recently_viewed_sequence_removes_its_quicklink(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_two_sequences()

        at = _run_page()
        # SEQ 1 est vue en premier (elle finira dans "Recemment consultees"),
        # puis on va sur SEQ 2 pour repartir a zero au niveau navigation.
        self._visit(at, "France", "revamp_inac", "SEQ 1")
        at.button[0].click().run()  # "🏠 Pays"
        self._visit(at, "Germany", "welcome", "SEQ 2")
        assert any(b.label == "📄 France / revamp_inac / SEQ 1" for b in at.button)

        # On revient sur SEQ 1 via son quicklink, puis on la supprime.
        recent_button = next(b for b in at.button if b.label == "📄 France / revamp_inac / SEQ 1")
        recent_button.click().run()
        at.checkbox(key="confirm_delete_France_revamp_inac_SEQ 1").set_value(True).run()
        delete_button = next(b for b in at.button if b.label == "🗑️ Supprimer cette séquence")
        delete_button.click().run()

        assert not at.exception
        assert ("France", "revamp_inac", "SEQ 1") not in at.session_state["recent_sequences"]
        assert not any(b.label == "📄 France / revamp_inac / SEQ 1" for b in at.button)

    def test_renaming_a_recently_viewed_sequence_replaces_its_quicklink(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_two_sequences()

        at = _run_page()
        self._visit(at, "France", "revamp_inac", "SEQ 1")
        at.button[0].click().run()  # "🏠 Pays"
        self._visit(at, "Germany", "welcome", "SEQ 2")
        assert any(b.label == "📄 France / revamp_inac / SEQ 1" for b in at.button)

        recent_button = next(b for b in at.button if b.label == "📄 France / revamp_inac / SEQ 1")
        recent_button.click().run()

        at.text_input(key="rename_country_France_revamp_inac_SEQ 1").set_value("Germany")
        at.text_input(key="rename_trigger_France_revamp_inac_SEQ 1").set_value("revamp_inac")
        at.text_input(key="rename_sequence_France_revamp_inac_SEQ 1").set_value("SEQ 1")
        submit_button = next(b for b in at.button if b.key and b.key.startswith("FormSubmitter"))
        submit_button.click().run()

        assert not at.exception
        assert ("France", "revamp_inac", "SEQ 1") not in at.session_state["recent_sequences"]
        assert ("Germany", "revamp_inac", "SEQ 1") in at.session_state["recent_sequences"]


class TestHistoryOverviewCaching:
    """load_classified_history()+build_history_tree() (la lecture SQL + la
    construction de l'arbre de navigation) sont mis en cache par
    _load_history_overview (st.cache_data, voir pages/1_Historique.py) pour
    ne pas rerequeter/reconstruire l'arbre a CHAQUE interaction avec un
    widget de la page - Streamlit reexecute tout le script a chaque rerun,
    meme pour un rerun qui ne mute rien (taper dans la recherche, naviguer
    dans les dossiers deja charges...).

    migrate_legacy_loyalty_sequences() (qa.classification) appelle elle-meme
    build_history_tree() SANS `entries=` a chaque rerun (reprise retroactive
    idempotente, voir pages/1_Historique.py) - donc SA PROPRE lecture
    interne de load_classified_history() est neutralisee ici (stub sans
    effet) pour isoler precisement le comportement de _load_history_overview,
    seule fonction concernee par cette amelioration."""

    def _stub_out_loyalty_migration(self, monkeypatch):
        monkeypatch.setattr(qa.classification, "migrate_legacy_loyalty_sequences", lambda: [])

    def test_rerun_without_mutation_does_not_re_invoke_the_underlying_query(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._stub_out_loyalty_migration(monkeypatch)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        calls = {"n": 0}
        original_load = qa.history.load_classified_history

        def _counting_load(*args, **kwargs):
            calls["n"] += 1
            return original_load(*args, **kwargs)

        monkeypatch.setattr(qa.history, "load_classified_history", _counting_load)

        at = _run_page()
        assert not at.exception
        assert calls["n"] == 1  # premier appel : cache froid, un seul appel reel.

        # Rerun sans aucune mutation de l'historique - juste taper dans le
        # champ de recherche (widget de la page, section "Vue d'ensemble") :
        # Streamlit reexecute tout le script, mais _load_history_overview
        # doit servir le resultat deja en cache plutot que rerequeter la base.
        at.text_input(key="history_search").set_value("SEQ 1").run()
        assert not at.exception
        assert calls["n"] == 1

        # Un deuxieme rerun different (navigation dans les dossiers deja
        # charges) ne doit pas non plus reinvoquer la requete.
        at.button(key="nav_country_France").click().run()
        assert not at.exception
        assert calls["n"] == 1

    def test_rename_action_is_visible_on_the_very_next_rerun(self, tmp_path, monkeypatch):
        """Critique : sans invalidation correcte du cache, la "Vue
        d'ensemble" continuerait d'afficher l'ancien pays juste apres un
        renommage reussi, jusqu'au prochain redemarrage du process."""
        monkeypatch.chdir(tmp_path)
        self._stub_out_loyalty_migration(monkeypatch)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()

        at.text_input(key="rename_country_France_revamp_inac_SEQ 1").set_value("Germany")
        at.text_input(key="rename_trigger_France_revamp_inac_SEQ 1").set_value("revamp_inac")
        at.text_input(key="rename_sequence_France_revamp_inac_SEQ 1").set_value("SEQ 1")
        submit_button = next(b for b in at.button if b.key and b.key.startswith("FormSubmitter"))
        submit_button.click().run()

        assert not at.exception
        # Le tableau "Vue d'ensemble" (construit a partir de l'arbre mis en
        # cache) doit refleter le renommage des ce meme rerun.
        rows = at.dataframe[0].value.to_dict("records")
        assert len(rows) == 1
        assert rows[0]["Pays"] == "Germany"

        # La navigation par dossiers (elle aussi construite depuis l'arbre
        # mis en cache) ne doit plus proposer "France".
        at.button[0].click().run()  # "🏠 Pays" : retour au choix de pays
        assert any(b.label == "📁 Germany" for b in at.button)
        assert not any(b.label == "📁 France" for b in at.button)

    def test_delete_action_is_visible_on_the_very_next_rerun(self, tmp_path, monkeypatch):
        """Critique : sans invalidation correcte du cache, la sequence
        supprimee continuerait d'apparaitre dans la "Vue d'ensemble" et la
        navigation par dossiers juste apres sa suppression."""
        monkeypatch.chdir(tmp_path)
        self._stub_out_loyalty_migration(monkeypatch)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )
        record_run(
            "SEQ 2", "wording", total=10, ok=10, ko=0, score=100.0,
            country="Germany", trigger="welcome", sequence="SEQ 2",
        )

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()

        at.checkbox(key="confirm_delete_France_revamp_inac_SEQ 1").set_value(True).run()
        delete_button = next(b for b in at.button if b.label == "🗑️ Supprimer cette séquence")
        delete_button.click().run()

        assert not at.exception
        rows = at.dataframe[0].value.to_dict("records")
        assert len(rows) == 1
        assert rows[0]["Pays"] == "Germany"


class TestReportDiffCaching:
    """load_last_two_reports_diff() (relit et compare jusqu'a 2 classeurs
    Excel par mode) est mis en cache par _cached_report_diff (st.cache_data,
    cle sur le chemin absolu + mtime + taille des rapports concernes, voir
    pages/1_Historique.py) pour ne pas reparser ces classeurs a CHAQUE
    interaction avec un widget de la page, meme quand l'expander "Comparer
    les 2 derniers rapports" reste replie (AppTest - comme Streamlit - execute
    le contenu d'un expander independamment de son etat visuel)."""

    def test_rerun_without_new_report_does_not_re_invoke_the_underlying_diff(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        fake_incrementing_datetime(monkeypatch)
        record_run(
            "SEQ 1", "wording", total=1, ok=0, ko=1, score=0.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        older = tmp_path / "older.xlsx"
        make_details_report(older, [{
            "Component Name": "Header", "Element": "Title", "Source Sheet": "SEQ 1",
            "Status": "OK", "Error Type": "", "HTML Wording": "Bonjour",
        }])
        archive_report(str(older), "France", "revamp_inac", "SEQ 1", mode="wording")

        newer = tmp_path / "newer.xlsx"
        make_details_report(newer, [{
            "Component Name": "Header", "Element": "Title", "Source Sheet": "SEQ 1",
            "Status": "KO", "Error Type": "Content mismatch", "HTML Wording": "Bonsoir",
        }])
        archive_report(str(newer), "France", "revamp_inac", "SEQ 1", mode="wording")

        calls = {"n": 0}
        original_diff = qa.report_diff.load_last_two_reports_diff

        def _counting_diff(*args, **kwargs):
            calls["n"] += 1
            return original_diff(*args, **kwargs)

        monkeypatch.setattr(qa.report_diff, "load_last_two_reports_diff", _counting_diff)

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()

        assert not at.exception
        # 2 modes essayes (wording, url) au premier affichage de la page de
        # sequence : au plus 2 appels reels, meme si "url" retourne None
        # faute de rapports.
        first_call_count = calls["n"]
        assert first_call_count >= 1

        # Rerun sans qu'aucun nouveau rapport ne soit archive - juste cocher
        # la case de confirmation de suppression (widget de la page, sans
        # valider la suppression) : ne doit pas reparser/rediffer les
        # classeurs Excel.
        at.checkbox(key="confirm_delete_France_revamp_inac_SEQ 1").set_value(True).run()
        assert not at.exception
        assert calls["n"] == first_call_count

    def test_new_archived_report_is_diffed_on_the_very_next_rerun(self, tmp_path, monkeypatch):
        """Critique : une rotation (nouveau rapport archive, ancien evince
        par KEEP_REPORTS) doit etre vue au rerun suivant, pas seulement apres
        redemarrage du process - le panneau de diff ne doit jamais rester
        bloque sur une comparaison perimee."""
        monkeypatch.chdir(tmp_path)
        fake_incrementing_datetime(monkeypatch)
        record_run(
            "SEQ 1", "wording", total=1, ok=0, ko=1, score=0.0,
            country="France", trigger="revamp_inac", sequence="SEQ 1",
        )

        older = tmp_path / "older.xlsx"
        make_details_report(older, [{
            "Component Name": "Header", "Element": "Title", "Source Sheet": "SEQ 1",
            "Status": "OK", "Error Type": "", "HTML Wording": "Bonjour",
        }])
        archive_report(str(older), "France", "revamp_inac", "SEQ 1", mode="wording")

        at = _run_page()
        at.button(key="nav_country_France").click().run()
        at.button(key="nav_trigger_revamp_inac").click().run()
        at.button(key="nav_sequence_SEQ 1").click().run()
        assert any("rien à comparer encore" in c.value for c in at.caption)

        # Un 2e rapport est archive "en dehors" de cette page (comme le
        # ferait home.py apres une nouvelle comparaison), puis on rerun la
        # MEME session sans redemarrer AppTest.
        newer = tmp_path / "newer.xlsx"
        make_details_report(newer, [{
            "Component Name": "Header", "Element": "Title", "Source Sheet": "SEQ 1",
            "Status": "KO", "Error Type": "Content mismatch", "HTML Wording": "Bonsoir",
        }])
        archive_report(str(newer), "France", "revamp_inac", "SEQ 1", mode="wording")

        at.run()

        assert not at.exception
        assert any("1 régression(s)" in c.value for c in at.caption)
