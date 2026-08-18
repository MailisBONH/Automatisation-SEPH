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

from streamlit.testing.v1 import AppTest

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
