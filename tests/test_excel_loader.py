import pandas as pd
import pytest

from qa.excel_loader import (
    find_column,
    find_sheet,
    find_sibling_sequence_sheet,
    load_sibling_sequence_wordings,
)


def make_xlsx(tmp_path, sheets: dict):
    """sheets: {sheet_name: DataFrame}"""
    path = tmp_path / "test.xlsx"
    with pd.ExcelWriter(path) as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return str(path)


def make_wording_workbook(tmp_path, sheets: dict, filename="wording.xlsx"):
    """sheets: {sheet_name: [[Component, Element, Wording, Technical], ...]} -
    ligne vide en tete + ligne d'en-tetes (header=1 cote lecture), comme un
    vrai onglet wording (voir tests/test_classification.py)."""
    path = tmp_path / filename
    header_row = ["COMPONENT NAME", "ELEMENT", "TRANSLATION", "TECHNICAL NAME"]
    with pd.ExcelWriter(path) as writer:
        for sheet_name, data_rows in sheets.items():
            rows = [["", "", "", ""], header_row] + data_rows
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False, header=False)
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


class TestFindSiblingSequenceSheet:
    """
    Onglets "SEQ X LOYAL"/"SEQ X NON LOYAL" (ou FID/NON FID) : le meme
    template HTML bascule de l'un a l'autre via une condition AEM selon
    l'audience, donc les deux onglets doivent etre reconnus comme "freres"
    d'une meme sequence pour etre fusionnes avant le matching.
    """

    def test_finds_sibling_with_opposite_loyalty_status(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
            "SEQ 1 LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        assert find_sibling_sequence_sheet(path, "SEQ 1 NON LOYAL") == "SEQ 1 LOYAL"
        assert find_sibling_sequence_sheet(path, "SEQ 1 LOYAL") == "SEQ 1 NON LOYAL"

    def test_fid_non_fid_naming_also_recognized(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "SEQ 2 FID": [["Header", "Title", "Hallo", "audit-component-header"]],
            "SEQ 2 NON FID": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        assert find_sibling_sequence_sheet(path, "SEQ 2 NON FID") == "SEQ 2 FID"

    def test_no_sibling_when_other_sheet_is_unrelated(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
            "GENERIC": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        assert find_sibling_sequence_sheet(path, "SEQ 1 NON LOYAL") is None

    def test_none_when_sheet_name_has_no_sequence_number_or_loyalty_keyword(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "GENERIC": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        assert find_sibling_sequence_sheet(path, "GENERIC") is None

    def test_ambiguous_multiple_candidates_returns_none(self, tmp_path):
        # 2 onglets "LOYAL" pour la SEQ 1 : impossible de savoir lequel est
        # le vrai frere, mieux vaut ne rien fusionner que fusionner a tort.
        path = make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
            "SEQ 1 LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
            "SEQ 1 WELCOME BACK LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        assert find_sibling_sequence_sheet(path, "SEQ 1 NON LOYAL") is None


class TestLoadSiblingSequenceWordings:
    def test_returns_none_and_empty_list_when_no_sibling(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        sibling_sheet, wordings = load_sibling_sequence_wordings(path, "SEQ 1 NON LOYAL")
        assert sibling_sheet is None
        assert wordings == []

    def test_loads_the_sibling_sheet_wordings(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Loyalty", "Text", "Dir fehlt noch 1 Punkt", "audit-component-loyalty"]],
            "SEQ 1 LOYAL": [["Loyalty", "Text", "Dir fehlen noch 2 Punkte", "audit-component-loyalty"]],
        })
        sibling_sheet, wordings = load_sibling_sequence_wordings(path, "SEQ 1 NON LOYAL")
        assert sibling_sheet == "SEQ 1 LOYAL"
        assert len(wordings) == 1
        assert wordings[0]["Wording"] == "Dir fehlen noch 2 Punkte"
        assert wordings[0]["Technical"] == "audit-component-loyalty"
