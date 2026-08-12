from qa.text_utils import normalize, normalize_currency
from qa.wording_qa import run_wording_qa


def excel_row(wording, technical=None, component="Comp", element="Elem"):
    return {
        "Component": component,
        "Element": element,
        "Wording": wording,
        "Technical": technical,
        "norm": normalize(wording),
        "curr": normalize_currency(wording),
    }


def html_item(text, component_class=None, tag="p", type_="Paragraph"):
    return {"text": text, "tag": tag, "type": type_, "component_class": component_class}


def result_for(results, html_text):
    matches = [r for r in results if r.get("HTML Wording") == html_text]
    assert matches, f"no result row for HTML text {html_text!r}"
    return matches[0]


class TestCascadeDoesNotLockInOnBadDirect:
    def test_short_fragment_recovers_via_split_instead_of_bad_direct(self):
        # Regression: a same-class Direct candidate that scores KO used to
        # be locked in immediately, even when a later html item genuinely
        # equals that Excel cell and a smaller fragment could otherwise have
        # been left for Split. Here "Discover our new arrivals today" should
        # land OK via Direct, independent of any other item's failure.
        html_texts = [
            html_item("New", component_class="audit-component-x"),
            html_item("Discover our new arrivals today", component_class="audit-component-x"),
        ]
        excel_wordings = [excel_row("Discover our new arrivals today", technical="audit-component-x")]

        results = run_wording_qa(html_texts, excel_wordings)

        row = result_for(results, "Discover our new arrivals today")
        assert row["Status"] == "OK"
        assert row["Match Type"] == "Direct"


class TestKoFallbackDoesNotStarveSplit:
    def test_ko_fragment_does_not_block_later_split_match_on_same_cell(self):
        # Regression: once an unrelated short fragment fell back to a KO
        # Direct match against an Excel cell, matched_excel_counts recorded
        # that cell as "claimed", and find_split_match's exclusion check
        # then skipped it for a later, genuinely good-containment fragment.
        excel_wordings = [
            excel_row(
                "Contact us Imprint Privacy policy for full details about how we handle your personal data",
                technical="audit-component-footer",
            )
        ]
        html_texts = [
            # Short, same-class fragment: will KO-fallback against the cell above.
            html_item("Imprint", component_class="audit-component-footer"),
            # Large fragment genuinely contained in the same cell (~78% length ratio).
            html_item(
                "Contact us Imprint Privacy policy for full details about how we handle your personal",
                component_class="audit-component-footer",
            ),
        ]

        results = run_wording_qa(html_texts, excel_wordings)

        big_fragment = result_for(
            results,
            "Contact us Imprint Privacy policy for full details about how we handle your personal",
        )
        assert big_fragment["Status"] == "OK"
        assert big_fragment["Match Type"] == "Split (HTML ⊂ Excel)"


class TestBasicWordingQa:
    def test_perfect_match_end_to_end(self):
        html_texts = [html_item("Shop now", component_class="audit-component-cta")]
        excel_wordings = [excel_row("Shop now", technical="audit-component-cta")]

        results = run_wording_qa(html_texts, excel_wordings)

        assert len(results) == 1
        assert results[0]["Status"] == "OK"

    def test_excel_wording_never_found_in_html_is_reported(self):
        html_texts = [html_item("Shop now", component_class="audit-component-cta")]
        excel_wordings = [
            excel_row("Shop now", technical="audit-component-cta"),
            excel_row("Discover the new collection", technical="audit-component-hero"),
        ]

        results = run_wording_qa(html_texts, excel_wordings)

        missing = [r for r in results if r["Excel Wording"] == "Discover the new collection"]
        assert len(missing) == 1
        assert missing[0]["Status"] == "KO"
