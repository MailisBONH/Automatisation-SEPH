import pandas as pd
import pytest

from conftest import make_wording_workbook
from qa.classification import (
    detect_classification,
    migrate_legacy_loyalty_sequences,
    normalize_sequence_name,
    resolve_sequence_alias,
)


def _fail(*args, **kwargs):
    raise AssertionError("should not be called")


def _make_wording_xlsx(tmp_path, sheet_name, wording_column_name, filename="wording.xlsx"):
    """Feuille wording minimale (une seule ligne de donnees, "Hallo Welt") :
    ligne 0 ignoree (header=1 cote lecture), ligne 1 = en-tetes, ligne 2 =
    donnees."""
    return make_wording_workbook(
        tmp_path,
        {sheet_name: [["Header", "Title", "Hallo Welt", "audit-component-header"]]},
        filename=filename,
        wording_column=wording_column_name,
    )


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
        ("SEQ 1 NON FID", "SEQ 1"),
        ("SEQ 1 NON LOYAL", "SEQ 1"),
        ("SEQ 1 FID", "SEQ 1"),
        ("SEQ 1 LOYAL", "SEQ 1"),
        ("SEQ 4 NON FID", "SEQ 4"),
        ("SEQ 4 WELCOME BACK NON LOYAL", "SEQ 4"),
        ("SEQ 4 FID", "SEQ 4"),
        ("SEQ 4 WELCOME BACK LOYAL", "SEQ 4"),
        ("seq 04 loyal", "SEQ 4"),
        ("SEQ 2", "SEQ 2"),
    ])
    def test_merges_fid_loyal_vocabulary_and_audience_variants_into_one_bare_sequence(self, raw, expected):
        # LOYAL/NON LOYAL (et FID/NON FID) sont les 2 wordings possibles d'un
        # seul et meme template HTML (voir find_sibling_sequence_sheet, deja
        # fusionnes cote moteur QA) : l'historique ne doit pas les eclater en
        # deux sequences distinctes.
        assert normalize_sequence_name(raw) == expected

    def test_no_sequence_number_returned_unchanged(self):
        assert normalize_sequence_name("Footer generic block") == "Footer generic block"

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

    def test_utm_domain_wins_over_ambiguous_wording_language_on_conflict(self, tmp_path):
        # Regression : "FRENCH" (colonne wording) peut designer la France OU
        # la Belgique - LANGUAGE_TO_COUNTRY retient la traduction litterale
        # (France) sans pouvoir trancher. Si le domaine des URLs UTM dit
        # explicitement Belgique, c'est un signal plus fiable et il doit
        # l'emporter, pas la simple traduction du mot-cle de langue.
        path = tmp_path / "conflict.xlsx"
        wording_rows = [
            ["", "", "", ""],
            ["COMPONENT NAME", "ELEMENT", "TRANSLATION FRENCH", "TECHNICAL NAME"],
            ["Header", "Title", "Bonjour", "audit-component-header"],
        ]
        utm_rows = [
            [None, "!! CHECK !!", None, None, None, None, None, None],
            [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
            [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
            ["HEADER", "https://example.com/", "trigger", "email", "revamp_inac", "seq1", "",
             "https://www.example.be/?utm_content=header"],
        ]
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(wording_rows).to_excel(writer, sheet_name="SEQ 1", index=False, header=False)
            pd.DataFrame(utm_rows).to_excel(writer, sheet_name="UTM TRIGGER", index=False, header=False)

        result = detect_classification(path, "SEQ 1", "wording")
        assert result["country"] == "Belgium"

    def test_language_still_used_as_fallback_when_no_utm_domain_detected(self, tmp_path):
        # Aucun onglet UTM dans ce classeur : la langue reste le seul signal
        # disponible, donc toujours utilisee (pas de domaine pour la
        # contredire).
        path = _make_wording_xlsx(tmp_path, "SEQ 1", "TRANSLATION FRENCH")
        result = detect_classification(path, "SEQ 1", "wording")
        assert result["country"] == "France"

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


class TestMigrateLegacyLoyaltySequences:
    """
    Reprise retroactive (voir pages/1_Historique.py) : toute sequence
    archivee sous un ancien nom qualifie LOYAL/NON LOYAL/FID/NON FID doit
    etre fusionnee vers son nom canonique "SEQ <n>", avec les memes outils
    que la fusion manuelle (move_sequence_dir + rewrite_classification).
    """

    def test_merges_loyal_and_non_loyal_pair_into_the_bare_sequence(self, monkeypatch):
        tree = {
            "Germany": {
                "Revamp Inac": {
                    "SEQ 1 LOYAL": [{"score": 100}],
                    "SEQ 1 NON LOYAL": [{"score": 90}],
                }
            }
        }
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: tree)

        moved, rewritten = [], []
        monkeypatch.setattr(
            "qa.classification.move_sequence_dir",
            lambda oc, ot, os_, nc, nt, ns: moved.append((oc, ot, os_, nc, nt, ns)),
        )
        monkeypatch.setattr(
            "qa.classification.rewrite_classification",
            lambda oc, ot, os_, nc, nt, ns: rewritten.append((oc, ot, os_, nc, nt, ns)),
        )

        migrated = migrate_legacy_loyalty_sequences()

        assert sorted(m[2] for m in migrated) == ["SEQ 1 LOYAL", "SEQ 1 NON LOYAL"]
        assert all(m[3] == "SEQ 1" for m in migrated)
        assert len(moved) == 2 and len(rewritten) == 2

    def test_already_canonical_sequence_is_left_untouched(self, monkeypatch):
        tree = {"Germany": {"Revamp Inac": {"SEQ 1": [{"score": 100}]}}}
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: tree)
        monkeypatch.setattr("qa.classification.move_sequence_dir", _fail)
        monkeypatch.setattr("qa.classification.rewrite_classification", _fail)

        assert migrate_legacy_loyalty_sequences() == []

    def test_non_sequence_named_folder_is_left_untouched(self, monkeypatch):
        # Pas de numero de sequence detectable -> normalize_sequence_name
        # retourne le nom inchange, rien a fusionner.
        tree = {"Germany": {"Revamp Inac": {"Footer generic block": [{"score": 100}]}}}
        monkeypatch.setattr("qa.classification.build_history_tree", lambda: tree)
        monkeypatch.setattr("qa.classification.move_sequence_dir", _fail)
        monkeypatch.setattr("qa.classification.rewrite_classification", _fail)

        assert migrate_legacy_loyalty_sequences() == []
