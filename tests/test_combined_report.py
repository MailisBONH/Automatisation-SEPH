import pandas as pd
import pytest

import qa.archive as archive
import qa.combined_report as combined_report
from conftest import make_details_report as _make_details_report


@pytest.fixture(autouse=True)
def isolated_output(tmp_path, monkeypatch):
    output_dir = str(tmp_path / "output")
    monkeypatch.setattr(archive, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(combined_report, "OUTPUT_DIR", output_dir)
    return tmp_path


class TestBuildCombinedReport:
    def test_returns_none_when_only_wording_archived(self, tmp_path, isolated_output):
        wording_path = tmp_path / "wording_report.xlsx"
        _make_details_report(wording_path, [{"Status": "OK"}, {"Status": "KO"}])
        archive.archive_report(str(wording_path), "Germany", "Revamp Inac", "SEQ 1", mode="wording")

        path, stats = combined_report.build_combined_report("Germany", "Revamp Inac", "SEQ 1")
        assert path is None
        assert stats is None

    def test_returns_none_when_only_url_archived(self, tmp_path, isolated_output):
        url_path = tmp_path / "url_report.xlsx"
        _make_details_report(url_path, [{"Status": "OK"}])
        archive.archive_report(str(url_path), "Germany", "Revamp Inac", "SEQ 1", mode="url")

        path, stats = combined_report.build_combined_report("Germany", "Revamp Inac", "SEQ 1")
        assert path is None
        assert stats is None

    def test_returns_none_when_nothing_archived(self, isolated_output):
        path, stats = combined_report.build_combined_report("Germany", "Revamp Inac", "SEQ 1")
        assert path is None
        assert stats is None

    def test_combines_wording_and_url_scores(self, tmp_path, isolated_output):
        wording_path = tmp_path / "wording_report.xlsx"
        _make_details_report(wording_path, [{"Status": "OK"}, {"Status": "OK"}, {"Status": "KO"}])
        archive.archive_report(str(wording_path), "Germany", "Revamp Inac", "SEQ 1", mode="wording")

        url_path = tmp_path / "url_report.xlsx"
        _make_details_report(url_path, [{"Status": "OK"}])
        archive.archive_report(str(url_path), "Germany", "Revamp Inac", "SEQ 1", mode="url")

        path, stats = combined_report.build_combined_report("Germany", "Revamp Inac", "SEQ 1")

        assert path is not None
        assert stats == {"total": 4, "ok": 3, "ko": 1, "score": 75.0}

    def test_combined_report_is_archived_under_its_own_type(self, tmp_path, isolated_output):
        wording_path = tmp_path / "wording_report.xlsx"
        _make_details_report(wording_path, [{"Status": "OK"}])
        archive.archive_report(str(wording_path), "Germany", "Revamp Inac", "SEQ 1", mode="wording")

        url_path = tmp_path / "url_report.xlsx"
        _make_details_report(url_path, [{"Status": "OK"}])
        archive.archive_report(str(url_path), "Germany", "Revamp Inac", "SEQ 1", mode="url")

        combined_report.build_combined_report("Germany", "Revamp Inac", "SEQ 1")

        combined_files = archive.list_reports("Germany", "Revamp Inac", "SEQ 1", mode="combined")
        assert len(combined_files) == 1

    def test_combined_report_contains_summary_and_detail_sheets(self, tmp_path, isolated_output):
        wording_path = tmp_path / "wording_report.xlsx"
        _make_details_report(wording_path, [{"Status": "OK", "Excel Wording": "Hello"}])
        archive.archive_report(str(wording_path), "Germany", "Revamp Inac", "SEQ 1", mode="wording")

        url_path = tmp_path / "url_report.xlsx"
        _make_details_report(url_path, [{"Status": "KO", "URL attendue (Excel)": "https://x"}])
        archive.archive_report(str(url_path), "Germany", "Revamp Inac", "SEQ 1", mode="url")

        path, _ = combined_report.build_combined_report("Germany", "Revamp Inac", "SEQ 1")

        assert pd.ExcelFile(path).sheet_names == ["Summary", "Wording", "URL"]

        wording_sheet = pd.read_excel(path, sheet_name="Wording")
        assert wording_sheet["Excel Wording"].iloc[0] == "Hello"

        url_sheet = pd.read_excel(path, sheet_name="URL")
        assert url_sheet["URL attendue (Excel)"].iloc[0] == "https://x"
