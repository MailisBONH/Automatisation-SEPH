import pandas as pd
import pytest

from qa.classification import (
    detect_classification,
    normalize_sequence_name,
    resolve_sequence_alias,
)


def _make_wording_xlsx(tmp_path, sheet_name, wording_column_name, filename="wording.xlsx"):
    """Feuille wording minimale : ligne 0 ignoree (header=1 cote lecture),
    ligne 1 = en-tetes, ligne 2 = donnees."""
    path = tmp_path / filename
    rows = [
        ["", "", "", ""],
        ["COMPONENT NAME", "ELEMENT", wording_column_name, "TECHNICAL NAME"],
        ["Header", "Title", "Hallo Welt", "audit-component-header"],
    ]
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False, header=False)
    return str(path)


def _make_utm_xlsx(tmp_path, campaign_value="revamp_inac", url="https://www.example.de/?utm_content=x",
                    sheet_name="UTM TRIGGER", filename="utm.xlsx"):
    """Plusieurs lignes de donnees partageant le meme campaign_value (comme un
    vrai onglet UTM, une campagne trackant plusieurs URLs) : la ligne modele
    "Mandatory" ne doit pas l'emporter sur le vote majoritaire (voir mode())."""
    path = tmp_path / filename
    rows = [
        [None, "!! CHECK !!", None, None, None, None, None, None],
        [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
        [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
        ["HEADER", "https://example.com/", "trigger", "email", campaign_value, "seq1", "", url],
        [None, "https://example.com/2", "trigger", "email", campaign_value, "seq1", "", url],
        [None, "https://example.com/3", "trigger", "email", campaign_value, "seq1", "", url],
    ]
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False, header=False)
    return str(path)


def _make_combined_xlsx(tmp_path):
    """Classeur avec un onglet wording (Allemand) ET un onglet UTM (meme campagne)."""
    path = tmp_path / "combined.xlsx"
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
        [None, "https://example.com/2", "trigger", "email", "revamp_inac", "seq1", "",
         "https://www.example.de/?utm_content=cta"],
        [None, "https://example.com/3", "trigger", "email", "revamp_inac", "seq1", "",
         "https://www.example.de/?utm_content=footer"],
    ]
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(wording_rows).to_excel(writer, sheet_name="SEQ 1 NON LOYAL", index=False, header=False)
        pd.DataFrame(utm_rows).to_excel(writer, sheet_name="UTM TRIGGER", index=False, header=False)
    return str(path)


class TestNormalizeSequenceName:
    @pytest.mark.parametrize("raw,expected", [
        ("SEQ 1 NON FID", "SEQ 1 NON LOYAL"),
        ("SEQ 1 NON LOYAL", "SEQ 1 NON LOYAL"),
        ("SEQ 1 FID", "SEQ 1 LOYAL"),
        ("SEQ 1 LOYAL", "SEQ 1 LOYAL"),
        ("SEQ 4 NON FID", "SEQ 4 NON LOYAL"),
        ("SEQ 4 WELCOME BACK NON LOYAL", "SEQ 4 NON LOYAL"),
        ("SEQ 4 FID", "SEQ 4 LOYAL"),
        ("SEQ 4 WELCOME BACK LOYAL", "SEQ 4 LOYAL"),
        ("seq 04 loyal", "SEQ 4 LOYAL"),
    ])
    def test_reconciles_fid_and_loyal_vocabulary(self, raw, expected):
        assert normalize_sequence_name(raw) == expected

    def test_no_sequence_number_returned_unchanged(self):
        assert normalize_sequence_name("Footer generic block") == "Footer generic block"

    def test_no_qualifier_returns_bare_seq_label(self):
        assert normalize_sequence_name("SEQ 2") == "SEQ 2"

    def test_empty_or_none_passthrough(self):
        assert normalize_sequence_name("") == ""
        assert normalize_sequence_name(None) is None


class TestResolveSequenceAlias:
    def test_qualified_sequence_returned_unchanged(self, monkeypatch):
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: {})
        assert resolve_sequence_alias("Germany", "Revamp Inac", "SEQ 2 LOYAL") == "SEQ 2 LOYAL"

    def test_no_sequence_number_returned_unchanged(self, monkeypatch):
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: {})
        assert resolve_sequence_alias("Germany", "Revamp Inac", "Footer") == "Footer"

    def test_reuses_single_matching_existing_sequence(self, monkeypatch):
        tree = {"Germany": {"Revamp Inac": {"SEQ 2 LOYAL": []}}}
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: tree)
        assert resolve_sequence_alias("Germany", "Revamp Inac", "SEQ 2") == "SEQ 2 LOYAL"

    def test_ambiguous_when_multiple_candidates(self, monkeypatch):
        tree = {"Germany": {"Revamp Inac": {"SEQ 2 LOYAL": [], "SEQ 2 NON LOYAL": []}}}
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: tree)
        assert resolve_sequence_alias("Germany", "Revamp Inac", "SEQ 2") == "SEQ 2"

    def test_no_existing_match_returned_unchanged(self, monkeypatch):
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: {})
        assert resolve_sequence_alias("Germany", "Revamp Inac", "SEQ 2") == "SEQ 2"

    def test_history_lookup_failure_is_swallowed(self, monkeypatch):
        def boom():
            raise RuntimeError("disk error")
        monkeypatch.setattr("qa.classification.build_history_tree", boom)
        assert resolve_sequence_alias("Germany", "Revamp Inac", "SEQ 2") == "SEQ 2"


class TestDetectClassification:
    def test_country_from_wording_language(self, tmp_path):
        path = _make_wording_xlsx(tmp_path, "SEQ 1 NON LOYAL", "TRANSLATION GERMAN")
        result = detect_classification(path, "SEQ 1 NON LOYAL", "wording")
        assert result["country"] == "Germany"
        assert result["trigger"] is None  # pas d'onglet UTM dans ce classeur

    def test_country_and_trigger_from_utm_tab(self, tmp_path):
        path = _make_utm_xlsx(tmp_path, campaign_value="welcome_back", url="https://www.example.fr/?utm_content=x")
        result = detect_classification(path, "anything", "url")
        assert result["country"] == "France"
        assert result["trigger"] == "Welcome Back"

    def test_combined_workbook_uses_wording_language_and_utm_trigger(self, tmp_path):
        path = _make_combined_xlsx(tmp_path)
        result = detect_classification(path, "SEQ 1 NON LOYAL", "wording")
        assert result == {"country": "Germany", "trigger": "Revamp Inac"}

    def test_unrecognized_language_returns_none_country(self, tmp_path):
        path = _make_wording_xlsx(tmp_path, "SEQ 1", "TRANSLATION KLINGON")
        result = detect_classification(path, "SEQ 1", "wording")
        assert result["country"] is None

    def test_sheet_not_found_does_not_raise(self, tmp_path):
        path = _make_wording_xlsx(tmp_path, "SEQ 1 NON LOYAL", "TRANSLATION GERMAN")
        result = detect_classification(path, "SEQ 4 DOES NOT EXIST", "wording")
        assert result == {"country": None, "trigger": None}

    def test_template_mandatory_row_does_not_win_the_majority_vote(self, tmp_path):
        # Onglet UTM avec une seule ligne de donnees reelles : la ligne
        # modele "Mandatory" du gabarit (colonne CAMPAIGN = "TRIGGER", sans
        # URL trackee) ne doit pas l'emporter sur l'unique vraie campagne.
        path = tmp_path / "utm_single_row.xlsx"
        rows = [
            [None, "!! CHECK !!", None, None, None, None, None, None],
            [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
            [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
            ["HEADER", "https://example.com/", "trigger", "email", "welcome_back", "seq1", "",
             "https://www.example.fr/?utm_content=header"],
        ]
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="UTM TRIGGER", index=False, header=False)

        result = detect_classification(str(path), "anything", "url")
        assert result["trigger"] == "Welcome Back"
