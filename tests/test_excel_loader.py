import pandas as pd
import pytest

from qa.excel_loader import find_column, find_sheet


def make_xlsx(tmp_path, sheets: dict):
    """sheets: {sheet_name: DataFrame}"""
    path = tmp_path / "test.xlsx"
    with pd.ExcelWriter(path) as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return str(path)


class TestFindSheet:
    def test_unique_case_insensitive_match(self, tmp_path):
        path = make_xlsx(tmp_path, {"SEQ 1 NON LOYAL": pd.DataFrame({"a": [1]}), "GENERIC": pd.DataFrame({"a": [1]})})
        assert find_sheet(path, "non loyal") == "SEQ 1 NON LOYAL"

    def test_no_match_raises(self, tmp_path):
        path = make_xlsx(tmp_path, {"SEQ 1": pd.DataFrame({"a": [1]})})
        with pytest.raises(ValueError, match="Aucune feuille"):
            find_sheet(path, "SEQ 4")

    def test_ambiguous_match_raises(self, tmp_path):
        path = make_xlsx(
            tmp_path,
            {"SEQ 1 NON FID": pd.DataFrame({"a": [1]}), "SEQ 4 NON FID": pd.DataFrame({"a": [1]})},
        )
        with pytest.raises(ValueError, match="Plusieurs feuilles"):
            find_sheet(path, "NON FID")


class TestFindColumn:
    def test_single_keyword_match(self):
        df = pd.DataFrame(columns=["COMPONENT NAME", "TRANSLATION GERMAN"])
        assert find_column(df, "TRANSLATION") == "TRANSLATION GERMAN"

    def test_falls_back_to_second_candidate(self):
        # First keyword ("TRANSLATION") has no match at all, second ("TEXT") does.
        df = pd.DataFrame(columns=["COMPONENT NAME", "TEXT"])
        assert find_column(df, ["TRANSLATION", "TEXT"]) == "TEXT"

    def test_first_candidate_wins_when_both_present(self):
        df = pd.DataFrame(columns=["TRANSLATION GERMAN", "TEXT"])
        assert find_column(df, ["TRANSLATION", "TEXT"]) == "TRANSLATION GERMAN"

    def test_no_candidate_matches_raises(self):
        df = pd.DataFrame(columns=["COMPONENT NAME"])
        with pytest.raises(ValueError, match="Impossible de déterminer"):
            find_column(df, "TRANSLATION")

    def test_ambiguous_single_keyword_raises(self):
        df = pd.DataFrame(columns=["TEXT ENGLISH", "TEXT GERMAN"])
        with pytest.raises(ValueError, match="Impossible de déterminer"):
            find_column(df, "TEXT")
