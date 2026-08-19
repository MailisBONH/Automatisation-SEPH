import pandas as pd
import pytest
from openpyxl import Workbook

import qa.report as report
from qa.report import (
    _sort_by_criticality,
    build_rich_diff,
    build_rich_diff_char,
    save_consolidated_report,
    save_report,
    score_fill_color,
    sort_results_by_html_order,
    status_fill_color,
    write_summary_sheet,
)


def _joined(rich):
    return "".join(block.text for block in rich)


def _bold_parts(rich):
    return [block.text for block in rich if block.font.b]


class TestBuildRichDiffChar:
    """
    Diff caractere par caractere pour le rapport URL (contrairement a
    build_rich_diff, qui decoupe par mots - inadapte a une URL, qui n'a pas
    d'espaces).
    """

    def test_identical_strings_have_no_bold_part(self):
        rich1, rich2 = build_rich_diff_char("https://example.com/shop", "https://example.com/shop")
        assert _bold_parts(rich1) == []
        assert _bold_parts(rich2) == []
        assert _joined(rich1) == "https://example.com/shop"
        assert _joined(rich2) == "https://example.com/shop"

    def test_single_character_typo_is_highlighted_alone(self):
        # "shop" vs "shpo" : seuls "o"/"p" different, pas l'URL entiere -
        # exactement ce qu'un decoupage par mots ne peut pas faire ici.
        rich1, rich2 = build_rich_diff_char("shop", "shpo")
        assert _joined(rich1) == "shop"
        assert _joined(rich2) == "shpo"
        # Les caracteres identiques ("sh" au debut) ne sont jamais en gras.
        assert "".join(block.text for block in rich1 if not block.font.b).startswith("sh")

    def test_completely_different_strings_are_fully_bold(self):
        rich1, rich2 = build_rich_diff_char("abc", "xyz")
        assert _bold_parts(rich1) == ["abc"]
        assert _bold_parts(rich2) == ["xyz"]

    def test_empty_strings_produce_no_blocks(self):
        rich1, rich2 = build_rich_diff_char("", "")
        assert list(rich1) == []
        assert list(rich2) == []

    def test_none_is_treated_as_empty_string(self):
        rich1, rich2 = build_rich_diff_char(None, "abc")
        assert list(rich1) == []
        assert _joined(rich2) == "abc"


class TestBuildRichDiffWordStillWorksAsBefore:
    """Regression : le diff mot-a-mot existant (wording) ne doit pas etre
    affecte par l'ajout de la version caractere par caractere."""

    def test_word_level_diff_highlights_whole_differing_word(self):
        rich1, rich2 = build_rich_diff("Discover our new arrivals", "Discover our old arrivals")
        bold1 = _bold_parts(rich1)
        bold2 = _bold_parts(rich2)
        assert bold1 == ["new "]
        assert bold2 == ["old "]


@pytest.fixture(autouse=True)
def isolated_report_output(tmp_path, monkeypatch):
    """save_report/save_consolidated_report ecrivent un .xlsx dans OUTPUT_DIR
    et appellent qa.history.record_run - isole les deux du vrai dossier
    output/ du projet (record_run n'a pas de "path=" explicite ici, il
    ecrirait sinon dans le vrai output/history.jsonl)."""
    monkeypatch.setattr(report, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(report, "record_run", lambda *a, **k: None)
    return tmp_path


def _row(status, excel="Hello", html="Hello", component="Header", element="Title",
         error="", criticality="", html_order=0):
    return {
        "Component Name": component, "Element": element, "Source Sheet": "",
        "Expected Class": "audit-component-header", "HTML Class": "audit-component-header",
        "Excel Wording": excel, "HTML Wording": html, "Status": status, "Error Type": error,
        "Similarity": "100%" if status == "OK" else "50%", "Criticality": criticality,
        "Match Type": "Direct", "_html_order": html_order,
    }


class TestScoreFillColor:
    def test_perfect_score_is_green(self):
        assert score_fill_color(100) == "C6EFCE"

    def test_at_warn_threshold_is_yellow(self):
        assert score_fill_color(80) == "FFEB9C"

    def test_just_below_warn_threshold_is_red(self):
        assert score_fill_color(79.9) == "FFC7CE"

    def test_zero_is_red(self):
        assert score_fill_color(0) == "FFC7CE"


class TestStatusFillColor:
    def test_ok_is_green(self):
        assert status_fill_color("OK") == "C6EFCE"

    def test_ko_is_red(self):
        assert status_fill_color("KO") == "FFC7CE"

    def test_anything_else_is_treated_as_ko(self):
        assert status_fill_color(None) == "FFC7CE"


class TestWriteSummarySheet:
    def test_title_meta_rows_and_score_cell_color(self):
        wb = Workbook()
        ws = wb.active
        meta = [("Date", "2026-01-01"), ("Total", 10), ("OK", 8), ("KO", 2), ("Score", 80)]

        write_summary_sheet(ws, "QA - SEQ 1", meta, score=80)

        assert ws.title == "Summary"
        assert ws["A1"].value == "QA - SEQ 1"
        assert ws["A3"].value == "Date" and ws["B3"].value == "2026-01-01"
        assert ws["A7"].value == "Score" and ws["B7"].value == 80
        # Score = derniere entree de meta -> ligne 3 + len(meta) - 1 = 7.
        assert ws["B7"].fill.start_color.rgb[-6:] == score_fill_color(80)

    def test_score_row_position_adapts_to_a_shorter_meta_list(self):
        # qa.combined_report n'a que 4 entrees (pas 5) : la cellule de score
        # a colorer doit suivre, pas rester figee sur une ligne fixe.
        wb = Workbook()
        ws = wb.active
        meta = [("Date", "2026-01-01"), ("Score Wording", "80%"), ("Score URL", "90%"), ("Score combiné", "85%")]

        write_summary_sheet(ws, "QA combiné", meta, score=85)

        assert ws["B6"].value == "85%"
        assert ws["B6"].fill.start_color.rgb[-6:] == score_fill_color(85)


class TestSortResultsByHtmlOrder:
    def test_sorts_by_html_order_when_present(self):
        df = pd.DataFrame([_row("OK", html_order=2), _row("OK", html_order=0), _row("OK", html_order=1)])
        sorted_df = sort_results_by_html_order(df)
        assert list(sorted_df["_html_order"]) == [0, 1, 2]

    def test_falls_back_to_component_name_when_html_order_is_absent(self):
        df = pd.DataFrame([_row("OK", component="Loyalty"), _row("OK", component="Footer")])
        df = df.drop(columns="_html_order")
        sorted_df = sort_results_by_html_order(df)
        assert list(sorted_df["Component Name"]) == ["Footer", "Loyalty"]

    def test_prefix_columns_take_priority_over_html_order(self):
        df = pd.DataFrame([
            {**_row("OK", html_order=0), "Sequence": "SEQ 2"},
            {**_row("OK", html_order=5), "Sequence": "SEQ 1"},
        ])
        sorted_df = sort_results_by_html_order(df, prefix_columns=["Sequence"])
        assert list(sorted_df["Sequence"]) == ["SEQ 1", "SEQ 2"]


class TestSortByCriticality:
    def test_sorts_high_before_medium_before_low(self):
        # Un tri alphabetique donnerait "High" < "Low" < "Medium" - faux.
        df = pd.DataFrame([
            _row("KO", criticality="Low"),
            _row("KO", criticality="High"),
            _row("KO", criticality="Medium"),
        ])
        sorted_df = _sort_by_criticality(df)
        assert list(sorted_df["Criticality"]) == ["High", "Medium", "Low"]

    def test_ties_break_on_html_order_not_alphabetical_component_name(self):
        # "LEGAL NOTICE" avant "LOYALTY SECTION" alphabetiquement, alors que
        # loyalty apparait bien avant sur la page (voir docstring de la fonction).
        df = pd.DataFrame([
            _row("KO", criticality="High", component="LEGAL NOTICE", html_order=5),
            _row("KO", criticality="High", component="LOYALTY SECTION", html_order=1),
        ])
        sorted_df = _sort_by_criticality(df)
        assert list(sorted_df["Component Name"]) == ["LOYALTY SECTION", "LEGAL NOTICE"]


class TestSaveReport:
    def test_generates_the_three_expected_sheets(self, tmp_path):
        results = [_row("OK", html_order=0), _row("KO", criticality="High", html_order=1)]
        path = save_report(results, "SEQ 1 NON LOYAL", mode="wording")

        assert pd.ExcelFile(path).sheet_names == ["Summary", "Details", "KO Only"]

    def test_summary_reports_the_correct_score(self, tmp_path):
        results = [_row("OK"), _row("OK"), _row("KO", criticality="High")]
        path = save_report(results, "SEQ 1", mode="wording")

        wb_data = pd.read_excel(path, sheet_name="Summary", header=None)
        # Cf. write_summary_sheet : "Score" est en A7/B7 (5 entrees a partir
        # de la ligne 3 -> 1-indexed row 7, soit index 6 en 0-indexed).
        assert wb_data.iloc[6, 0] == "Score"
        assert wb_data.iloc[6, 1] == pytest.approx(66.67, abs=0.01)

    def test_ko_only_sheet_contains_only_ko_rows(self, tmp_path):
        results = [_row("OK"), _row("KO", criticality="High"), _row("KO", criticality="Low")]
        path = save_report(results, "SEQ 1", mode="wording")

        ko_df = pd.read_excel(path, sheet_name="KO Only")
        assert len(ko_df) == 2
        assert set(ko_df["Status"]) == {"KO"}

    def test_url_mode_uses_url_specific_columns_and_header_labels(self, tmp_path):
        results = [{
            "Component Name": "", "Source Section": "SEQ 1 FID", "Expected Class": "audit-component-cta",
            "HTML Class": "audit-component-cta", "Excel Wording": "https://a.com/x",
            "HTML Wording": "https://a.com/x", "Status": "OK", "Error Type": "",
            "Similarity": "100%", "Criticality": "", "Occurrences": 1, "_html_order": 0,
        }]
        path = save_report(results, "SEQ 1 FID", mode="url")

        details = pd.read_excel(path, sheet_name="Details")
        assert "URL attendue (Excel)" in details.columns
        assert "Section (FID/NON FID)" in details.columns


class TestSaveConsolidatedReport:
    def test_adds_a_sequence_column_and_a_per_sequence_recap(self, tmp_path):
        results_by_sequence = {
            "SEQ 1": [_row("OK"), _row("OK")],
            "SEQ 2": [_row("OK"), _row("KO", criticality="High")],
        }
        path = save_consolidated_report(results_by_sequence, report_name="Multi", mode="wording")

        details = pd.read_excel(path, sheet_name="Details")
        assert set(details["Sequence"]) == {"SEQ 1", "SEQ 2"}

        recap = pd.read_excel(path, sheet_name="Summary", header=None)
        # En-tete du recap par sequence : ligne 9 (voir save_consolidated_report).
        assert list(recap.iloc[8, :5]) == ["Séquence", "Total", "OK", "KO", "Score"]

    def test_global_score_covers_all_sequences_combined(self, tmp_path):
        results_by_sequence = {"SEQ 1": [_row("OK"), _row("KO", criticality="High")]}
        path = save_consolidated_report(results_by_sequence, report_name="Multi", mode="wording")

        summary = pd.read_excel(path, sheet_name="Summary", header=None)
        assert summary.iloc[6, 0] == "Score global"
        assert summary.iloc[6, 1] == 50.0

    def test_empty_results_do_not_crash(self, tmp_path):
        path = save_consolidated_report({}, report_name="Multi", mode="wording")
        assert pd.ExcelFile(path).sheet_names == ["Summary", "Details", "KO Only"]
