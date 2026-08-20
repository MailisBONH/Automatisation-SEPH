import os
import time

import pytest

import qa.archive as archive
from conftest import fake_incrementing_datetime, make_details_report
from qa.report_diff import diff_reports, diff_uploaded_reports, load_last_two_reports_diff


def wording_row(component="Header", element="Title", source_sheet="SEQ 1", status="OK",
                 error_type="", html_wording="Bonjour"):
    return {
        "Component Name": component, "Element": element, "Source Sheet": source_sheet,
        "Expected Class": "audit-component-header", "HTML Class": "audit-component-header",
        "Excel Wording": "Bonjour", "HTML Wording": html_wording,
        "Status": status, "Error Type": error_type, "Similarity": "", "Criticality": "", "Match Type": "",
    }


def url_row(expected_class="HEADER", source_section="", status="OK", error_type="",
            html_wording="https://example.com/"):
    return {
        "Source Section": source_section, "Expected Class": expected_class, "HTML Class": "audit-component-header",
        "Excel Wording": "https://example.com/", "HTML Wording": html_wording,
        "Status": status, "Error Type": error_type, "Similarity": "100%", "Occurrences": 1,
    }


class TestDiffReportsWording:
    def test_regression_ok_to_ko(self):
        previous = [wording_row(status="OK")]
        current = [wording_row(status="KO", error_type="Content mismatch", html_wording="Bonsoir")]

        diff = diff_reports(previous, current, "wording")

        assert len(diff["regressed"]) == 1
        assert diff["regressed"][0]["previous"]["Status"] == "OK"
        assert diff["regressed"][0]["current"]["Status"] == "KO"
        assert diff["fixed"] == diff["changed"] == diff["added"] == diff["removed"] == []

    def test_fix_ko_to_ok(self):
        previous = [wording_row(status="KO", error_type="Minor typo", html_wording="Bonjou")]
        current = [wording_row(status="OK")]

        diff = diff_reports(previous, current, "wording")

        assert len(diff["fixed"]) == 1

    def test_ko_to_ko_with_different_error_type_is_changed(self):
        previous = [wording_row(status="KO", error_type="Minor typo", html_wording="Bonjou")]
        current = [wording_row(status="KO", error_type="Content mismatch", html_wording="Autre chose")]

        diff = diff_reports(previous, current, "wording")

        assert len(diff["changed"]) == 1
        assert diff["unchanged_ko"] == 0

    def test_ko_to_ko_with_identical_error_type_and_text_is_unchanged(self):
        previous = [wording_row(status="KO", error_type="Minor typo", html_wording="Bonjou")]
        current = [wording_row(status="KO", error_type="Minor typo", html_wording="Bonjou")]

        diff = diff_reports(previous, current, "wording")

        assert diff["changed"] == []
        assert diff["unchanged_ko"] == 1
        assert diff["regressed"] == diff["fixed"] == []

    def test_ok_to_ok_is_unchanged(self):
        previous = [wording_row(status="OK")]
        current = [wording_row(status="OK")]

        diff = diff_reports(previous, current, "wording")

        assert diff["unchanged_ok"] == 1
        assert not any(diff[k] for k in ("regressed", "fixed", "changed", "added", "removed"))

    def test_new_component_is_added(self):
        previous = [wording_row(component="Header")]
        current = [wording_row(component="Header"), wording_row(component="Footer", element="Legal")]

        diff = diff_reports(previous, current, "wording")

        assert len(diff["added"]) == 1
        assert diff["added"][0]["previous"] is None
        assert diff["added"][0]["current"]["Component Name"] == "Footer"

    def test_removed_component_is_removed(self):
        previous = [wording_row(component="Header"), wording_row(component="Footer", element="Legal")]
        current = [wording_row(component="Header")]

        diff = diff_reports(previous, current, "wording")

        assert len(diff["removed"]) == 1
        assert diff["removed"][0]["current"] is None
        assert diff["removed"][0]["previous"]["Component Name"] == "Footer"

    def test_same_component_element_from_different_source_sheet_are_distinct(self):
        # Un meme Component/Element peut venir de l'onglet sequence ET de
        # l'onglet frere LOYAL/NON LOYAL fusionne (voir Source Sheet) - ne
        # doivent pas etre confondus par la cle de diff.
        previous = [wording_row(source_sheet="SEQ 1")]
        current = [wording_row(source_sheet="SEQ 1"), wording_row(source_sheet="SEQ 1 LOYAL")]

        diff = diff_reports(previous, current, "wording")

        assert len(diff["added"]) == 1
        assert diff["added"][0]["current"]["Source Sheet"] == "SEQ 1 LOYAL"


class TestDiffReportsUrl:
    def test_regression_uses_expected_class_as_key(self):
        previous = [url_row(expected_class="HEADER", status="OK")]
        current = [url_row(expected_class="HEADER", status="KO", error_type="URL not found", html_wording="")]

        diff = diff_reports(previous, current, "url")

        assert len(diff["regressed"]) == 1

    def test_unexpected_url_without_expected_class_uses_html_wording_as_key(self):
        previous = [url_row(expected_class="", html_wording="https://example.com/social")]
        current = [url_row(expected_class="", html_wording="https://example.com/social", status="KO",
                            error_type="Unexpected URL (not in Excel)")]

        diff = diff_reports(previous, current, "url")

        assert len(diff["regressed"]) == 1

    def test_different_source_section_are_distinct_entries(self):
        previous = [url_row(expected_class="HEADER", source_section="SEQ 1 FID")]
        current = [
            url_row(expected_class="HEADER", source_section="SEQ 1 FID"),
            url_row(expected_class="HEADER", source_section="SEQ 1 NON FID"),
        ]

        diff = diff_reports(previous, current, "url")

        assert len(diff["added"]) == 1


class TestLoadLastTwoReportsDiff:
    @pytest.fixture(autouse=True)
    def isolated_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr(archive, "OUTPUT_DIR", str(tmp_path / "output"))
        return tmp_path

    def test_returns_none_with_fewer_than_two_reports(self, tmp_path):
        path = tmp_path / "report.xlsx"
        make_details_report(path, [{"Status": "OK", "Component Name": "Header", "Element": "Title",
                                     "Source Sheet": "SEQ 1"}])
        archive.archive_report(str(path), "Germany", "Revamp Inac", "SEQ 1", mode="wording")

        assert load_last_two_reports_diff("Germany", "Revamp Inac", "SEQ 1", "wording") is None

    def test_diffs_the_two_most_recent_wording_reports(self, tmp_path, monkeypatch):
        # archive_report() nomme son fichier a la seconde pres et list_reports()
        # trie par mtime reel : sur des fichiers factices archives coup sur
        # coup, les deux peuvent finir avec le meme nom (le 2e ecraserait le
        # 1er) et/ou des mtimes indiscernables. fake_incrementing_datetime
        # ecarte le 1er probleme, le backdatage explicite le 2nd - meme combo
        # que tests/test_archive.py::test_rotation_keeps_only_last_two_per_type.
        fake_incrementing_datetime(monkeypatch)

        older = tmp_path / "older.xlsx"
        make_details_report(older, [wording_row(status="OK")])
        older_path = archive.archive_report(str(older), "Germany", "Revamp Inac", "SEQ 1", mode="wording")
        os.utime(older_path, (time.time() - 60, time.time() - 60))

        newer = tmp_path / "newer.xlsx"
        make_details_report(newer, [wording_row(status="KO", error_type="Content mismatch",
                                                        html_wording="Bonsoir")])
        archive.archive_report(str(newer), "Germany", "Revamp Inac", "SEQ 1", mode="wording")

        diff = load_last_two_reports_diff("Germany", "Revamp Inac", "SEQ 1", "wording")

        assert len(diff["regressed"]) == 1

    def test_normalizes_french_url_headers_before_diffing(self, tmp_path, monkeypatch):
        # Les rapports url archives ont des en-tetes francais dans le
        # fichier Excel (voir qa.report.URL_HEADER_LABELS) - le diff doit
        # les reconnaitre malgre tout.
        fake_incrementing_datetime(monkeypatch)

        older = tmp_path / "older.xlsx"
        make_details_report(older, [{
            "Section (FID/NON FID)": "", "Classe attendue (Excel)": "HEADER",
            "Classe trouvée (HTML)": "audit-component-header",
            "URL attendue (Excel)": "https://example.com/", "URL trouvée (HTML)": "https://example.com/",
            "Status": "OK", "Raison": "", "Similarité (%)": "100%", "Occurrences (HTML)": 1,
        }])
        older_path = archive.archive_report(str(older), "Germany", "Revamp Inac", "SEQ 1", mode="url")
        os.utime(older_path, (time.time() - 60, time.time() - 60))

        newer = tmp_path / "newer.xlsx"
        make_details_report(newer, [{
            "Section (FID/NON FID)": "", "Classe attendue (Excel)": "HEADER",
            "Classe trouvée (HTML)": "", "URL attendue (Excel)": "https://example.com/",
            "URL trouvée (HTML)": "", "Status": "KO", "Raison": "URL not found",
            "Similarité (%)": "0%", "Occurrences (HTML)": 0,
        }])
        archive.archive_report(str(newer), "Germany", "Revamp Inac", "SEQ 1", mode="url")

        diff = load_last_two_reports_diff("Germany", "Revamp Inac", "SEQ 1", "url")

        assert len(diff["regressed"]) == 1


class TestDiffUploadedReports:
    """diff_uploaded_reports : meme moteur que load_last_two_reports_diff,
    mais a partir de 2 fichiers quelconques (pas d'archivage requis) -
    utilise directement des chemins de fichiers ici (un upload Streamlit se
    comporte comme un objet fichier standard pour pandas.read_excel, pas
    besoin de le simuler specifiquement)."""

    def test_diffs_two_arbitrary_wording_reports(self, tmp_path):
        previous_path = tmp_path / "previous.xlsx"
        make_details_report(previous_path, [wording_row(status="OK")])

        current_path = tmp_path / "current.xlsx"
        make_details_report(current_path, [wording_row(status="KO", error_type="Content mismatch",
                                                          html_wording="Bonsoir")])

        diff = diff_uploaded_reports(str(previous_path), str(current_path), "wording")

        assert len(diff["regressed"]) == 1

    def test_normalizes_french_url_headers(self, tmp_path):
        previous_path = tmp_path / "previous.xlsx"
        make_details_report(previous_path, [{
            "Section (FID/NON FID)": "", "Classe attendue (Excel)": "HEADER",
            "Classe trouvée (HTML)": "audit-component-header",
            "URL attendue (Excel)": "https://example.com/", "URL trouvée (HTML)": "https://example.com/",
            "Status": "OK", "Raison": "", "Similarité (%)": "100%", "Occurrences (HTML)": 1,
        }])

        current_path = tmp_path / "current.xlsx"
        make_details_report(current_path, [{
            "Section (FID/NON FID)": "", "Classe attendue (Excel)": "HEADER",
            "Classe trouvée (HTML)": "", "URL attendue (Excel)": "https://example.com/",
            "URL trouvée (HTML)": "", "Status": "KO", "Raison": "URL not found",
            "Similarité (%)": "0%", "Occurrences (HTML)": 0,
        }])

        diff = diff_uploaded_reports(str(previous_path), str(current_path), "url")

        assert len(diff["regressed"]) == 1

    def test_missing_details_sheet_raises_a_clear_error(self, tmp_path):
        from openpyxl import Workbook

        not_a_report = tmp_path / "not_a_report.xlsx"
        wb = Workbook()
        wb.active.title = "Summary"
        wb.save(not_a_report)

        valid_report = tmp_path / "valid.xlsx"
        make_details_report(valid_report, [wording_row(status="OK")])

        with pytest.raises(ValueError, match="Details"):
            diff_uploaded_reports(str(not_a_report), str(valid_report), "wording")

    def test_can_accept_file_like_objects_not_just_paths(self, tmp_path):
        # Un upload Streamlit se comporte comme un objet fichier binaire
        # (methode .read()), pas comme un chemin - verifie que
        # pandas.read_excel s'en sort tout aussi bien.
        previous_path = tmp_path / "previous.xlsx"
        make_details_report(previous_path, [wording_row(status="OK")])
        current_path = tmp_path / "current.xlsx"
        make_details_report(current_path, [wording_row(status="KO", error_type="Minor typo",
                                                          html_wording="Bonjou")])

        with open(previous_path, "rb") as previous_file, open(current_path, "rb") as current_file:
            diff = diff_uploaded_reports(previous_file, current_file, "wording")

        assert len(diff["regressed"]) == 1
