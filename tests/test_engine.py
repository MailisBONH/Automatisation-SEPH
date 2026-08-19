import pandas as pd
import pytest

from conftest import make_utm_workbook, make_wording_workbook
from qa.engine import QA_RUN_READ_ERRORS, run_qa_for_sequence

_make_wording_workbook = make_wording_workbook


def _make_html(tmp_path, text, component_class, filename="email.html"):
    path = tmp_path / filename
    path.write_text(
        f'<div class="text-contribution"><p class="{component_class}">{text}</p></div>',
        encoding="utf-8",
    )
    return str(path)


def _make_utm_workbook(tmp_path, sections: dict, filename="utm.xlsx"):
    """Le label ("audit-component-cta") matche la classe posee par
    _make_html_with_link, pour que ces tests de fusion de section ne
    declenchent pas accidentellement un KO de classe (Wrong component /
    class not found) - ce n'est pas ce qu'ils cherchent a verifier."""
    return make_utm_workbook(tmp_path, sections, filename=filename, label="audit-component-cta")


def _make_html_with_link(tmp_path, href, filename="email.html"):
    path = tmp_path / filename
    # Ancetre audit-component-* : sans classe HTML detectable, run_url_qa
    # renvoie desormais KO_URL_CLASS_NOT_FOUND (comme le wording), ce que
    # ces tests de fusion de section ne cherchent pas a verifier.
    path.write_text(
        f'<div class="audit-component-cta"><a href="{href}">Shop</a></div>',
        encoding="utf-8",
    )
    return str(path)


class TestRunQaForSequenceMergesSiblingSheet:
    def test_sibling_sheet_wording_is_merged_automatically(self, tmp_path):
        # Le HTML affiche la branche "LOYAL", mais le mot-cle donne resout
        # l'onglet "NON LOYAL" : sans fusion automatique de l'onglet frere,
        # ce texte ressortirait a tort comme wording introuvable.
        excel_file = _make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Loyalty", "Text", "Dir fehlt noch 1 Punkt", "audit-component-loyalty"]],
            "SEQ 1 LOYAL": [["Loyalty", "Text", "Dir fehlen noch 2 Punkte", "audit-component-loyalty"]],
        })
        html_file = _make_html(tmp_path, "Dir fehlen noch 2 Punkte", "audit-component-loyalty")

        results, sheet = run_qa_for_sequence(html_file, excel_file, "SEQ 1 NON LOYAL", mode="wording")

        assert sheet == "SEQ 1 NON LOYAL"
        matched = [r for r in results if r["HTML Wording"] == "Dir fehlen noch 2 Punkte"]
        assert matched and matched[0]["Status"] == "OK"

    def test_no_sibling_sheet_leaves_behavior_unchanged(self, tmp_path):
        excel_file = _make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Header", "Title", "Hallo Welt", "audit-component-header"]],
        })
        html_file = _make_html(tmp_path, "Hallo Welt", "audit-component-header")

        results, sheet = run_qa_for_sequence(html_file, excel_file, "SEQ 1 NON LOYAL", mode="wording")

        assert sheet == "SEQ 1 NON LOYAL"
        assert len(results) == 1
        assert results[0]["Status"] == "OK"

    def test_sibling_wordings_identical_to_sequence_are_not_merged_as_duplicates(self, tmp_path):
        # Regression : la plupart des composants (header, carrousel...) ne
        # changent pas selon l'audience - seuls quelques wordings sont
        # reellement specifiques a LOYAL/NON LOYAL. Fusionner aveuglement
        # tout l'onglet frere produisait 2 lignes "OK" identiques pour
        # chaque wording partage entre les deux onglets.
        excel_file = _make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [
                ["Header", "Title", "Hallo Welt", "audit-component-header"],
                ["Loyalty", "Text", "Dir fehlt noch 1 Punkt", "audit-component-loyalty"],
            ],
            "SEQ 1 LOYAL": [
                ["Header", "Title", "Hallo Welt", "audit-component-header"],
                ["Loyalty", "Text", "Dir fehlen noch 2 Punkte", "audit-component-loyalty"],
            ],
        })
        html_file = _make_html(tmp_path, "Hallo Welt", "audit-component-header")

        results, sheet = run_qa_for_sequence(html_file, excel_file, "SEQ 1 NON LOYAL", mode="wording")

        matched = [r for r in results if r["HTML Wording"] == "Hallo Welt"]
        assert len(matched) == 1
        assert matched[0]["Status"] == "OK"

    def test_sibling_wording_only_slightly_different_is_still_merged_separately(self, tmp_path):
        # Regression : le filtre de doublon exige une egalite STRICTE (texte
        # normalise identique), pas une similarite approchee. Un pave de
        # mentions legales recopie avec une seule variation (ex. un nom de
        # marque different) doit rester 2 wordings Excel distincts - chacun
        # signale separement s'il n'est pas trouve - plutot que d'etre
        # silencieusement traite comme "le meme" et masque.
        excel_file = _make_wording_workbook(tmp_path, {
            "SEQ 1 NON LOYAL": [["Footer", "Legal", "Clean at Sephora Unlimited programme", "audit-component-footer"]],
            "SEQ 1 LOYAL": [["Footer", "Legal", "Clean at Sephora My Sephora programme", "audit-component-footer"]],
        })
        html_file = _make_html(tmp_path, "Something else entirely", "audit-component-unrelated")

        results, sheet = run_qa_for_sequence(html_file, excel_file, "SEQ 1 NON LOYAL", mode="wording")

        footer_rows = [r for r in results if r.get("Expected Class") == "audit-component-footer"]
        assert len(footer_rows) == 2
        assert all(r["Status"] == "KO" for r in footer_rows)


class TestRunQaForSequenceMergesSiblingUrlSection:
    """
    Symetrique de TestRunQaForSequenceMergesSiblingSheet, mais pour le mode
    "url" : le meme HTML peut rendre le lien de l'une ou l'autre audience
    (FID/NON FID) via la meme condition AEM que le wording, donc la section
    UTM "soeur" doit etre fusionnee automatiquement, exactement comme
    l'onglet frere cote wording.
    """

    def test_sibling_section_url_is_merged_automatically(self, tmp_path):
        # Le HTML rend le lien "FID", mais le mot-cle resout la section
        # "NON FID" : sans fusion automatique de la section soeur, ce lien
        # ressortirait a tort comme "Unexpected URL (not in Excel)" (aucune
        # URL Excel ne le couvrirait). L'URL "NON FID" elle-meme, non rendue
        # par ce HTML precis, reste normalement en KO "URL not found" -
        # comme un wording de la branche non rendue reste "Missing wording"
        # cote wording (voir TestRunQaForSequenceMergesSiblingSheet) : ce
        # n'est pas ce que ce test verifie.
        excel_file = _make_utm_workbook(tmp_path, {
            "SEQ 1 NON FID": ["https://example.com/non-fid"],
            "SEQ 1 FID": ["https://example.com/fid"],
        })
        html_file = _make_html_with_link(tmp_path, "https://example.com/fid")

        results, sheet = run_qa_for_sequence(html_file, excel_file, "SEQ 1 NON FID", mode="url")

        assert sheet == "UTM TRIGGER"
        matched = [r for r in results if r["HTML Wording"] == "https://example.com/fid"]
        assert matched and matched[0]["Status"] == "OK"
        assert not any(r["Error Type"] == "Unexpected URL (not in Excel)" for r in results)

    def test_no_sibling_section_leaves_behavior_unchanged(self, tmp_path):
        excel_file = _make_utm_workbook(tmp_path, {"SEQ 1 NON FID": ["https://example.com/non-fid"]})
        html_file = _make_html_with_link(tmp_path, "https://example.com/non-fid")

        results, sheet = run_qa_for_sequence(html_file, excel_file, "SEQ 1 NON FID", mode="url")

        assert len(results) == 1
        assert results[0]["Status"] == "OK"

    def test_sibling_urls_identical_to_section_are_not_merged_as_duplicates(self, tmp_path):
        # La plupart des liens (header, social...) ne changent pas selon
        # l'audience : un lien identique (meme URL, meme label) cote
        # section soeur ne doit pas produire un doublon.
        excel_file = _make_utm_workbook(tmp_path, {
            "SEQ 1 NON FID": ["https://example.com/shared"],
            "SEQ 1 FID": ["https://example.com/shared"],
        })
        html_file = _make_html_with_link(tmp_path, "https://example.com/shared")

        results, sheet = run_qa_for_sequence(html_file, excel_file, "SEQ 1 NON FID", mode="url")

        matched = [r for r in results if r["HTML Wording"] == "https://example.com/shared"]
        assert len(matched) == 1
        assert matched[0]["Status"] == "OK"

    def test_sibling_url_identical_but_under_a_different_label_is_still_a_single_row(self, tmp_path):
        # Regression : le meme lien peut etre liste sous des labels
        # differents entre les deux sections (ex. "HEADER" cote NON FID,
        # "SOCIAL" cote FID) - c'est toujours la MEME destination testee
        # contre le MEME lien HTML, donc un seul OK doit apparaitre, pas un
        # par label. run_url_qa ne filtre jamais le matching par label
        # (seulement l'affichage), donc dedoublonner par URL seule ne fait
        # perdre aucune verification reelle.
        path = tmp_path / "utm_diff_label.xlsx"
        rows = [
            [None, "!! CHECK !!", None, None, None, None, None, None],
            [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
            [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
            [None, "SEQ 1 NON FID", None, None, None, None, None, None],
            ["audit-component-cta", "https://example.com/", "trigger", "email", "revamp_inac", "seq1", "",
             "https://example.com/shared"],
            [None, "SEQ 1 FID", None, None, None, None, None, None],
            ["SOCIAL", "https://example.com/", "trigger", "email", "revamp_inac", "seq1", "",
             "https://example.com/shared"],
        ]
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="UTM TRIGGER", index=False, header=False)

        html_file = _make_html_with_link(tmp_path, "https://example.com/shared")

        results, sheet = run_qa_for_sequence(html_file, str(path), "SEQ 1 NON FID", mode="url")

        matched = [r for r in results if r["HTML Wording"] == "https://example.com/shared"]
        assert len(matched) == 1
        assert matched[0]["Status"] == "OK"


class TestRunQaForSequenceReadErrors:
    """
    home.py catche QA_RUN_READ_ERRORS (pas juste ValueError) autour de ses
    appels a run_qa/run_qa_for_sequence, pour degrader proprement (message
    clair) plutot que planter sur un fichier illisible - HTML non-UTF8
    (load_raw_html lit toujours en UTF-8) ou Excel corrompu/introuvable.
    """

    def test_non_utf8_html_raises_an_error_covered_by_qa_run_read_errors(self, tmp_path):
        excel_file = _make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo", "audit-component-header"]],
        })
        html_file = tmp_path / "email.html"
        html_file.write_bytes("café".encode("latin-1"))  # sequence invalide en UTF-8

        with pytest.raises(QA_RUN_READ_ERRORS):
            run_qa_for_sequence(str(html_file), excel_file, "SEQ 1", mode="wording")

    def test_missing_html_file_raises_an_error_covered_by_qa_run_read_errors(self, tmp_path):
        excel_file = _make_wording_workbook(tmp_path, {
            "SEQ 1": [["Header", "Title", "Hallo", "audit-component-header"]],
        })

        with pytest.raises(QA_RUN_READ_ERRORS):
            run_qa_for_sequence(str(tmp_path / "does_not_exist.html"), excel_file, "SEQ 1", mode="wording")
