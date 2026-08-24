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
import io
import os
import shutil
import tempfile
import threading

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from conftest import make_details_report, make_wording_workbook

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

    def test_html_special_characters_in_text_are_escaped(self):
        # Le resultat est injecte comme innerHTML brut par le tableau AgGrid
        # (voir _render_table) : un wording contenant litteralement des
        # caracteres HTML (ex. un email fourni par un tiers) ne doit jamais
        # s'executer comme du HTML/JS - regression test pour la faille XSS
        # corrigee dans highlight_differences.
        excel, html_out = home.highlight_differences("<script>alert(1)</script>", "safe text")
        assert "<script>" not in excel
        assert "&lt;script&gt;" in excel

    def test_html_special_characters_survive_inside_a_diff_span(self):
        excel, html_out = home.highlight_differences("Hello world", "Hello <b>there</b>")
        assert "<b>there</b>" not in html_out
        assert "&lt;b&gt;there&lt;/b&gt;" in html_out
        # Le span de surlignage lui-meme reste un vrai tag HTML.
        assert "<span style='font-weight:bold;color:#d62828'>" in html_out


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

    def test_ok_rows_are_html_escaped_even_without_a_diff(self):
        # Les colonnes Excel/HTML Wording d'une ligne OK passent quand meme
        # par le rendu innerHTML brut du tableau AgGrid (voir _render_table) :
        # doivent etre echappees comme les lignes KO diffees, sinon un texte
        # extrait contenant litteralement des caracteres HTML s'executerait
        # comme du HTML/JS (regression test pour la faille XSS corrigee).
        results = [
            {"Component Name": "Header", "Status": "OK",
             "Excel Wording": "<img src=x onerror=alert(1)>", "HTML Wording": "<img src=x onerror=alert(1)>",
             "_html_order": 0},
        ]
        _, df_display = home._prepare_display_df(results, mode="wording")

        assert "<img" not in df_display.iloc[0]["Excel Wording"]
        assert "&lt;img" in df_display.iloc[0]["Excel Wording"]

    def test_url_mode_ok_rows_are_html_escaped_even_without_a_diff(self):
        # Meme regression test que test_ok_rows_are_html_escaped_even_without_a_diff
        # (branche wording), applique a la branche url - les deux passent
        # desormais par le meme helper _safe_html_cell plutot que par une
        # copie du code d'echappement, ce test verifie qu'aucune des deux
        # branches n'a ete oubliee.
        results = [
            {
                "Component Name": "", "Source Section": "SEQ 1", "Expected Class": "audit-component-a",
                "HTML Class": "audit-component-a", "Excel Wording": "<img src=x onerror=alert(1)>",
                "HTML Wording": "<img src=x onerror=alert(1)>", "Status": "OK", "Error Type": "",
                "Similarity": "100%", "Occurrences": 1, "_html_order": 0,
            },
        ]
        _, df_display = home._prepare_display_df(results, mode="url")

        assert "<img" not in df_display.iloc[0]["URL attendue (Excel)"]
        assert "&lt;img" in df_display.iloc[0]["URL attendue (Excel)"]

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


class TestThreadSafeLineBuffer:
    """_ThreadSafeLineBuffer est la cible de contextlib.redirect_stdout
    pendant l'execution parallele du mode batch (voir _execute_batch/
    _execute_combined_batch) : sans elle, les print() de plusieurs threads
    peuvent entrelacer leurs ecritures (chaque print() fait 2 appels
    .write() distincts) et fusionner deux messages en une seule ligne
    illisible - regression test pour ce bug corrige."""

    def test_single_thread_writes_pass_through_unchanged(self):
        target = io.StringIO()
        buf = home._ThreadSafeLineBuffer(target)

        print("premiere ligne", file=buf)
        print("deuxieme ligne", file=buf)

        assert target.getvalue() == "premiere ligne\ndeuxieme ligne\n"

    def test_concurrent_writes_from_multiple_threads_never_interleave_a_line(self):
        target = io.StringIO()
        buf = home._ThreadSafeLineBuffer(target)
        n_threads = 8
        n_lines = 50
        barrier = threading.Barrier(n_threads)

        def worker(thread_id):
            barrier.wait()  # maximise les chances d'ecritures simultanees
            for i in range(n_lines):
                # Un print() ecrit le texte puis "\n" en 2 appels .write()
                # distincts : c'est exactement entre ces deux appels qu'une
                # ecriture non protegee d'un autre thread pourrait s'inserer.
                print(f"thread-{thread_id}-line-{i}", file=buf)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = target.getvalue().splitlines()
        assert len(lines) == n_threads * n_lines
        # Chaque ligne doit rester exactement celle qu'un seul thread a
        # ecrite - jamais un fragment de deux lignes fusionnees.
        for line in lines:
            assert line.startswith("thread-") and "-line-" in line
            thread_part, line_part = line.split("-line-")
            assert thread_part.startswith("thread-")
            int(thread_part.removeprefix("thread-"))  # leve si corrompu
            int(line_part)  # leve si corrompu


class TestRunExcelDiagnostics:
    """_run_excel_diagnostics calcule les messages du panneau "🔍 Diagnostic
    du fichier Excel" sans appel st.* (voir home.py), pour que l'appelant
    puisse decider d'ouvrir ou non le panneau automatiquement selon la
    presence d'un warning/error - regression test pour ce comportement
    (le panneau etait toujours deplie avant, quel que soit le contenu)."""

    def test_clean_workbook_has_no_warning_or_error(self, tmp_path):
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]]
        })

        messages = home._run_excel_diagnostics(
            excel_file, html_path=None, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=False
        )

        levels = [level for level, _ in messages]
        assert "warning" not in levels and "error" not in levels
        assert "success" in levels

    def test_missing_technical_name_produces_a_warning(self, tmp_path):
        # Colonne TECHNICAL NAME vide (une seule espace : une cellule
        # vraiment vide serait relue comme NaN par pandas, or Technical
        # n'est converti en chaine par excel_loader que si pd.notna() - un
        # NaN brut resterait tel quel et compterait a tort comme "rempli",
        # bool(nan) etant True) sur toutes les lignes -> fill_rate == 0.
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", " "]]
        })

        messages = home._run_excel_diagnostics(
            excel_file, html_path=None, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=False
        )

        levels = [level for level, _ in messages]
        assert "warning" in levels
        warning_text = next(text for level, text in messages if level == "warning")
        assert "TECHNICAL NAME" in warning_text

    def test_unreadable_excel_produces_a_single_error(self, tmp_path):
        missing_path = str(tmp_path / "does_not_exist.xlsx")

        messages = home._run_excel_diagnostics(
            missing_path, html_path=None, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=False
        )

        assert messages == [("error", messages[0][1])]
        assert "Impossible de lire le fichier Excel" in messages[0][1]

    def test_expander_expands_only_when_a_warning_or_error_is_present(self, tmp_path):
        # Verifie la decision d'ouverture automatique elle-meme (voir la
        # section DIAGNOSTIC EXCEL de home.py), pas seulement les messages.
        clean_excel = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]]
        })
        dirty_excel = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", " "]]
        }, filename="dirty.xlsx")

        def has_issues(excel_file):
            messages = home._run_excel_diagnostics(
                excel_file, html_path=None, test_wording=True, test_url=False,
                wording_keyword="SEQ 1", url_keyword="", include_generic=False
            )
            return any(level in ("warning", "error") for level, _ in messages)

        assert has_issues(clean_excel) is False
        assert has_issues(dirty_excel) is True


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


class _FakeUploadedFile:
    """Simule l'objet retourne par st.file_uploader (attributs .name et
    .getvalue() seulement, les deux seuls utilises par home.py) - partagee
    entre TestWriteBatchHtml et les tests des fonctions _execute_* (mode
    batch), aucun upload de fichier reel n'etant simulable via AppTest."""
    def __init__(self, content: bytes, name: str = "email.html"):
        self._content = content
        self.name = name

    def getvalue(self):
        return self._content


class TestWriteBatchHtml:
    def test_writes_the_uploaded_bytes_under_a_session_scoped_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = home._write_batch_html(_FakeUploadedFile(b"<html>hello</html>"), "sess123")

        assert path.startswith("temp/sess123_") and path.endswith(".html")
        assert open(path, "rb").read() == b"<html>hello</html>"

    def test_two_calls_for_the_same_session_get_distinct_paths(self, tmp_path, monkeypatch):
        # Mode batch : plusieurs fichiers HTML pour une meme session ne
        # doivent jamais s'ecraser les uns les autres pendant la boucle.
        monkeypatch.chdir(tmp_path)
        path1 = home._write_batch_html(_FakeUploadedFile(b"A"), "sess123")
        path2 = home._write_batch_html(_FakeUploadedFile(b"B"), "sess123")
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

        results, error_message = home._run_batch_qa(html_file, excel_file, "SEQ 1", "wording", False, label="SEQ 1")

        assert results is not None
        assert results[0]["Status"] == "OK"
        assert error_message is None

    def test_returns_an_error_message_instead_of_raising_on_unreadable_html(self, tmp_path):
        # Ne doit plus afficher l'erreur elle-meme (st.error) : cette fonction
        # est appelee depuis un thread worker par _run_jobs_in_parallel, et
        # Streamlit n'autorise pas les appels a son API hors du thread du
        # script - c'est a l'appelant (thread principal) d'afficher
        # error_message une fois tous les jobs du batch termines.
        excel_file, html_file = self._make_excel_and_html(tmp_path)
        # Ecrase le HTML valide par une sequence non-UTF8 (voir QA_RUN_READ_ERRORS).
        with open(html_file, "wb") as f:
            f.write("café".encode("latin-1"))

        results, error_message = home._run_batch_qa(html_file, excel_file, "SEQ 1", "wording", False, label="SEQ 1")

        assert results is None
        assert "SEQ 1" in error_message


class TestRunJobsInParallel:
    def test_empty_job_list_returns_empty_list(self):
        assert home._run_jobs_in_parallel([]) == []

    def test_runs_every_job_and_preserves_order(self):
        results = home._run_jobs_in_parallel([lambda: 1, lambda: 2, lambda: 3])
        assert results == [1, 2, 3]

    def test_an_unexpected_exception_from_a_job_propagates(self):
        # _run_batch_qa (le seul appelant reel) n'est jamais cense lever -
        # il attrape lui-meme QA_RUN_READ_ERRORS et retourne un message
        # d'erreur (voir TestRunBatchQa) - une exception qui remonte jusqu'ici
        # est donc un bug de programmation, pas une erreur utilisateur a
        # avaler silencieusement.
        def _boom():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            home._run_jobs_in_parallel([lambda: 1, _boom, lambda: 3])


@pytest.fixture
def isolated_report_dir(tmp_path, monkeypatch):
    """
    _execute_batch/_execute_combined_batch/_execute_single_or_combined_test
    ecrivent un vrai rapport Excel via qa.report (save_report/
    save_consolidated_report), qui appelle lui-meme record_run() en mode
    "brut" (voir qa.report, qa.history.record_run) - sans isolation, ces
    tests ecriraient dans le vrai output/ et le vrai output/history.db du
    projet (meme risque documente par TestDoArchive.isolated_archive_dirs
    plus bas, pour le chemin d'archivage classe plutot que ce chemin "brut").
    monkeypatch.chdir isole aussi les fichiers temporaires de
    _write_batch_html (mode batch), qui utilise toujours un chemin relatif
    "temp/".
    """
    import qa.report as report_module
    from qa.history import record_run as real_record_run

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(report_module, "OUTPUT_DIR", str(tmp_path / "output"))
    history_path = str(tmp_path / "history.db")
    monkeypatch.setattr(
        report_module, "record_run",
        lambda *a, path=history_path, **k: real_record_run(*a, path=path, **k)
    )


class TestExecuteSingleOrCombinedTest:
    """_execute_single_or_combined_test orchestre run_qa (mode simple, ou
    Wording+URL sur le meme HTML) et construit le dict last_run - jusqu'ici
    la seule facon de l'exercer etait un upload de fichier reel dans le
    navigateur (non simulable par AppTest, voir TestHomeAppSmoke), ce qui
    laissait tout ce chemin hors de la couverture de tests."""

    def _make_wording_files(self, tmp_path, wording="Hallo", html_text="Hallo", seq="SEQ 1"):
        excel_file = make_wording_workbook(tmp_path, {
            seq: [["Header", "Title", wording, "audit-component-header"]],
        })
        html_file = tmp_path / "email.html"
        html_file.write_text(
            f'<div class="text-contribution"><p class="audit-component-header">{html_text}</p></div>',
            encoding="utf-8",
        )
        return str(html_file), excel_file

    def _make_combined_files(self, tmp_path):
        """Classeur avec un onglet wording ET un onglet UTM (une seule
        section "SEQ 1", label "audit-component-cta" - meme convention que
        tests/test_engine.py._make_utm_workbook) + un HTML qui satisfait les
        deux a la fois, pour exercer la branche test_wording ET test_url."""
        path = tmp_path / "combined.xlsx"
        wording_rows = [
            ["", "", "", ""],
            ["COMPONENT NAME", "ELEMENT", "TRANSLATION", "TECHNICAL NAME"],
            ["Header", "Title", "Hallo Welt", "audit-component-header"],
        ]
        utm_rows = [
            [None, "!! CHECK !!", None, None, None, None, None, None],
            [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
            [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
            [None, "SEQ 1", None, None, None, None, None, None],
            ["audit-component-cta", "https://example.com/", "trigger", "email", "revamp_inac", "seq1", "",
             "https://example.com/shop"],
        ]
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(wording_rows).to_excel(writer, sheet_name="SEQ 1", index=False, header=False)
            pd.DataFrame(utm_rows).to_excel(writer, sheet_name="UTM TRIGGER", index=False, header=False)

        html_file = tmp_path / "email.html"
        html_file.write_text(
            '<div class="text-contribution"><p class="audit-component-header">Hallo Welt</p></div>'
            '<div class="audit-component-cta"><a href="https://example.com/shop">Shop</a></div>',
            encoding="utf-8",
        )
        return str(html_file), str(path)

    def test_wording_only_returns_a_single_last_run(self, tmp_path, isolated_report_dir):
        html_file, excel_file = self._make_wording_files(tmp_path)

        last_run, error = home._execute_single_or_combined_test(
            html_file, excel_file, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=False,
        )

        assert error is None
        assert last_run["kind"] == "single"
        assert last_run["mode"] == "wording"
        assert last_run["sequence"] == "SEQ 1"
        assert last_run["results"][0]["Status"] == "OK"
        assert last_run["download_filename"].startswith("qa_report_SEQ_1_")

    def test_unreadable_html_returns_no_last_run_and_an_error_message(self, tmp_path, isolated_report_dir):
        html_file, excel_file = self._make_wording_files(tmp_path)
        with open(html_file, "wb") as f:
            f.write("café".encode("latin-1"))

        last_run, error = home._execute_single_or_combined_test(
            html_file, excel_file, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=False,
        )

        assert last_run is None
        assert error is not None

    def test_wording_and_url_combined_returns_a_combined_test_last_run(self, tmp_path, isolated_report_dir):
        html_file, excel_file = self._make_combined_files(tmp_path)

        last_run, error = home._execute_single_or_combined_test(
            html_file, excel_file, test_wording=True, test_url=True,
            wording_keyword="SEQ 1", url_keyword="SEQ 1", include_generic=False,
        )

        assert error is None
        assert last_run["kind"] == "combined_test"
        assert last_run["wording_results"][0]["Status"] == "OK"
        assert last_run["url_results"][0]["Status"] == "OK"
        assert "--- Wording ---" in last_run["debug_logs"] and "--- URL ---" in last_run["debug_logs"]


class TestExecuteBatch:
    """_execute_batch orchestre le mode multi-sequences a un seul type de
    test (Wording OU URL) : ecriture des fichiers temporaires, jobs
    paralleles (voir TestRunJobsInParallel), rapport consolide."""

    def _make_files(self, tmp_path, sequences=("SEQ 1", "SEQ 2")):
        excel_file = make_wording_workbook(tmp_path, {
            seq: [["Header", "Title", "Hallo", "audit-component-header"]] for seq in sequences
        })
        assignments = []
        for i, seq in enumerate(sequences):
            content = '<div class="text-contribution"><p class="audit-component-header">Hallo</p></div>'
            assignments.append((_FakeUploadedFile(content.encode("utf-8"), name=f"email_{i}.html"), seq))
        return excel_file, assignments

    def test_all_sequences_succeed(self, tmp_path, isolated_report_dir):
        excel_file, assignments = self._make_files(tmp_path)

        last_run, errors, debug_logs = home._execute_batch(
            excel_file, assignments, "wording", False, "sess1"
        )

        assert errors == []
        assert last_run["kind"] == "batch"
        assert set(last_run["sequences"].keys()) == {"SEQ 1", "SEQ 2"}
        assert all(r["Status"] == "OK" for rows in last_run["sequences"].values() for r in rows)
        assert len(last_run["recap_rows"]) == 2
        # _execute_batch capture desormais ses logs via run_with_debug_capture
        # (voir _build_and_run_batch_jobs) plutot que sa propre redirection -
        # verifie que l'entete par sequence (print() dans la boucle de
        # construction des jobs) est toujours bien remontee dans debug_logs.
        assert "SEQ 1" in last_run["debug_logs"] and "SEQ 2" in last_run["debug_logs"]

    def test_one_failing_file_does_not_block_the_others(self, tmp_path, isolated_report_dir):
        excel_file, assignments = self._make_files(tmp_path)
        # Le fichier de "SEQ 2" est illisible (non-UTF8) - "SEQ 1" doit quand
        # meme aboutir, avec un message d'erreur pour l'autre.
        bad_file = _FakeUploadedFile("café".encode("latin-1"), name="email_1.html")
        assignments = [assignments[0], (bad_file, "SEQ 2")]

        last_run, errors, debug_logs = home._execute_batch(
            excel_file, assignments, "wording", False, "sess1"
        )

        assert len(errors) == 1
        assert last_run["kind"] == "batch"
        assert set(last_run["sequences"].keys()) == {"SEQ 1"}

    def test_every_file_failing_returns_no_last_run(self, tmp_path, isolated_report_dir):
        excel_file, assignments = self._make_files(tmp_path)
        bad_assignments = [
            (_FakeUploadedFile("café".encode("latin-1"), name=f.name), seq) for f, seq in assignments
        ]

        last_run, errors, debug_logs = home._execute_batch(
            excel_file, bad_assignments, "wording", False, "sess1"
        )

        assert last_run is None
        assert len(errors) == 2


class TestExecuteCombinedBatch:
    """_execute_combined_batch orchestre le mode multi-sequences "Wording +
    URL" : 2 jobs (wording + url) par fichier, voir _execute_batch pour le
    cas a un seul type de test."""

    def _make_files(self, tmp_path):
        path = tmp_path / "combined.xlsx"
        wording_rows = [
            ["", "", "", ""],
            ["COMPONENT NAME", "ELEMENT", "TRANSLATION", "TECHNICAL NAME"],
            ["Header", "Title", "Hallo Welt", "audit-component-header"],
        ]
        utm_rows = [
            [None, "!! CHECK !!", None, None, None, None, None, None],
            [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
            [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
            [None, "SEQ 1", None, None, None, None, None, None],
            ["audit-component-cta", "https://example.com/", "trigger", "email", "revamp_inac", "seq1", "",
             "https://example.com/shop"],
        ]
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(wording_rows).to_excel(writer, sheet_name="SEQ 1", index=False, header=False)
            pd.DataFrame(utm_rows).to_excel(writer, sheet_name="UTM TRIGGER", index=False, header=False)

        html_content = (
            '<div class="text-contribution"><p class="audit-component-header">Hallo Welt</p></div>'
            '<div class="audit-component-cta"><a href="https://example.com/shop">Shop</a></div>'
        )
        uploaded_file = _FakeUploadedFile(html_content.encode("utf-8"), name="email.html")
        return str(path), [(uploaded_file, "SEQ 1", "SEQ 1")]

    def test_wording_and_url_both_succeed(self, tmp_path, isolated_report_dir):
        excel_file, assignments = self._make_files(tmp_path)

        last_run, errors, debug_logs = home._execute_combined_batch(
            excel_file, assignments, False, "sess1"
        )

        assert errors == []
        assert last_run["kind"] == "combined_batch"
        assert len(last_run["pairs"]) == 1
        assert last_run["pairs"][0]["wording_rows"][0]["Status"] == "OK"
        assert last_run["pairs"][0]["url_rows"][0]["Status"] == "OK"
        assert last_run["recap_rows"][0]["Score Wording"] == 100.0
        assert last_run["recap_rows"][0]["Score URL"] == 100.0
        # Meme verification que TestExecuteBatch : la capture de debug passe
        # desormais par run_with_debug_capture (voir
        # _build_and_run_combined_batch_jobs) plutot qu'une redirection
        # dupliquee dans cette fonction.
        assert "SEQ 1" in last_run["debug_logs"]


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

    def test_archives_a_single_run_and_records_its_score_in_history(self, tmp_path):
        from qa.history import load_sequence_history

        report_path = tmp_path / "report.xlsx"
        make_details_report(str(report_path), [{"Status": "OK"}, {"Status": "KO"}])
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
        make_details_report(str(wording_report), [{"Status": "OK"}])
        url_report = tmp_path / "url.xlsx"
        make_details_report(str(url_report), [{"Status": "OK"}])

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
        make_details_report(str(report_path), rows)
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
        make_details_report(str(report_path), [{"Status": "OK"}, {"Status": "KO"}])
        last_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1 NON LOYAL",
            "results": [{"Status": "OK"}, {"Status": "KO"}], "report_path": str(report_path),
        }

        regressions = home._do_archive(last_run, "Germany", "Revamp Inac")

        assert regressions == []

    def test_returns_a_regression_when_score_drops_significantly(self, tmp_path):
        first_report = tmp_path / "first.xlsx"
        make_details_report(str(first_report), [{"Status": "OK"}] * 10)
        first_run = {
            "kind": "single", "mode": "wording", "sequence": "SEQ 1 NON LOYAL",
            "results": [{"Status": "OK"}] * 10, "report_path": str(first_report),
        }
        home._do_archive(first_run, "Germany", "Revamp Inac")

        second_report = tmp_path / "second.xlsx"
        second_rows = [{"Status": "OK"}] * 2 + [{"Status": "KO"}] * 8
        make_details_report(str(second_report), second_rows)
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


class TestSendTeamsNotificationSafely:
    """_send_teams_notification_safely est le point d'entree du thread
    daemon fire-and-forget lance par _do_archive (voir plus haut) :
    send_teams_notification() n'attrape que requests.RequestException, donc
    toute autre exception (bug de construction du message, regression
    future...) doit etre avalee ICI plutot que de disparaitre silencieuse-
    ment dans le thread - regression test pour ce point corrige."""

    def test_swallows_any_exception_raised_by_the_notification_call(self, monkeypatch, capsys):
        def _boom(subject, message):
            raise TypeError("payload inattendu")

        monkeypatch.setattr(home, "send_teams_notification", _boom)

        home._send_teams_notification_safely("QA Germany / Revamp", "resume")  # ne doit pas lever

        captured = capsys.readouterr()
        assert "Notification Teams" in captured.out
        assert "QA Germany / Revamp" in captured.out

    def test_still_calls_the_real_notification_function_on_success(self, monkeypatch):
        calls = []
        monkeypatch.setattr(home, "send_teams_notification", lambda subject, message: calls.append((subject, message)))

        home._send_teams_notification_safely("QA Germany / Revamp", "resume")

        assert calls == [("QA Germany / Revamp", "resume")]


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

    def test_batch_mode_and_generic_checkboxes_live_in_a_collapsed_expander(self, tmp_path, monkeypatch):
        # Regression test pour la reduction de la surcharge de widgets
        # visibles par defaut (voir home.py) : ces deux reglages ne
        # concernent qu'une minorite de campagnes, ils ne doivent plus etre
        # deplies par defaut sur la page principale.
        monkeypatch.chdir(tmp_path)
        at = AppTest.from_file(HOME_PATH)
        at.run()
        assert not at.exception
        advanced = next(e for e in at.expander if e.label == "⚙️ Options avancées")
        assert advanced.proto.expanded is False
        assert len(advanced.checkbox) == 2
