import os
import time

import pandas as pd
import pytest

from conftest import make_utm_workbook, make_wording_workbook
from qa.excel_loader import (
    _open_excel_file,
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

    def test_short_sequence_keyword_does_not_match_longer_sequence_number(self, tmp_path):
        # Regression : "SEQ 1" ne doit jamais matcher "SEQ 10 ..." par simple
        # sous-chaine (prefixe numerique) - seul un onglet nomme "SEQ 1 ..."
        # (ou "SEQ 1" seul) doit compter comme un match.
        path = make_xlsx(tmp_path, {"SEQ 10 NON LOYAL": pd.DataFrame({"a": [1]})})
        with pytest.raises(ValueError, match="Aucune feuille"):
            find_sheet(path, "SEQ 1")


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

    def test_ambiguous_first_candidate_raises_instead_of_falling_back(self):
        # Regression : le premier mot-cle (preferentiel) matche 2 colonnes
        # (ambigu, l'outil ne peut pas deviner laquelle), mais un second
        # mot-cle de repli resoudrait une colonne unique sans rapport. C'est
        # l'ambiguite du premier mot-cle qui doit remonter, pas le fallback
        # silencieux sur "TEXT".
        df = pd.DataFrame(columns=["TRANSLATION ENGLISH", "TRANSLATION GERMAN", "TEXT"])
        with pytest.raises(ValueError, match="plusieurs colonnes correspondent à 'TRANSLATION'"):
            find_column(df, ["TRANSLATION", "TEXT"])


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

    def test_no_matching_sequence_keyword_raises(self, tmp_path):
        path = make_utm_workbook(tmp_path, {"SEQ 1 FID": ["https://example.com/fid"]})
        with pytest.raises(ValueError, match="Aucune séquence"):
            load_excel_urls(path, "SEQ 99")

    def test_ambiguous_sequence_keyword_raises(self, tmp_path):
        path = make_utm_workbook(tmp_path, {
            "SEQ 1 FID": ["https://example.com/a"],
            "SEQ 1 NON FID": ["https://example.com/b"],
        })
        with pytest.raises(ValueError, match="Plusieurs séquences"):
            load_excel_urls(path, "SEQ 1")


class TestLoadExcelUrlsWithoutUrlLabelColumn:
    """
    Distinct du cas "onglet a plat sans sections nommees" (voir
    TestLoadExcelUrlsReturnsResolvedSectionName) : ici, la colonne "URL"
    (celle qui porterait les labels de sequence) est totalement absente de
    l'onglet - meme la ligne d'en-tete ne la contient pas. Impossible de
    decouper en sequences des ce stade, keyword est ignore et tout l'onglet
    est retourne, comme le cas a plat.
    """

    def test_returns_every_url_unfiltered(self, tmp_path):
        path = tmp_path / "utm_no_url_column.xlsx"
        rows = [
            [None, "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
            ["HEADER", "trigger", "email", "revamp_inac", "seq1", "", "https://example.com/a"],
            [None, "trigger", "email", "revamp_inac", "seq1", "", "https://example.com/b"],
        ]
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="UTM TRIGGER", index=False, header=False)

        sheet, section_name, urls = load_excel_urls(str(path), "anything")

        assert section_name == ""
        assert [u["url"] for u in urls] == ["https://example.com/a", "https://example.com/b"]
        # Le label (colonne la plus a gauche) est report vers le bas quand vide.
        assert [u["label"] for u in urls] == ["HEADER", "HEADER"]


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


class TestOpenExcelFileCache:
    """
    _open_excel_file cache le pd.ExcelFile ouvert par (chemin, date de
    derniere modification, taille) pour eviter de reparcourir le meme
    classeur a chaque interaction Streamlit (voir home.py) - le point
    critique a garantir est qu'un fichier reuploade sous le meme chemin
    (cas reel : temp/<session_id>_input.xlsx, ecrase a chaque upload) ne
    sert jamais une version perimee.
    """

    def test_reading_the_same_unchanged_path_returns_the_same_object(self, tmp_path):
        path = make_xlsx(tmp_path, {"Sheet1": pd.DataFrame({"a": [1]})})
        assert _open_excel_file(path) is _open_excel_file(path)

    def test_overwriting_the_path_invalidates_the_cache(self, tmp_path):
        path = tmp_path / "workbook.xlsx"
        pd.DataFrame({"a": [1]}).to_excel(path, sheet_name="First", index=False)
        first = _open_excel_file(str(path))
        assert first.sheet_names == ["First"]

        pd.DataFrame({"a": [1]}).to_excel(path, sheet_name="Second", index=False)
        # Force un mtime distinct du premier ecrit : sur un systeme de
        # fichiers a resolution grossiere (ou sous charge), deux ecritures
        # rapprochees peuvent partager le meme mtime_ns - la seule autre
        # partie de la cle (la taille) pourrait alors coincider par hasard
        # et faire ce test flaky. On ne teste pas la precision de l'horloge
        # du systeme de fichiers, juste que la cle change bien avec elle.
        os.utime(path, (time.time() + 5, time.time() + 5))
        second = _open_excel_file(str(path))

        assert second.sheet_names == ["Second"]
        assert second is not first
