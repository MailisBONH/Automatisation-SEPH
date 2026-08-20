"""
Tests pour home.py (page principale de l'outil QA) : jusqu'ici la seule
partie du code sans aucune couverture (voir README). Deux familles de
tests :
- Tests unitaires directs sur les fonctions pures du module (pas de widget
  Streamlit implique) : highlight_differences, _prepare_display_df,
  _run_items, _archive_key.
- Tests de fumee via streamlit.testing.v1.AppTest, qui execute reellement
  le script dans un runtime Streamlit simule : verifient que l'app demarre
  sans exception et que les widgets cles (mode, cases a cocher) reagissent
  comme attendu, ce qu'un import direct ne peut pas verifier (home.py
  execute son UI au niveau module, hors de toute fonction).
"""
import os
import shutil
import tempfile

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from conftest import make_wording_workbook

HOME_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "home.py")

# home.py execute son UI (et notamment qa.cleanup.cleanup_old_temp_files_throttled,
# qui ecrit un marqueur dans temp/) au niveau module, des l'import : on isole
# ce premier import dans un dossier temporaire jetable pour que "pytest"
# ne touche jamais le vrai dossier temp/ du projet. Les tests AppTest
# ci-dessous n'ont pas ce probleme (ils executent le script via leur propre
# mecanisme, pas un import Python) et font deja leur propre isolation via
# monkeypatch.chdir(tmp_path).
_isolated_import_dir = tempfile.mkdtemp(prefix="qa_home_import_")
_original_cwd = os.getcwd()
os.chdir(_isolated_import_dir)
try:
    import home
finally:
    os.chdir(_original_cwd)
    shutil.rmtree(_isolated_import_dir, ignore_errors=True)


class TestHighlightDifferences:
    def test_identical_texts_have_no_highlight(self):
        excel, html_out = home.highlight_differences("Hello world", "Hello world")
        assert excel == "Hello world"
        assert html_out == "Hello world"
        assert "<span" not in excel

    def test_word_level_diff_wraps_only_the_differing_word(self):
        excel, html_out = home.highlight_differences("Hello world", "Hello there")
        assert excel == 'Hello <span style=\'font-weight:bold;color:#d62828\'>world</span>'
        assert html_out == 'Hello <span style=\'font-weight:bold;color:#d62828\'>there</span>'

    def test_char_level_diff_used_for_urls(self):
        excel, html_out = home.highlight_differences("https://a.com/x", "https://a.com/y", char_level=True)
        assert "<span" in excel and "x</span>" in excel
        assert "<span" in html_out and "y</span>" in html_out
        # Pas de separateur artificiel introduit entre les caracteres.
        assert "https://a.com/" in excel.replace("<span style='font-weight:bold;color:#d62828'>", "")

    def test_none_values_are_treated_as_empty_strings(self):
        excel, html_out = home.highlight_differences(None, None)
        assert excel == "" and html_out == ""


class TestPrepareDisplayDf:
    def test_wording_mode_marks_diff_only_on_ko_rows(self):
        results = [
            {"Component Name": "Header", "Status": "OK", "Excel Wording": "Hello", "HTML Wording": "Hello", "_html_order": 0},
            {"Component Name": "Footer", "Status": "KO", "Excel Wording": "Hello world", "HTML Wording": "Hello there", "_html_order": 1},
        ]
        df, df_display = home._prepare_display_df(results, mode="wording")

        assert len(df) == 2
        assert "_html_order" not in df_display.columns
        assert "<span" not in df_display.iloc[0]["HTML Wording"]
        assert "<span" in df_display.iloc[1]["HTML Wording"]

    def test_url_mode_renames_columns_and_uses_char_level_diff(self):
        results = [
            {
                "Component Name": "", "Source Section": "SEQ 1", "Expected Class": "audit-component-a",
                "HTML Class": "audit-component-b", "Excel Wording": "https://a.com/x",
                "HTML Wording": "https://a.com/y", "Status": "KO", "Error Type": "URL not found",
                "Similarity": "80%", "Occurrences": 1, "_html_order": 0,
            },
        ]
        df, df_display = home._prepare_display_df(results, mode="url")

        assert "URL attendue (Excel)" in df_display.columns
        assert "URL trouvée (HTML)" in df_display.columns
        assert "Excel Wording" not in df_display.columns
        assert "<span" in df_display.iloc[0]["URL trouvée (HTML)"]

    def test_rows_are_sorted_by_html_order(self):
        results = [
            {"Component Name": "B", "Status": "OK", "Excel Wording": "B", "HTML Wording": "B", "_html_order": 1},
            {"Component Name": "A", "Status": "OK", "Excel Wording": "A", "HTML Wording": "A", "_html_order": 0},
        ]
        df, _ = home._prepare_display_df(results, mode="wording")
        assert list(df["Excel Wording"]) == ["A", "B"]


class TestRunItems:
    def test_single_kind_returns_one_item(self):
        last_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1",
            "results": [{"Status": "OK"}], "report_path": "r.xlsx",
        }
        items = home._run_items(last_run)
        assert items == [{"mode": "wording", "sequence": "SEQ 1", "rows": [{"Status": "OK"}], "report_path": "r.xlsx"}]

    def test_single_kind_falls_back_to_sheet_name_when_sequence_missing(self):
        # Cas onglet UTM "plat" (voir home.py) : keyword vide, sequence
        # absente du dict, on retombe sur sheet_name.
        last_run = {
            "kind": "single", "mode": "url", "sheet_name": "UTM TRIGGER",
            "results": [], "report_path": "r.xlsx",
        }
        items = home._run_items(last_run)
        assert items[0]["sequence"] == "UTM TRIGGER"

    def test_batch_kind_returns_one_item_per_sequence_without_report_path(self):
        last_run = {
            "kind": "batch", "mode": "wording",
            "sequences": {"SEQ 1": [{"Status": "OK"}], "SEQ 2": [{"Status": "KO"}]},
        }
        items = home._run_items(last_run)
        assert len(items) == 2
        assert all(item["report_path"] is None for item in items)
        assert {item["sequence"] for item in items} == {"SEQ 1", "SEQ 2"}

    def test_combined_test_kind_returns_one_wording_item_and_one_url_item(self):
        last_run = {
            "kind": "combined_test",
            "wording_sequence": "SEQ 1", "wording_results": [{"Status": "OK"}], "wording_report_path": "w.xlsx",
            "url_sequence": "SEQ 1 FID", "url_results": [{"Status": "KO"}], "url_report_path": "u.xlsx",
        }
        items = home._run_items(last_run)
        assert {item["mode"] for item in items} == {"wording", "url"}
        assert len(items) == 2

    def test_combined_batch_kind_skips_pairs_with_empty_rows_on_one_side(self):
        # Une paire dont un cote a echoue (voir le try/except du bouton
        # Comparer) a des rows vides pour ce cote : ne doit pas etre
        # archivee (rien de reel a y mettre).
        last_run = {
            "kind": "combined_batch",
            "pairs": [
                {
                    "wording_sequence": "SEQ 1", "wording_rows": [{"Status": "OK"}],
                    "url_sequence": "SEQ 1 FID", "url_rows": [],
                },
            ],
        }
        items = home._run_items(last_run)
        assert len(items) == 1
        assert items[0]["mode"] == "wording"


class TestArchiveKey:
    def test_single_kind_uses_report_path(self):
        last_run = {"kind": "single", "report_path": "r.xlsx"}
        assert home._archive_key(last_run) == "archived::r.xlsx"

    def test_combined_kinds_use_both_report_paths(self):
        last_run = {"kind": "combined_test", "wording_report_path": "w.xlsx", "url_report_path": "u.xlsx"}
        assert home._archive_key(last_run) == "archived::w.xlsx::u.xlsx"

    def test_same_run_always_produces_the_same_key(self):
        last_run = {"kind": "single", "report_path": "r.xlsx"}
        assert home._archive_key(last_run) == home._archive_key(dict(last_run))


class TestWriteBatchHtml:
    class _FakeUploadedFile:
        def __init__(self, content: bytes):
            self._content = content

        def getvalue(self):
            return self._content

    def test_writes_the_uploaded_bytes_under_a_session_scoped_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = home._write_batch_html(self._FakeUploadedFile(b"<html>hello</html>"), "sess123")

        assert path.startswith("temp/sess123_") and path.endswith(".html")
        assert open(path, "rb").read() == b"<html>hello</html>"

    def test_two_calls_for_the_same_session_get_distinct_paths(self, tmp_path, monkeypatch):
        # Mode batch : plusieurs fichiers HTML pour une meme session ne
        # doivent jamais s'ecraser les uns les autres pendant la boucle.
        monkeypatch.chdir(tmp_path)
        path1 = home._write_batch_html(self._FakeUploadedFile(b"A"), "sess123")
        path2 = home._write_batch_html(self._FakeUploadedFile(b"B"), "sess123")
        assert path1 != path2


class TestRunBatchQa:
    def _make_excel_and_html(self, tmp_path, wording="Hallo", html_text="Hallo"):
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", wording, "audit-component-header"]],
        })
        html_file = tmp_path / "email.html"
        html_file.write_text(
            f'<div class="text-contribution"><p class="audit-component-header">{html_text}</p></div>',
            encoding="utf-8",
        )
        return excel_file, str(html_file)

    def test_returns_results_on_success(self, tmp_path):
        excel_file, html_file = self._make_excel_and_html(tmp_path)

        results = home._run_batch_qa(html_file, excel_file, "SEQ 1", "wording", False, label="SEQ 1")

        assert results is not None
        assert results[0]["Status"] == "OK"

    def test_returns_none_and_shows_an_error_on_unreadable_html(self, tmp_path):
        excel_file, html_file = self._make_excel_and_html(tmp_path)
        # Ecrase le HTML valide par une sequence non-UTF8 (voir QA_RUN_READ_ERRORS).
        with open(html_file, "wb") as f:
            f.write("café".encode("latin-1"))

        results = home._run_batch_qa(html_file, excel_file, "SEQ 1", "wording", False, label="SEQ 1")

        assert results is None


class TestDoArchive:
    """
    _do_archive est le coeur de l'orchestration d'archivage (voir
    ensure_archived) : resout le nom de sequence, copie le rapport dans
    output/<Pays>/<Trigger>/<Sequence>/, et enregistre l'entree
    d'historique correspondante. Isole des vraies output/history.jsonl du
    projet (voir isolated_archive_dirs) et du vrai historique deja archive
    (build_history_tree stubbe a vide) pour rendre resolve_sequence_alias
    deterministe, independant de tout run reel deja archive par ailleurs.

    home.py, qa.archive, qa.combined_report et qa.report importent chacun
    OUTPUT_DIR/record_run separement (from qa.config import OUTPUT_DIR /
    from qa.history import record_run cree une liaison PROPRE a chaque
    module) : les QUATRE doivent etre isoles, pas seulement ceux que
    _do_archive appelle directement. Un oubli ici a deja cause une
    suppression accidentelle de vrais fichiers projet (item["report_path"]
    absent -> _do_archive retombe sur qa.report.save_report, qui ecrirait
    sinon dans le vrai output/ et le vrai output/history.jsonl - voir le
    mode "batch", ou report_path est justement None).

    home.find_regression (voir qa.history.find_regression, appele par
    _do_archive AVANT chaque record_run pour detecter une regression de
    score) lit aussi l'historique par son propre chemin par defaut - doit
    etre redirige vers le meme tmp_path que record_run, sinon il lirait le
    vrai output/history.jsonl du projet a chaque test.
    """

    @pytest.fixture(autouse=True)
    def isolated_archive_dirs(self, tmp_path, monkeypatch):
        import qa.archive as archive_module
        import qa.combined_report as combined_report_module
        import qa.report as report_module
        from qa.history import find_regression as real_find_regression
        from qa.history import record_run as real_record_run

        monkeypatch.setattr(archive_module, "OUTPUT_DIR", str(tmp_path / "output"))
        monkeypatch.setattr(combined_report_module, "OUTPUT_DIR", str(tmp_path / "output"))
        monkeypatch.setattr(report_module, "OUTPUT_DIR", str(tmp_path / "output"))
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: {})

        self.history_path = str(tmp_path / "history.jsonl")
        redirect_record_run = lambda *a, path=self.history_path, **k: real_record_run(*a, path=path, **k)
        monkeypatch.setattr(home, "record_run", redirect_record_run)
        monkeypatch.setattr(report_module, "record_run", redirect_record_run)

        redirect_find_regression = lambda *a, path=self.history_path, **k: real_find_regression(*a, path=path, **k)
        monkeypatch.setattr(home, "find_regression", redirect_find_regression)

    def _make_details_report(self, path, rows):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Details"
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row[h] for h in headers])
        wb.save(path)

    def test_archives_a_single_run_and_records_its_score_in_history(self, tmp_path):
        from qa.history import load_sequence_history

        report_path = tmp_path / "report.xlsx"
        self._make_details_report(str(report_path), [{"Status": "OK"}, {"Status": "KO"}])
        last_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1 NON LOYAL",
            "results": [{"Status": "OK"}, {"Status": "KO"}], "report_path": str(report_path),
        }

        home._do_archive(last_run, "Germany", "Revamp Inac")

        entries = load_sequence_history("Germany", "Revamp Inac", "SEQ 1", path=self.history_path)
        assert any(e["mode"] == "wording" and e["score"] == 50.0 for e in entries)

    def test_archives_a_batch_item_with_no_pregenerated_report_path(self, tmp_path):
        # Regression : un item "batch" (voir _run_items) a report_path=None -
        # _do_archive doit alors appeler qa.report.save_report lui-meme.
        # Avant la correction de ce fixture, qa.report.OUTPUT_DIR/record_run
        # n'etaient pas isoles : cet appel aurait ecrit dans le vrai output/
        # et le vrai output/history.jsonl du projet.
        from qa.history import load_sequence_history

        def _row(component, status, criticality):
            return {
                "Component Name": component, "Element": "", "Source Sheet": "",
                "Expected Class": "", "HTML Class": "", "Excel Wording": "", "HTML Wording": "",
                "Status": status, "Error Type": "", "Similarity": "", "Criticality": criticality,
                "Match Type": "",
            }

        rows = [_row("Header", "OK", ""), _row("Footer", "KO", "High")]
        last_run = {"kind": "batch", "mode": "wording", "sequences": {"SEQ 1 NON LOYAL": rows}}

        home._do_archive(last_run, "Germany", "Revamp Inac")

        entries = load_sequence_history("Germany", "Revamp Inac", "SEQ 1", path=self.history_path)
        assert any(e["mode"] == "wording" and e["score"] == 50.0 for e in entries)
        generated = list((tmp_path / "output").glob("QA_*.xlsx"))
        assert len(generated) >= 1

    def test_generates_a_combined_report_once_wording_and_url_are_both_archived(self, tmp_path):
        from qa.history import load_sequence_history

        wording_report = tmp_path / "wording.xlsx"
        self._make_details_report(str(wording_report), [{"Status": "OK"}])
        url_report = tmp_path / "url.xlsx"
        self._make_details_report(str(url_report), [{"Status": "OK"}])

        # normalize_sequence_name reduit "SEQ 1 NON LOYAL"/"SEQ 1 NON FID" au
        # meme "SEQ 1" : les deux tombent dans le meme dossier de sequence,
        # ce qui doit declencher la generation du rapport combine.
        last_run = {
            "kind": "combined_test",
            "wording_sequence": "SEQ 1 NON LOYAL", "wording_results": [{"Status": "OK"}],
            "wording_report_path": str(wording_report),
            "url_sequence": "SEQ 1 NON FID", "url_results": [{"Status": "OK"}],
            "url_report_path": str(url_report),
        }

        home._do_archive(last_run, "Germany", "Revamp Inac")

        entries = load_sequence_history("Germany", "Revamp Inac", "SEQ 1", path=self.history_path)
        assert any(e["mode"] == "combined" and e["score"] == 100.0 for e in entries)

    def test_records_error_type_breakdown(self, tmp_path):
        from qa.history import load_sequence_history

        report_path = tmp_path / "report.xlsx"
        rows = [{"Status": "OK"}, {"Status": "KO", "Error Type": "Minor typo"},
                {"Status": "KO", "Error Type": "Minor typo"}, {"Status": "KO", "Error Type": "Content mismatch"}]
        self._make_details_report(str(report_path), rows)
        last_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1 NON LOYAL",
            "results": rows, "report_path": str(report_path),
        }

        home._do_archive(last_run, "Germany", "Revamp Inac")

        entries = load_sequence_history("Germany", "Revamp Inac", "SEQ 1", path=self.history_path)
        wording_entry = next(e for e in entries if e["mode"] == "wording")
        assert wording_entry["error_types"] == {"Minor typo": 2, "Content mismatch": 1}

    def test_returns_empty_list_when_no_regression(self, tmp_path):
        report_path = tmp_path / "report.xlsx"
        self._make_details_report(str(report_path), [{"Status": "OK"}, {"Status": "KO"}])
        last_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1 NON LOYAL",
            "results": [{"Status": "OK"}, {"Status": "KO"}], "report_path": str(report_path),
        }

        regressions = home._do_archive(last_run, "Germany", "Revamp Inac")

        assert regressions == []

    def test_returns_a_regression_when_score_drops_significantly(self, tmp_path):
        first_report = tmp_path / "first.xlsx"
        self._make_details_report(str(first_report), [{"Status": "OK"}] * 10)
        first_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1 NON LOYAL",
            "results": [{"Status": "OK"}] * 10, "report_path": str(first_report),
        }
        home._do_archive(first_run, "Germany", "Revamp Inac")

        second_report = tmp_path / "second.xlsx"
        second_rows = [{"Status": "OK"}] * 2 + [{"Status": "KO"}] * 8
        self._make_details_report(str(second_report), second_rows)
        second_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1 NON LOYAL",
            "results": second_rows, "report_path": str(second_report),
        }

        regressions = home._do_archive(second_run, "Germany", "Revamp Inac")

        assert len(regressions) == 1
        assert regressions[0]["previous_score"] == 100.0
        assert regressions[0]["current_score"] == 20.0


def _make_combined_excel(tmp_path, filename="combined.xlsx"):
    """Classeur avec un onglet wording (Allemand) ET un onglet UTM (meme
    trigger) - necessaire pour que detect_classification detecte a la fois
    un pays (langue) ET un trigger (onglet UTM), voir tests/test_classification.py."""
    path = tmp_path / filename
    wording_rows = [
        ["", "", "", ""],
        ["COMPONENT NAME", "ELEMENT", "TRANSLATION GERMAN", "TECHNICAL NAME"],
        ["Header", "Title", "Hallo Welt", "audit-component-header"],
    ]
    utm_rows = [
        [None, "!! CHECK !!", None, None, None, None, None, None],
        [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
        [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
        ["HEADER", "https://example.com/", "trigger", "email", "revamp_inac", "seq1", "",
         "https://www.example.de/?utm_content=header"],
    ]
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(wording_rows).to_excel(writer, sheet_name="SEQ 1", index=False, header=False)
        pd.DataFrame(utm_rows).to_excel(writer, sheet_name="UTM TRIGGER", index=False, header=False)
    return str(path)


class TestEnsureArchived:
    """
    ensure_archived decide entre archivage automatique (pays/trigger
    detectes) et ouverture d'une pop-up de completion manuelle - voir
    _do_archive/_prompt_missing_classification pour ce que chaque branche
    declenche reellement.
    """

    @pytest.fixture(autouse=True)
    def isolated_ensure_archived(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: {})
        self.do_archive_calls = []
        monkeypatch.setattr(home, "_do_archive", lambda last_run, c, t: self.do_archive_calls.append((c, t)))
        self.prompt_calls = []
        monkeypatch.setattr(
            home, "_prompt_missing_classification",
            lambda last_run, guess, sequences: self.prompt_calls.append((guess, sequences))
        )
        yield
        import streamlit as st
        st.session_state.clear()

    def test_auto_archives_when_country_and_trigger_are_both_detected(self, tmp_path):
        excel_file = _make_combined_excel(tmp_path)
        last_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1", "excel_path": excel_file,
            "results": [{"Status": "OK"}], "report_path": str(tmp_path / "report.xlsx"),
        }

        home.ensure_archived(last_run)

        assert self.do_archive_calls == [("Germany", "Revamp Inac")]
        assert self.prompt_calls == []

    def test_opens_the_completion_prompt_when_nothing_is_detected(self, tmp_path):
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]],
        }, wording_column="TRANSLATION KLINGON")
        last_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1", "excel_path": excel_file,
            "results": [{"Status": "OK"}], "report_path": str(tmp_path / "report_2.xlsx"),
        }

        home.ensure_archived(last_run)

        assert self.do_archive_calls == []
        assert len(self.prompt_calls) == 1

    def test_is_idempotent_for_an_already_archived_run(self, tmp_path):
        excel_file = _make_combined_excel(tmp_path)
        last_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1", "excel_path": excel_file,
            "results": [{"Status": "OK"}], "report_path": str(tmp_path / "report_3.xlsx"),
        }

        home.ensure_archived(last_run)
        home.ensure_archived(last_run)

        assert len(self.do_archive_calls) == 1


class TestHomeAppSmoke:
    """Verifie que l'app demarre sans exception et que les widgets cles
    reagissent - ne remplace pas un test manuel dans un navigateur (upload
    de fichiers non simulable par AppTest), mais aurait attrape toute
    erreur d'import/de syntaxe ou de logique de rendu au niveau module."""

    def test_app_starts_without_exception(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        at = AppTest.from_file(HOME_PATH)
        at.run()
        assert not at.exception

    def test_mode_radio_offers_the_three_expected_options(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        at = AppTest.from_file(HOME_PATH)
        at.run()
        assert at.radio[0].options == ["Wording", "URL", "Wording + URL"]

    def test_wording_mode_shows_batch_and_generic_checkboxes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        at = AppTest.from_file(HOME_PATH)
        at.run()
        assert not at.exception
        assert len(at.checkbox) == 2

    def test_switching_to_url_mode_hides_the_generic_components_checkbox(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        at = AppTest.from_file(HOME_PATH)
        at.run()
        at.radio[0].set_value("URL").run()
        assert not at.exception
        assert len(at.checkbox) == 1

    def test_comparer_button_without_any_upload_shows_an_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        at = AppTest.from_file(HOME_PATH)
        at.run()
        at.button[0].click().run()
        assert not at.exception
        assert any("uploader le HTML et l'Excel" in e.value for e in at.error)
