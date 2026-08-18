import pandas as pd
import pytest

from qa.excel_loader import (
    find_column,
    find_sheet,
    find_sibling_sequence_sheet,
    find_sibling_url_section,
    list_url_sequence_choices,
    list_wording_sheet_choices,
    load_excel_urls,
    load_excel_wordings,
    load_generic_wordings,
    load_sibling_sequence_wordings,
    load_sibling_url_section,
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


def make_utm_workbook(tmp_path, sections: dict, filename="utm.xlsx", sheet_name="UTM TRIGGER"):
    """
    sections: {section_name: [url1, url2, ...]} - un onglet UTM avec
    plusieurs sequences empilees, chacune demarree par une ligne
    "separateur" (une seule cellule non vide, dans la colonne "URL" - voir
    qa.excel_loader._parse_url_sections).
    """
    path = tmp_path / filename
    rows = [
        [None, "!! CHECK !!", None, None, None, None, None, None],
        [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
        [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
    ]
    for name, urls in sections.items():
        rows.append([None, name, None, None, None, None, None, None])
        for url in urls:
            rows.append(["HEADER", "https://example.com/", "trigger", "email", "revamp_inac", "seq1", "", url])

    with pd.ExcelWriter(path) as writer:
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

    def test_sibling_wordings_carry_the_sibling_sheet_name(self, tmp_path):
        # Regression : le rapport doit pouvoir indiquer que ce wording vient
        # de l'onglet frere fusionne, pas de l'onglet sequence teste.
        path = make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Loyalty", "Text", "Dir fehlt noch 1 Punkt", "audit-component-loyalty"]],
            "SEQ 1 LOYAL": [["Loyalty", "Text", "Dir fehlen noch 2 Punkte", "audit-component-loyalty"]],
        })
        _, wordings = load_sibling_sequence_wordings(path, "SEQ 1 NON LOYAL")
        assert wordings[0]["Sheet"] == "SEQ 1 LOYAL"


class TestWordingsCarrySourceSheet:
    def test_load_excel_wordings_tags_each_row_with_its_own_sheet(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        sheet, wordings = load_excel_wordings(path, "NON LOYAL")
        assert sheet == "SEQ 1 NON LOYAL"
        assert wordings[0]["Sheet"] == "SEQ 1 NON LOYAL"

    def test_load_generic_wordings_tags_each_row_with_the_generic_sheet(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "GENERIC": [["Footer", "Text", "Mentions légales", "audit-component-footer"]],
        })
        sheet, wordings = load_generic_wordings(path)
        assert sheet == "GENERIC"
        assert wordings[0]["Sheet"] == "GENERIC"


class TestListWordingSheetChoices:
    """
    Le menu de selection d'une sequence Wording (home.py) ne doit jamais
    proposer l'onglet UTM (dedie a la QA URL) ni l'onglet GENERIC (toujours
    fusionne automatiquement), et ne doit proposer qu'UN SEUL onglet d'une
    paire FID/NON FID (ou LOYAL/NON LOYAL) "frere" puisque l'autre est
    fusionne automatiquement des qu'on teste le premier.
    """

    def test_excludes_url_and_generic_sheets(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo", "audit-component-header"]],
            "GENERIC": [["Footer", "Text", "Mentions légales", "audit-component-footer"]],
        })
        # Ajoute un onglet UTM (structure differente, sans importance ici -
        # seul le nom compte pour le filtrage).
        with pd.ExcelWriter(path, mode="a", engine="openpyxl") as writer:
            pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="UTM TRACKING", index=False)

        choices = list_wording_sheet_choices(path)
        names = [name for name, _ in choices]
        assert names == ["SEQ 1"]

    def test_merges_loyal_non_loyal_pair_into_a_single_labeled_choice(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
            "SEQ 1 LOYAL": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        choices = list_wording_sheet_choices(path)

        assert len(choices) == 1
        name, label = choices[0]
        assert name == "SEQ 1 NON LOYAL"
        assert "SEQ 1 LOYAL" in label

    def test_sheet_without_sibling_is_listed_with_its_own_name_as_label(self, tmp_path):
        path = make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        assert list_wording_sheet_choices(path) == [("SEQ 1", "SEQ 1")]


class TestLoadExcelUrlsReturnsResolvedSectionName:
    def test_returns_the_exact_matched_section_name(self, tmp_path):
        path = make_utm_workbook(tmp_path, {
            "SEQ 1 FID": ["https://example.com/fid"],
            "SEQ 1 NON FID": ["https://example.com/non-fid"],
        })
        sheet, section_name, urls = load_excel_urls(path, "NON FID")
        assert section_name == "SEQ 1 NON FID"
        assert [u["url"] for u in urls] == ["https://example.com/non-fid"]

    def test_flat_tab_without_sections_returns_empty_section_name(self, tmp_path):
        path = make_utm_workbook(tmp_path, {"": ["https://example.com/a"]})
        sheet, section_name, urls = load_excel_urls(path, "anything")
        assert section_name == ""

    def test_each_url_dict_carries_its_own_section(self, tmp_path):
        # Chaque URL porte sa section d'origine (utilisee ensuite pour la
        # colonne "Source Section" du rapport, voir qa.url_qa).
        path = make_utm_workbook(tmp_path, {
            "SEQ 1 NON FID": ["https://example.com/non-fid"],
        })
        _, _, urls = load_excel_urls(path, "NON FID")
        assert urls[0]["Section"] == "SEQ 1 NON FID"

    def test_flat_tab_url_carries_an_empty_section(self, tmp_path):
        path = make_utm_workbook(tmp_path, {"": ["https://example.com/a"]})
        _, _, urls = load_excel_urls(path, "anything")
        assert urls[0]["Section"] == ""


class TestFindSiblingUrlSection:
    """
    Symetrique de TestFindSiblingSequenceSheet (voir plus haut), mais pour
    des sections d'un meme onglet UTM plutot que des onglets separes.
    """

    def test_finds_sibling_with_opposite_fid_status(self, tmp_path):
        path = make_utm_workbook(tmp_path, {
            "SEQ 1 FID": ["https://example.com/fid"],
            "SEQ 1 NON FID": ["https://example.com/non-fid"],
        })
        assert find_sibling_url_section(path, "SEQ 1 FID") == "SEQ 1 NON FID"
        assert find_sibling_url_section(path, "SEQ 1 NON FID") == "SEQ 1 FID"

    def test_no_sibling_when_only_one_section_exists(self, tmp_path):
        path = make_utm_workbook(tmp_path, {"SEQ 1 FID": ["https://example.com/fid"]})
        assert find_sibling_url_section(path, "SEQ 1 FID") is None

    def test_no_sibling_for_unrelated_sequence_number(self, tmp_path):
        path = make_utm_workbook(tmp_path, {
            "SEQ 1 FID": ["https://example.com/a"],
            "SEQ 2 NON FID": ["https://example.com/b"],
        })
        assert find_sibling_url_section(path, "SEQ 1 FID") is None

    def test_ambiguous_multiple_candidates_returns_none(self, tmp_path):
        path = make_utm_workbook(tmp_path, {
            "SEQ 1 FID": ["https://example.com/a"],
            "SEQ 1 NON FID": ["https://example.com/b"],
            "SEQ 1 WELCOME BACK NON FID": ["https://example.com/c"],
        })
        assert find_sibling_url_section(path, "SEQ 1 FID") is None


class TestLoadSiblingUrlSection:
    def test_returns_none_and_empty_list_when_no_sibling(self, tmp_path):
        path = make_utm_workbook(tmp_path, {"SEQ 1 FID": ["https://example.com/fid"]})
        sibling_name, urls = load_sibling_url_section(path, "SEQ 1 FID")
        assert sibling_name is None
        assert urls == []

    def test_loads_the_sibling_section_urls(self, tmp_path):
        path = make_utm_workbook(tmp_path, {
            "SEQ 1 FID": ["https://example.com/fid"],
            "SEQ 1 NON FID": ["https://example.com/non-fid"],
        })
        sibling_name, urls = load_sibling_url_section(path, "SEQ 1 FID")
        assert sibling_name == "SEQ 1 NON FID"
        assert [u["url"] for u in urls] == ["https://example.com/non-fid"]


class TestListUrlSequenceChoices:
    """
    Le menu de selection d'une sequence URL (home.py) ne doit proposer
    qu'UN SEUL onglet d'une paire FID/NON FID "soeur", comme pour le menu
    Wording (voir TestListWordingSheetChoices).
    """

    def test_merges_fid_non_fid_pair_into_a_single_labeled_choice(self, tmp_path):
        path = make_utm_workbook(tmp_path, {
            "SEQ 1 FID": ["https://example.com/fid"],
            "SEQ 1 NON FID": ["https://example.com/non-fid"],
        })
        choices = list_url_sequence_choices(path)

        assert len(choices) == 1
        name, label = choices[0]
        assert name == "SEQ 1 FID"
        assert "SEQ 1 NON FID" in label

    def test_section_without_sibling_is_listed_with_its_own_name_as_label(self, tmp_path):
        path = make_utm_workbook(tmp_path, {"SEQ 1 FID": ["https://example.com/fid"]})
        assert list_url_sequence_choices(path) == [("SEQ 1 FID", "SEQ 1 FID")]

    def test_flat_tab_without_sections_returns_empty_list(self, tmp_path):
        path = make_utm_workbook(tmp_path, {"": ["https://example.com/a"]})
        assert list_url_sequence_choices(path) == []
