from qa.diagnostics import find_duplicate_components, find_unused_excel_classes, find_wrong_language_wordings


def excel_row(wording="text", technical=None, component="Comp"):
    return {"Component": component, "Element": "Elem", "Wording": wording, "Technical": technical}


def html_item(text="text", component_class=None):
    return {"text": text, "component_class": component_class}


class TestFindUnusedExcelClasses:
    def test_class_present_in_both_is_not_flagged(self):
        html_texts = [html_item(component_class="audit-component-header")]
        excel_wordings = [excel_row(technical="audit-component-header")]
        assert find_unused_excel_classes(html_texts, excel_wordings) == []

    def test_class_declared_but_absent_from_html_is_flagged(self):
        html_texts = [html_item(component_class="audit-component-advantage-card")]
        excel_wordings = [excel_row(technical="audit-component-reassurance-bloc")]
        assert find_unused_excel_classes(html_texts, excel_wordings) == ["audit-component-reassurance-bloc"]

    def test_none_and_missing_classes_are_ignored(self):
        html_texts = [html_item(component_class=None)]
        excel_wordings = [excel_row(technical=None)]
        assert find_unused_excel_classes(html_texts, excel_wordings) == []

    def test_results_are_sorted_and_deduplicated(self):
        html_texts = []
        excel_wordings = [
            excel_row(technical="audit-component-b"),
            excel_row(technical="audit-component-a"),
            excel_row(technical="audit-component-b"),
        ]
        assert find_unused_excel_classes(html_texts, excel_wordings) == [
            "audit-component-a",
            "audit-component-b",
        ]


class TestFindDuplicateComponents:
    def test_same_component_same_class_is_not_flagged(self):
        excel_wordings = [
            excel_row(component="HEADER", technical="audit-component-header"),
            excel_row(component="HEADER", technical="audit-component-header"),
        ]
        assert find_duplicate_components(excel_wordings) == []

    def test_same_component_different_classes_is_flagged(self):
        excel_wordings = [
            excel_row(component="NON FID SECTION", technical="audit-component-non-fid-section"),
            excel_row(component="NON FID SECTION", technical="audit-component-loyalty-section"),
        ]
        result = find_duplicate_components(excel_wordings)
        assert result == [
            ("NON FID SECTION", ["audit-component-loyalty-section", "audit-component-non-fid-section"])
        ]

    def test_rows_without_technical_are_ignored(self):
        excel_wordings = [
            excel_row(component="LEGAL NOTICE", technical=None),
            excel_row(component="LEGAL NOTICE", technical=None),
        ]
        assert find_duplicate_components(excel_wordings) == []


GERMAN_SENTENCES = [
    "Entdecke Neuheiten und Empfehlungen, die perfekt zu deinem Stil passen.",
    "Wir haben die besten Angebote für dich ausgewählt, schau sie dir jetzt an.",
    "Deine Bestellung wird in Kürze versendet, vielen Dank für deine Geduld.",
    "Sichere dir jetzt deinen Rabatt, bevor das Angebot morgen ausläuft.",
]
ITALIAN_SENTENCES = [
    "Scopri il programma fedeltà e ottieni subito i tuoi vantaggi esclusivi.",
    "Approfitta delle nostre migliori offerte selezionate apposta per te.",
]


class TestFindWrongLanguageWordings:
    def test_minority_cluster_is_flagged(self):
        excel_wordings = [excel_row(wording=s, component="A") for s in GERMAN_SENTENCES]
        excel_wordings += [excel_row(wording=s, component="B") for s in ITALIAN_SENTENCES]

        mismatches = find_wrong_language_wordings(excel_wordings)

        flagged_texts = {m["Wording"] for m in mismatches}
        assert flagged_texts == set(ITALIAN_SENTENCES)
        assert all(m["expected_lang"] == "de" for m in mismatches)
        assert all(m["detected_lang"] == "it" for m in mismatches)

    def test_all_same_language_is_not_flagged(self):
        excel_wordings = [excel_row(wording=s, component="A") for s in GERMAN_SENTENCES]
        assert find_wrong_language_wordings(excel_wordings) == []

    def test_isolated_single_occurrence_is_not_flagged(self):
        # Only one Italian sentence among many German ones: below
        # min_cluster_size, treated as noise rather than a real block.
        excel_wordings = [excel_row(wording=s, component="A") for s in GERMAN_SENTENCES]
        excel_wordings.append(excel_row(wording=ITALIAN_SENTENCES[0], component="B"))

        assert find_wrong_language_wordings(excel_wordings) == []

    def test_short_texts_are_ignored(self):
        excel_wordings = [excel_row(wording="Neu", component="A"), excel_row(wording="Hallo", component="B")]
        assert find_wrong_language_wordings(excel_wordings) == []

    def test_too_few_candidates_returns_empty(self):
        excel_wordings = [excel_row(wording=GERMAN_SENTENCES[0], component="A")]
        assert find_wrong_language_wordings(excel_wordings) == []
