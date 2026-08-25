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

from conftest import fake_incrementing_datetime, make_details_report, make_wording_workbook

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

    def test_generic_sheet_detected_without_the_checkbox_produces_a_warning(self, tmp_path):
        # Un onglet generique present mais non fusionne (case a cocher non
        # cochee) laisserait les composants communs a toutes les sequences
        # (footer, mentions legales...) manquer du referentiel et ressortir
        # a tort en erreur - le diagnostic doit le signaler AVANT de
        # comparer, pas apres coup dans les resultats.
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]],
            "GENERIC": [["Footer", "Legal", "Mentions légales", "audit-component-footer"]],
        })

        messages = home._run_excel_diagnostics(
            excel_file, html_path=None, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=False
        )

        warning_text = next(
            (text for level, text in messages if level == "warning" and "GENERIC" in text), None
        )
        assert warning_text is not None
        assert "case correspondante n'est pas cochée" in warning_text

    def test_generic_sheet_detected_with_the_checkbox_checked_produces_no_such_warning(self, tmp_path):
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]],
            "GENERIC": [["Footer", "Legal", "Mentions légales", "audit-component-footer"]],
        })

        messages = home._run_excel_diagnostics(
            excel_file, html_path=None, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=True
        )

        assert not any("case correspondante n'est pas cochée" in text for _, text in messages)

    def test_no_generic_sheet_produces_no_such_warning(self, tmp_path):
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]],
        })

        messages = home._run_excel_diagnostics(
            excel_file, html_path=None, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=False
        )

        assert not any("case correspondante n'est pas cochée" in text for _, text in messages)


class TestDetectGenericSheetName:
    def test_finds_a_sheet_containing_the_keyword_case_insensitively(self):
        assert home._detect_generic_sheet_name(["SEQ 1", "Generic Components", "UTM TRIGGER"]) == "Generic Components"

    def test_returns_none_when_no_sheet_matches(self):
        assert home._detect_generic_sheet_name(["SEQ 1", "UTM TRIGGER"]) is None


class TestRunExcelDiagnosticsBatchMode:
    """Version allegee du diagnostic pour le mode multi-sequences (voir
    _run_excel_diagnostics_batch_mode) : meme detection d'onglet generique
    non fusionne que le mode simple, mais gatee par test_wording (la case
    a cocher elle-meme n'existe pas en mode url pur)."""

    def test_generic_sheet_detected_without_the_checkbox_produces_a_warning(self, tmp_path):
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]],
            "GENERIC": [["Footer", "Legal", "Mentions légales", "audit-component-footer"]],
        })

        messages = home._run_excel_diagnostics_batch_mode(excel_file, include_generic=False, test_wording=True)

        assert any("GENERIC" in text and "case correspondante" in text for _, text in messages)

    def test_url_only_mode_never_shows_the_generic_checkbox_warning(self, tmp_path):
        # La case "composants generiques" n'existe meme pas en mode URL pur
        # (voir home.py) - le diagnostic ne doit pas suggerer de cocher une
        # case que l'utilisateur ne peut pas voir.
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]],
            "GENERIC": [["Footer", "Legal", "Mentions légales", "audit-component-footer"]],
        })

        messages = home._run_excel_diagnostics_batch_mode(excel_file, include_generic=False, test_wording=False)

        assert not any("case correspondante" in text for _, text in messages)


class TestRunExcelDiagnosticsCached:
    """_run_excel_diagnostics_cached (utilisee par la section DIAGNOSTIC
    EXCEL de home.py) met en cache _run_excel_diagnostics par (chemin,
    mtime, taille) pour eviter de reparser le classeur/HTML a chaque rerun
    Streamlit (le script entier est reexecute a chaque interaction avec un
    widget) - regression tests pour (a) le cache-hit reel et (b)
    l'invalidation quand un fichier different est reuploade sous le meme
    chemin (chemin fixe par session, contenu remplace a chaque upload)."""

    def test_repeated_calls_with_the_same_file_hit_the_cache(self, tmp_path, monkeypatch):
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]]
        })
        calls = []
        original = home._run_excel_diagnostics

        def _counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(home, "_run_excel_diagnostics", _counting)

        for _ in range(3):
            home._run_excel_diagnostics_cached(
                excel_file, html_path=None, test_wording=True, test_url=False,
                wording_keyword="SEQ 1", url_keyword="", include_generic=False
            )

        assert len(calls) == 1

    def test_reuploading_a_different_file_at_the_same_path_invalidates_the_cache(self, tmp_path):
        path = tmp_path / "input.xlsx"

        clean = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]]
        }, filename="clean.xlsx")
        shutil.copy2(clean, path)

        first = home._run_excel_diagnostics_cached(
            str(path), html_path=None, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=False
        )
        assert not any(level in ("warning", "error") for level, _ in first)

        dirty = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", " "]]
        }, filename="dirty.xlsx")
        shutil.copy2(dirty, path)
        # Force un mtime distinct : deux ecritures rapprochees dans un test
        # pourraient sinon retomber sur la meme valeur arrondie selon la
        # resolution du systeme de fichiers, et masquer une regression ici.
        current = os.stat(path).st_mtime
        os.utime(path, (current + 5, current + 5))

        second = home._run_excel_diagnostics_cached(
            str(path), html_path=None, test_wording=True, test_url=False,
            wording_keyword="SEQ 1", url_keyword="", include_generic=False
        )
        assert any(level == "warning" for level, _ in second)


def _single_panel(mode, sequence, rows, report_path="r.xlsx"):
    """Panel mono-sequence (consolidated=False) - forme produite par
    _execute_single_or_combined_test pour un run simple, voir le schema
    commun documente en tete de home.py."""
    return {
        "mode": mode, "consolidated": False,
        "results_by_sequence": {sequence: rows},
        "report_path": report_path, "download_filename": "r.xlsx",
        "title": "Comparaison terminée", "tab_label": None,
    }


def _consolidated_panel(mode, results_by_sequence, report_path=None):
    """Panel multi-sequence (consolidated=True) - forme produite par
    _execute_batch/_execute_combined_batch, voir le schema commun."""
    return {
        "mode": mode, "consolidated": True,
        "results_by_sequence": results_by_sequence,
        "report_path": report_path, "download_filename": "r.xlsx",
        "title": "Comparaison terminée", "tab_label": mode,
    }


class TestCurrentInputsSignature:
    """_uploaded_file_identity / _current_inputs_signature determinent si le
    dernier resultat affiche (last_run) doit etre efface - voir home.py,
    bloc "EFFACE LE DERNIER RESULTAT SI LES ENTREES ONT CHANGE"."""

    def test_no_file_uploaded_gives_none_identity(self):
        assert home._uploaded_file_identity(None) is None

    def test_same_name_and_content_give_the_same_identity(self):
        a = home._uploaded_file_identity(_FakeUploadedFile(b"hello", name="a.html"))
        b = home._uploaded_file_identity(_FakeUploadedFile(b"hello", name="a.html"))
        assert a == b

    def test_different_content_under_the_same_name_gives_a_different_identity(self):
        # Re-upload d'un fichier different sous le meme nom : ne doit pas
        # etre confondu avec le precedent.
        a = home._uploaded_file_identity(_FakeUploadedFile(b"hello", name="a.html"))
        b = home._uploaded_file_identity(_FakeUploadedFile(b"goodbye", name="a.html"))
        assert a != b

    def test_identical_inputs_give_the_same_signature(self):
        excel = _FakeUploadedFile(b"excel-bytes", name="ref.xlsx")
        html_file = _FakeUploadedFile(b"html-bytes", name="mail.html")
        sig1 = home._current_inputs_signature("Wording", False, excel, html_file, None)
        sig2 = home._current_inputs_signature("Wording", False, excel, html_file, None)
        assert sig1 == sig2

    def test_replacing_the_html_file_changes_the_signature(self):
        excel = _FakeUploadedFile(b"excel-bytes", name="ref.xlsx")
        sig_before = home._current_inputs_signature(
            "Wording", False, excel, _FakeUploadedFile(b"v1", name="mail.html"), None
        )
        sig_after = home._current_inputs_signature(
            "Wording", False, excel, _FakeUploadedFile(b"v2", name="mail.html"), None
        )
        assert sig_before != sig_after

    def test_changing_mode_changes_the_signature_even_with_the_same_files(self):
        excel = _FakeUploadedFile(b"excel-bytes", name="ref.xlsx")
        html_file = _FakeUploadedFile(b"html-bytes", name="mail.html")
        sig_wording = home._current_inputs_signature("Wording", False, excel, html_file, None)
        sig_url = home._current_inputs_signature("URL", False, excel, html_file, None)
        assert sig_wording != sig_url

    def test_toggling_batch_mode_changes_the_signature_even_with_the_same_mode(self):
        excel = _FakeUploadedFile(b"excel-bytes", name="ref.xlsx")
        sig_simple = home._current_inputs_signature("Wording", False, excel, None, None)
        sig_batch = home._current_inputs_signature("Wording", True, excel, None, [])
        assert sig_simple != sig_batch

    def test_changing_the_assignments_signature_changes_the_overall_signature(self):
        # Regression : re-associer un fichier a une autre sequence (mode
        # multi-sequences) sans changer ni fichier ni mode doit quand meme
        # changer la signature globale - sans quoi last_run resterait affiche
        # a tort apres une re-association (voir home.py, le calcul de
        # assignments_signature juste avant l'appel a cette fonction).
        excel = _FakeUploadedFile(b"excel-bytes", name="ref.xlsx")
        html_files = [_FakeUploadedFile(b"html-bytes", name="mail.html")]
        sig_before = home._current_inputs_signature(
            "Wording", True, excel, None, html_files, assignments_signature=(("mail.html", "SEQ 1"),)
        )
        sig_after = home._current_inputs_signature(
            "Wording", True, excel, None, html_files, assignments_signature=(("mail.html", "SEQ 2"),)
        )
        assert sig_before != sig_after

    def test_default_assignments_signature_is_backward_compatible(self):
        # assignments_signature est optionnel (valeur par defaut ()) pour que
        # les appels existants (mode simple, avant l'ajout de ce parametre)
        # restent valides sans le fournir explicitement.
        excel = _FakeUploadedFile(b"excel-bytes", name="ref.xlsx")
        sig_without_arg = home._current_inputs_signature("Wording", False, excel, None, None)
        sig_with_empty_tuple = home._current_inputs_signature("Wording", False, excel, None, None, ())
        assert sig_without_arg == sig_with_empty_tuple


class TestSequenceWidgetKey:
    """_sequence_widget_key (voir home.py) - regression test pour le crash
    "StreamlitDuplicateElementKey" quand deux fichiers HTML uploades en mode
    batch partagent le meme nom (courant pour un export "view-source" AEM
    jamais renomme) : la cle du selectbox d'association doit rester unique
    par (index, nom de fichier), pas seulement par nom."""

    def test_same_filename_at_different_indices_gives_different_keys(self):
        key_0 = home._sequence_widget_key("seq_choice_", 0, "email.html")
        key_1 = home._sequence_widget_key("seq_choice_", 1, "email.html")
        assert key_0 != key_1

    def test_different_prefixes_give_different_keys_for_the_same_file(self):
        # wseq_/useq_ (mode "Wording + URL" batch) ne doivent jamais collisionner
        # entre eux pour le meme fichier au meme index.
        assert home._sequence_widget_key("wseq_", 0, "email.html") != home._sequence_widget_key("useq_", 0, "email.html")


class TestBestSequenceGuess:
    """_best_sequence_guess (coeur partage par _guess_sequence_choice -
    suggestion affichee, seuil bas - et _apply_high_confidence_sequence_guesses
    - pre-association en masse, seuil haut, voir le bouton "Appliquer les
    suggestions") - jusqu'ici jamais teste directement, seulement via l'UI."""

    def test_sequence_name_contained_in_filename_is_a_high_confidence_match(self):
        option, score = home._best_sequence_guess("SEQ1_Germany_email.html", ["SEQ 1", "SEQ 2"])
        assert option == "SEQ 1"
        assert score >= home._SEQUENCE_GUESS_HIGH_CONFIDENCE_SCORE

    def test_unrelated_filename_scores_low(self):
        option, score = home._best_sequence_guess("random_export.html", ["SEQ 1", "SEQ 2"])
        assert score < home._SEQUENCE_GUESS_MIN_SCORE

    def test_empty_filename_returns_none_and_zero(self):
        option, score = home._best_sequence_guess("", ["SEQ 1"])
        assert option is None
        assert score == 0.0

    def test_guess_sequence_choice_uses_the_low_threshold(self):
        # SEQ 1 non contenu tel quel dans le nom de fichier ("seq_1", pas
        # "seq1") : score en dessous du seuil haute confiance mais au-dessus
        # du seuil de simple suggestion.
        assert home._guess_sequence_choice("seq_1_export.html", ["SEQ 1"]) == "SEQ 1"


class TestApplyHighConfidenceSequenceGuesses:
    """Le bouton "🪄 Appliquer les suggestions à haute confiance" (mode
    batch) pre-remplit st.session_state AVANT que les selectbox ne soient
    instancies (voir home.py) - ce test verifie directement cette
    pre-association, sans passer par un upload de fichier (non simulable,
    voir _FakeUploadedFile)."""

    def test_prefills_session_state_only_for_high_confidence_matches(self, monkeypatch):
        files = [
            _FakeUploadedFile(b"", name="SEQ1_export.html"),  # contient "SEQ 1" -> haute confiance
            _FakeUploadedFile(b"", name="random_file.html"),  # aucun rapport -> pas de pre-remplissage
        ]
        monkeypatch.setattr(home.st, "session_state", {})

        home._apply_high_confidence_sequence_guesses(files, ["SEQ 1", "SEQ 2"], "seq_choice_")

        # Cle prefixee par l'index (voir _sequence_widget_key) : necessaire
        # pour que deux fichiers de meme nom restent des widgets distincts
        # (voir TestSequenceWidgetKey) - ce test verifie juste que CETTE
        # fonction produit bien la cle attendue pour le fichier a l'index 0.
        assert home.st.session_state.get(home._sequence_widget_key("seq_choice_", 0, "SEQ1_export.html")) == "SEQ 1"
        assert home._sequence_widget_key("seq_choice_", 1, "random_file.html") not in home.st.session_state


class TestRunItems:
    def test_single_panel_returns_one_item(self):
        last_run = {"panels": [_single_panel("wording", "SEQ 1", [{"Status": "OK"}], report_path="r.xlsx")]}
        items = home._run_items(last_run)
        assert items == [{"mode": "wording", "sequence": "SEQ 1", "rows": [{"Status": "OK"}], "report_path": "r.xlsx"}]

    def test_consolidated_panel_returns_one_item_per_sequence_without_report_path(self):
        last_run = {"panels": [_consolidated_panel(
            "wording", {"SEQ 1": [{"Status": "OK"}], "SEQ 2": [{"Status": "KO"}]}, report_path="multi.xlsx"
        )]}
        items = home._run_items(last_run)
        assert len(items) == 2
        assert all(item["report_path"] is None for item in items)
        assert {item["sequence"] for item in items} == {"SEQ 1", "SEQ 2"}

    def test_two_single_panels_return_one_wording_item_and_one_url_item(self):
        last_run = {"panels": [
            _single_panel("wording", "SEQ 1", [{"Status": "OK"}], report_path="w.xlsx"),
            _single_panel("url", "SEQ 1 FID", [{"Status": "KO"}], report_path="u.xlsx"),
        ]}
        items = home._run_items(last_run)
        assert {item["mode"] for item in items} == {"wording", "url"}
        assert len(items) == 2

    def test_consolidated_panels_never_report_a_per_sequence_path_even_with_a_single_sequence(self):
        # Meme avec une seule sequence dans le panel, un panel consolide
        # (rapport multi-sequences) ne doit jamais reporter son report_path
        # tel quel a l'archivage - le rapport consolide ne se decoupe pas
        # par sequence, un rapport individuel doit etre regenere.
        last_run = {"panels": [_consolidated_panel("wording", {"SEQ 1": [{"Status": "OK"}]}, report_path="multi.xlsx")]}
        items = home._run_items(last_run)
        assert items == [{"mode": "wording", "sequence": "SEQ 1", "rows": [{"Status": "OK"}], "report_path": None}]


class TestArchiveKey:
    def test_single_panel_uses_its_report_path(self):
        last_run = {"panels": [_single_panel("wording", "SEQ 1", [{"Status": "OK"}], report_path="r.xlsx")]}
        assert home._archive_key(last_run) == "archived::r.xlsx"

    def test_two_panels_use_both_report_paths(self):
        last_run = {"panels": [
            _single_panel("wording", "SEQ 1", [{"Status": "OK"}], report_path="w.xlsx"),
            _single_panel("url", "SEQ 1", [{"Status": "OK"}], report_path="u.xlsx"),
        ]}
        assert home._archive_key(last_run) == "archived::w.xlsx::u.xlsx"

    def test_same_run_always_produces_the_same_key(self):
        last_run = {"panels": [_single_panel("wording", "SEQ 1", [{"Status": "OK"}], report_path="r.xlsx")]}
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
        assert len(last_run["panels"]) == 1
        panel = last_run["panels"][0]
        assert panel["mode"] == "wording"
        assert panel["consolidated"] is False
        assert list(panel["results_by_sequence"].keys()) == ["SEQ 1"]
        assert panel["results_by_sequence"]["SEQ 1"][0]["Status"] == "OK"
        assert panel["download_filename"].startswith("qa_report_SEQ_1_")

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
        assert len(last_run["panels"]) == 2
        wording_panel, url_panel = last_run["panels"]
        assert wording_panel["mode"] == "wording"
        assert next(iter(wording_panel["results_by_sequence"].values()))[0]["Status"] == "OK"
        assert url_panel["mode"] == "url"
        assert next(iter(url_panel["results_by_sequence"].values()))[0]["Status"] == "OK"
        assert last_run["combined_score"] == {"ok": 2, "total": 2}
        assert last_run["top_banner"] == "Comparaison terminée (Wording + URL)"
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
        assert len(last_run["panels"]) == 1
        panel = last_run["panels"][0]
        assert panel["consolidated"] is True
        assert set(panel["results_by_sequence"].keys()) == {"SEQ 1", "SEQ 2"}
        assert all(r["Status"] == "OK" for rows in panel["results_by_sequence"].values() for r in rows)
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
        assert len(last_run["panels"]) == 1
        assert set(last_run["panels"][0]["results_by_sequence"].keys()) == {"SEQ 1"}

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
        assert len(last_run["panels"]) == 2
        wording_panel, url_panel = last_run["panels"]
        assert wording_panel["mode"] == "wording" and wording_panel["consolidated"] is True
        assert wording_panel["results_by_sequence"]["SEQ 1"][0]["Status"] == "OK"
        assert url_panel["mode"] == "url" and url_panel["consolidated"] is True
        assert url_panel["results_by_sequence"]["SEQ 1"][0]["Status"] == "OK"
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
        last_run = {"panels": [
            _single_panel("wording", "SEQ 1 NON LOYAL", [{"Status": "OK"}, {"Status": "KO"}], report_path=str(report_path))
        ]}

        home._do_archive(last_run, "Germany", "Revamp Inac")

        entries = load_sequence_history("Germany", "Revamp Inac", "SEQ 1", path=self.history_path)
        assert any(e["mode"] == "wording" and e["score"] == 50.0 for e in entries)

    def test_retrying_after_a_partial_failure_does_not_duplicate_already_archived_items(self, tmp_path, monkeypatch):
        # Regression : si archive_report/record_run levait une exception au
        # milieu de la boucle (ex. erreur I/O transitoire), archive_key
        # n'etait jamais marque dans session_state (la fonction n'atteint
        # jamais son retour normal) - le rerun Streamlit suivant relancait
        # alors TOUTE la boucle depuis le debut via ensure_archived, ré-
        # archivant/ré-enregistrant les items deja traites avec succes
        # (entrees d'historique dupliquees pour eux).
        from qa.history import load_sequence_history

        report_path_1 = tmp_path / "report1.xlsx"
        make_details_report(str(report_path_1), [{"Status": "OK"}])
        report_path_2 = tmp_path / "report2.xlsx"
        make_details_report(str(report_path_2), [{"Status": "OK"}])

        last_run = {"panels": [
            _single_panel("wording", "SEQ 1", [{"Status": "OK"}], report_path=str(report_path_1)),
            _single_panel("wording", "SEQ 2", [{"Status": "OK"}], report_path=str(report_path_2)),
        ]}

        original_record_run = home.record_run
        call_count = {"n": 0}

        def _record_run_failing_on_second_item(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("erreur I/O transitoire simulee")
            return original_record_run(*args, **kwargs)

        monkeypatch.setattr(home.st, "session_state", {})
        monkeypatch.setattr(home, "record_run", _record_run_failing_on_second_item)

        with pytest.raises(OSError):
            home._do_archive(last_run, "Germany", "Revamp Inac")

        # Reessai (equivalent au rerun Streamlit declenche par ensure_archived
        # puisque archive_key n'a pas ete marque) - cette fois sans echec.
        monkeypatch.setattr(home, "record_run", original_record_run)
        home._do_archive(last_run, "Germany", "Revamp Inac")

        entries_seq1 = load_sequence_history("Germany", "Revamp Inac", "SEQ 1", path=self.history_path)
        entries_seq2 = load_sequence_history("Germany", "Revamp Inac", "SEQ 2", path=self.history_path)
        assert len(entries_seq1) == 1
        assert len(entries_seq2) == 1

    def test_escapes_markdown_special_characters_before_notifying_teams(self, tmp_path, monkeypatch):
        # Un onglet Excel peut porter n'importe quel nom (voir
        # qa.classification.normalize_sequence_name) : une "sequence"
        # contenant une syntaxe de lien markdown ne doit pas produire un
        # lien cliquable trompeur dans le message poste sur le canal Teams
        # configure - regression test pour la faille d'injection corrigee
        # (voir qa.notifications.escape_teams_markdown).
        captured = {}

        def _fake_thread(target, args, daemon):
            target(*args)  # execute tout de suite (pas de vrai thread) pour inspecter le resultat

            class _Dummy:
                def start(self):
                    pass
            return _Dummy()

        monkeypatch.setattr(home.threading, "Thread", _fake_thread)
        monkeypatch.setattr(
            home, "send_teams_notification",
            lambda subject, message: captured.update(subject=subject, message=message)
        )

        report_path = tmp_path / "report.xlsx"
        make_details_report(str(report_path), [{"Status": "OK"}])
        malicious_sequence = "[Cliquez ici](https://phishing.example)"
        last_run = {"panels": [
            _single_panel("wording", malicious_sequence, [{"Status": "OK"}], report_path=str(report_path))
        ]}

        home._do_archive(last_run, "Germany", "Revamp Inac")

        assert "](" not in captured["message"]
        assert r"\[Cliquez ici\]\(https://phishing.example\)" in captured["message"]

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
        last_run = {"panels": [_consolidated_panel("wording", {"SEQ 1 NON LOYAL": rows})]}

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
        last_run = {"panels": [
            _single_panel("wording", "SEQ 1 NON LOYAL", [{"Status": "OK"}], report_path=str(wording_report)),
            _single_panel("url", "SEQ 1 NON FID", [{"Status": "OK"}], report_path=str(url_report)),
        ]}

        home._do_archive(last_run, "Germany", "Revamp Inac")

        entries = load_sequence_history("Germany", "Revamp Inac", "SEQ 1", path=self.history_path)
        assert any(e["mode"] == "combined" and e["score"] == 100.0 for e in entries)

    def test_records_error_type_breakdown(self, tmp_path):
        from qa.history import load_sequence_history

        report_path = tmp_path / "report.xlsx"
        rows = [{"Status": "OK"}, {"Status": "KO", "Error Type": "Minor typo"},
                {"Status": "KO", "Error Type": "Minor typo"}, {"Status": "KO", "Error Type": "Content mismatch"}]
        make_details_report(str(report_path), rows)
        last_run = {"panels": [_single_panel("wording", "SEQ 1 NON LOYAL", rows, report_path=str(report_path))]}

        home._do_archive(last_run, "Germany", "Revamp Inac")

        entries = load_sequence_history("Germany", "Revamp Inac", "SEQ 1", path=self.history_path)
        wording_entry = next(e for e in entries if e["mode"] == "wording")
        assert wording_entry["error_types"] == {"Minor typo": 2, "Content mismatch": 1}

    def test_returns_empty_list_when_no_regression(self, tmp_path):
        report_path = tmp_path / "report.xlsx"
        make_details_report(str(report_path), [{"Status": "OK"}, {"Status": "KO"}])
        last_run = {"panels": [
            _single_panel("wording", "SEQ 1 NON LOYAL", [{"Status": "OK"}, {"Status": "KO"}], report_path=str(report_path))
        ]}

        regressions = home._do_archive(last_run, "Germany", "Revamp Inac")

        assert regressions == []

    def test_returns_a_regression_when_score_drops_significantly(self, tmp_path):
        first_report = tmp_path / "first.xlsx"
        make_details_report(str(first_report), [{"Status": "OK"}] * 10)
        first_run = {"panels": [
            _single_panel("wording", "SEQ 1 NON LOYAL", [{"Status": "OK"}] * 10, report_path=str(first_report))
        ]}
        home._do_archive(first_run, "Germany", "Revamp Inac")

        second_report = tmp_path / "second.xlsx"
        second_rows = [{"Status": "OK"}] * 2 + [{"Status": "KO"}] * 8
        make_details_report(str(second_report), second_rows)
        second_run = {"panels": [
            _single_panel("wording", "SEQ 1 NON LOYAL", second_rows, report_path=str(second_report))
        ]}

        regressions = home._do_archive(second_run, "Germany", "Revamp Inac")

        assert len(regressions) == 1
        assert regressions[0]["previous_score"] == 100.0
        assert regressions[0]["current_score"] == 20.0

    def test_regression_is_enriched_with_the_components_that_actually_regressed(self, tmp_path, monkeypatch):
        # Au-dela du delta de score seul : qa.report_diff sait dire
        # PRECISEMENT quel composant est passe de OK a KO - _do_archive doit
        # l'utiliser pour enrichir la regression (voir home.py, affiche en
        # haut de page plutot qu'un simple pourcentage en bas).
        # fake_incrementing_datetime : sans lui, les 2 rapports archives a
        # la meme seconde porteraient le meme nom de fichier horodate et le
        # second ecraserait le premier (voir qa.archive.archive_report),
        # laissant list_reports n'en trouver qu'un seul - insuffisant pour
        # load_last_two_reports_diff.
        fake_incrementing_datetime(monkeypatch)
        first_rows = [
            {"Component Name": "Header", "Element": "Title", "Status": "OK"},
            {"Component Name": "Footer", "Element": "Legal", "Status": "OK"},
        ]
        first_report = tmp_path / "first.xlsx"
        make_details_report(str(first_report), first_rows)
        first_run = {"panels": [_single_panel("wording", "SEQ 1 NON LOYAL", first_rows, report_path=str(first_report))]}
        home._do_archive(first_run, "Germany", "Revamp Inac")

        second_rows = [
            {"Component Name": "Header", "Element": "Title", "Status": "KO"},
            {"Component Name": "Footer", "Element": "Legal", "Status": "OK"},
        ]
        second_report = tmp_path / "second.xlsx"
        make_details_report(str(second_report), second_rows)
        second_run = {"panels": [_single_panel("wording", "SEQ 1 NON LOYAL", second_rows, report_path=str(second_report))]}

        regressions = home._do_archive(second_run, "Germany", "Revamp Inac")

        assert len(regressions) == 1
        assert regressions[0]["regressed_items"] == ["Header / Title"]

    def test_combined_mode_regression_has_no_regressed_items(self, tmp_path, monkeypatch):
        # qa.report_diff ne supporte pas mode="combined" (2 onglets Wording/
        # URL distincts, pas un seul "Details" comparable) - _do_archive ne
        # doit pas planter pour autant, juste ne pas enrichir cette entree.
        fake_incrementing_datetime(monkeypatch)

        def _archive_once(wording_status, url_status, suffix):
            wr = [{"Component Name": "Header", "Element": "Title", "Status": wording_status}]
            ur = [{"Expected Class": "audit-component-cta", "Status": url_status}]
            wording_report = tmp_path / f"wording_{suffix}.xlsx"
            make_details_report(str(wording_report), wr)
            url_report = tmp_path / f"url_{suffix}.xlsx"
            make_details_report(str(url_report), ur)
            last_run = {"panels": [
                _single_panel("wording", "SEQ 1 NON LOYAL", wr, report_path=str(wording_report)),
                _single_panel("url", "SEQ 1 NON FID", ur, report_path=str(url_report)),
            ]}
            return home._do_archive(last_run, "Germany", "Revamp Inac")

        _archive_once("OK", "OK", "1")
        regressions = _archive_once("KO", "KO", "2")

        combined_regressions = [r for r in regressions if r["mode"] == "combined"]
        assert combined_regressions
        assert "regressed_items" not in combined_regressions[0]


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
            "excel_path": excel_file,
            "panels": [_single_panel("wording", "SEQ 1", [{"Status": "OK"}], report_path=str(tmp_path / "report.xlsx"))],
        }

        home.ensure_archived(last_run)

        assert self.do_archive_calls == [("Germany", "Revamp Inac")]
        assert self.prompt_calls == []

    def test_opens_the_completion_prompt_when_nothing_is_detected(self, tmp_path):
        excel_file = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo Welt", "audit-component-header"]],
        }, wording_column="TRANSLATION KLINGON")
        last_run = {
            "excel_path": excel_file,
            "panels": [_single_panel("wording", "SEQ 1", [{"Status": "OK"}], report_path=str(tmp_path / "report_2.xlsx"))],
        }

        home.ensure_archived(last_run)

        assert self.do_archive_calls == []
        assert len(self.prompt_calls) == 1

    def test_is_idempotent_for_an_already_archived_run(self, tmp_path):
        excel_file = _make_combined_excel(tmp_path)
        last_run = {
            "excel_path": excel_file,
            "panels": [_single_panel("wording", "SEQ 1", [{"Status": "OK"}], report_path=str(tmp_path / "report_3.xlsx"))],
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

    def test_comparer_button_without_any_upload_is_disabled_with_an_explanation(self, tmp_path, monkeypatch):
        # Regression test : le bouton etait auparavant toujours actif, et
        # n'affichait l'erreur qu'apres coup (clic -> st.error). Il est
        # maintenant desactive tant que rien n'est uploade, avec une caption
        # expliquant pourquoi.
        monkeypatch.chdir(tmp_path)
        at = AppTest.from_file(HOME_PATH)
        at.run()
        assert not at.exception
        assert at.button[0].disabled is True
        assert any("Uploadez le fichier HTML et le fichier Excel" in c.value for c in at.caption)

    def test_batch_mode_and_generic_checkboxes_live_in_an_expanded_panel(self, tmp_path, monkeypatch):
        # Regroupees dans un panneau "Options avancees" deplie par defaut
        # (voir home.py) : ces reglages restent visibles d'emblee, meme
        # s'ils ne concernent qu'une minorite de campagnes.
        monkeypatch.chdir(tmp_path)
        at = AppTest.from_file(HOME_PATH)
        at.run()
        assert not at.exception
        advanced = next(e for e in at.expander if e.label == "⚙️ Options avancées")
        assert advanced.proto.expanded is True
        assert len(advanced.checkbox) == 2


class TestHomeAppRendersLastRun:
    """Le bloc d'affichage final (dispatch onglets/direct, banniere/metrique/
    recap, _render_panel) n'a jamais ete couvert par un test executant
    reellement le script Streamlit - l'upload de fichier necessaire pour y
    arriver via le bouton Comparer n'est pas simulable par AppTest (voir
    TestHomeAppSmoke). On contourne ça en injectant directement last_run
    dans session_state puis en relançant le script - ensure_archived est
    neutralise (deja teste independamment sur ses propres cas, voir
    TestDoArchive/TestEnsureArchived) : seul le RENDU nous interesse ici."""

    def _run_with_last_run(self, tmp_path, monkeypatch, last_run):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(home, "ensure_archived", lambda last_run: None)
        at = AppTest.from_file(HOME_PATH)
        at.run()
        at.session_state["last_run"] = last_run
        at.run()
        return at

    def _row(self, status):
        return {
            "Component Name": "Header", "Element": "Title", "Source Sheet": "",
            "Expected Class": "audit-component-header", "HTML Class": "audit-component-header",
            "Excel Wording": "Hallo", "HTML Wording": "Hallo", "Status": status,
            "Error Type": "" if status == "OK" else "Content mismatch",
            "Similarity": "100%", "Criticality": "" if status == "OK" else "High", "Match Type": "Direct",
        }

    def test_renders_a_single_panel_run_without_exception(self, tmp_path, monkeypatch):
        report_path = tmp_path / "report.xlsx"
        make_details_report(str(report_path), [self._row("OK")])
        last_run = {
            "excel_path": "irrelevant.xlsx", "top_banner": None, "combined_score": None, "recap_rows": None,
            "panels": [{
                "mode": "wording", "consolidated": False,
                "results_by_sequence": {"SEQ 1": [self._row("OK")]},
                "report_path": str(report_path), "download_filename": "r.xlsx",
                "title": "Comparaison terminée", "tab_label": None,
            }],
            "debug_logs": "",
        }

        at = self._run_with_last_run(tmp_path, monkeypatch, last_run)

        assert not at.exception
        assert any("Comparaison terminée" in s.value for s in at.success)

    def test_renders_a_two_panel_combined_run_with_tabs_and_metric(self, tmp_path, monkeypatch):
        wording_report = tmp_path / "wording.xlsx"
        make_details_report(str(wording_report), [self._row("OK")])
        url_report = tmp_path / "url.xlsx"
        make_details_report(str(url_report), [self._row("KO")])
        last_run = {
            "excel_path": "irrelevant.xlsx",
            "top_banner": "Comparaison terminée (Wording + URL)",
            "combined_score": {"ok": 1, "total": 2},
            "recap_rows": None,
            "panels": [
                {
                    "mode": "wording", "consolidated": False,
                    "results_by_sequence": {"SEQ 1": [self._row("OK")]},
                    "report_path": str(wording_report), "download_filename": "w.xlsx",
                    "title": "📝 Résultats Wording", "tab_label": "📝 Résultats Wording",
                },
                {
                    "mode": "url", "consolidated": False,
                    "results_by_sequence": {"SEQ 1": [self._row("KO")]},
                    "report_path": str(url_report), "download_filename": "u.xlsx",
                    "title": "🔗 Résultats URL", "tab_label": "🔗 Résultats URL",
                },
            ],
            "debug_logs": "",
        }

        at = self._run_with_last_run(tmp_path, monkeypatch, last_run)

        assert not at.exception
        assert any("Comparaison terminée (Wording + URL)" in s.value for s in at.success)
        assert any("Score combiné" in m.label for m in at.metric)
        assert {t.label for t in at.tabs} == {"📝 Résultats Wording", "🔗 Résultats URL"}

    def test_renders_a_consolidated_batch_run_with_recap_table(self, tmp_path, monkeypatch):
        report_path = tmp_path / "multi.xlsx"
        make_details_report(str(report_path), [self._row("OK")])
        last_run = {
            "excel_path": "irrelevant.xlsx", "top_banner": None, "combined_score": None,
            "recap_rows": [{"Séquence": "SEQ 1", "Total": 1, "OK": 1, "KO": 0, "Score": 100.0}],
            "panels": [{
                "mode": "wording", "consolidated": True,
                "results_by_sequence": {"SEQ 1": [self._row("OK")]},
                "report_path": str(report_path), "download_filename": "multi.xlsx",
                "title": "Comparaison terminée pour 1 séquence(s)", "tab_label": None,
            }],
            "debug_logs": "",
        }

        at = self._run_with_last_run(tmp_path, monkeypatch, last_run)

        assert not at.exception
        assert any("Récapitulatif par séquence" in h.value for h in at.subheader)
        assert any("séquence(s)" in s.value for s in at.success)

    def test_batch_expanders_auto_expand_only_the_sequences_with_a_ko(self, tmp_path, monkeypatch):
        # Regression test : sur un run multi-sequences, la sequence en echec
        # doit s'ouvrir automatiquement (pour la trouver sans cliquer chaque
        # expander une par une), la sequence propre reste repliee.
        report_path = tmp_path / "multi.xlsx"
        make_details_report(str(report_path), [self._row("OK"), self._row("KO")])
        last_run = {
            "excel_path": "irrelevant.xlsx", "top_banner": None, "combined_score": None,
            "recap_rows": [
                {"Séquence": "SEQ 1", "Total": 1, "OK": 1, "KO": 0, "Score": 100.0},
                {"Séquence": "SEQ 2", "Total": 1, "OK": 0, "KO": 1, "Score": 0.0},
            ],
            "panels": [{
                "mode": "wording", "consolidated": True,
                "results_by_sequence": {"SEQ 1": [self._row("OK")], "SEQ 2": [self._row("KO")]},
                "report_path": str(report_path), "download_filename": "multi.xlsx",
                "title": "Comparaison terminée pour 2 séquence(s)", "tab_label": None,
            }],
            "debug_logs": "",
        }

        at = self._run_with_last_run(tmp_path, monkeypatch, last_run)

        assert not at.exception
        clean_expander = next(e for e in at.expander if e.label.startswith("✅ SEQ 1"))
        failing_expander = next(e for e in at.expander if e.label.startswith("⚠️ SEQ 2"))
        assert clean_expander.proto.expanded is False
        assert failing_expander.proto.expanded is True


class TestHomeAppClearsLastRunOnInputChange:
    """Regression test : changer le type de test (ou de fichier - non
    simulable par AppTest, voir _FakeUploadedFile et TestCurrentInputsSignature
    pour la couverture equivalente sur les fichiers) sans recliquer sur
    "Comparer" doit effacer l'ancien resultat plutot que le laisser affiche a
    tort (voir home.py, bloc "EFFACE LE DERNIER RESULTAT SI LES ENTREES ONT
    CHANGE")."""

    def _inject_last_run(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(home, "ensure_archived", lambda last_run: None)
        report_path = tmp_path / "report.xlsx"
        make_details_report(str(report_path), [{
            "Component Name": "Header", "Element": "Title", "Source Sheet": "",
            "Expected Class": "audit-component-header", "HTML Class": "audit-component-header",
            "Excel Wording": "Hallo", "HTML Wording": "Hallo", "Status": "OK",
            "Error Type": "", "Similarity": "100%", "Criticality": "", "Match Type": "Direct",
        }])
        last_run = {
            "excel_path": "irrelevant.xlsx", "top_banner": None, "combined_score": None, "recap_rows": None,
            "panels": [{
                "mode": "wording", "consolidated": False,
                "results_by_sequence": {"SEQ 1": [{"Status": "OK"}]},
                "report_path": str(report_path), "download_filename": "r.xlsx",
                "title": "Comparaison terminée", "tab_label": None,
            }],
            "debug_logs": "",
        }
        at = AppTest.from_file(HOME_PATH)
        at.run()
        at.session_state["last_run"] = last_run
        at.run()
        assert "last_run" in at.session_state  # sanity check avant le vrai scenario teste
        return at

    def test_rerun_without_any_change_keeps_the_result(self, tmp_path, monkeypatch):
        # Un rerun "neutre" (equivalent a une interaction avec un widget qui
        # n'affecte ni le mode ni les fichiers, ex: la case a cocher "Afficher
        # uniquement les erreurs" dans le tableau de resultat) ne doit pas
        # effacer un resultat toujours valide.
        at = self._inject_last_run(tmp_path, monkeypatch)
        at.run()
        assert "last_run" in at.session_state

    def test_switching_the_test_mode_clears_the_result(self, tmp_path, monkeypatch):
        at = self._inject_last_run(tmp_path, monkeypatch)
        at.radio[0].set_value("URL").run()
        assert "last_run" not in at.session_state

    def test_toggling_batch_mode_clears_the_result(self, tmp_path, monkeypatch):
        at = self._inject_last_run(tmp_path, monkeypatch)
        at.checkbox[0].set_value(True).run()  # case "Traiter plusieurs séquences en une fois"
        assert "last_run" not in at.session_state
