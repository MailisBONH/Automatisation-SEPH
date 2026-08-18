from qa.config import KO_UNEXPECTED_URL, KO_URL_NOT_FOUND
from qa.url_qa import run_url_qa


def excel_entry(url, label=""):
    return {"label": label, "url": url}


class TestExcelUrlPresentInHtml:
    def test_exact_match_is_ok(self):
        html = '<a href="https://example.com/?utm_source=x">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/?utm_source=x")])
        assert len(results) == 1
        assert results[0]["Status"] == "OK"
        assert results[0]["Match Type"] == "Exact"

    def test_missing_excel_url_is_ko_not_found(self):
        # Le lien HTML present ("other") ne matche pas non plus l'URL Excel
        # attendue : il ressort donc separement en KO_UNEXPECTED_URL (voir
        # TestUnexpectedHtmlUrlNotInExcel) - les deux KO coexistent, chacun
        # signalant un ecart different.
        html = '<a href="https://example.com/other">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/?utm_source=x")])
        not_found = [r for r in results if r["Error Type"] == KO_URL_NOT_FOUND]
        assert len(not_found) == 1
        assert not_found[0]["Status"] == "KO"


class TestUnexpectedHtmlUrlNotInExcel:
    """
    Regression : le mode URL ne verifiait auparavant que "l'URL Excel est-elle
    dans le HTML", jamais l'inverse. run_url_qa doit maintenant aussi
    signaler tout lien HTML qui n'a matche aucune URL Excel (ni exactement,
    ni fonctionnellement).
    """

    def test_html_link_absent_from_excel_is_flagged(self):
        html = (
            '<a href="https://example.com/?utm_source=x">Shop</a>'
            '<a href="https://example.com/never-referenced">Untracked</a>'
        )
        results = run_url_qa(html, [excel_entry("https://example.com/?utm_source=x")])

        unexpected = [r for r in results if r["Error Type"] == KO_UNEXPECTED_URL]
        assert len(unexpected) == 1
        assert unexpected[0]["Status"] == "KO"
        assert unexpected[0]["HTML Wording"] == "https://example.com/never-referenced"
        assert unexpected[0]["Excel Wording"] == ""

    def test_link_matched_exactly_is_not_also_flagged_as_unexpected(self):
        html = '<a href="https://example.com/?utm_source=x">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/?utm_source=x")])
        assert not any(r["Error Type"] == KO_UNEXPECTED_URL for r in results)

    def test_link_matched_functionally_is_not_also_flagged_as_unexpected(self):
        # Meme URL, ordre des parametres UTM different -> "functional match",
        # pas "exact" ; ce lien ne doit pas en plus ressortir comme "absent
        # de l'Excel" alors qu'il a bien ete rapproche d'une ligne Excel.
        html = '<a href="https://example.com/?utm_medium=y&utm_source=x">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/?utm_source=x&utm_medium=y")])
        assert not any(r["Error Type"] == KO_UNEXPECTED_URL for r in results)

    def test_repeated_unmatched_link_is_reported_only_once(self):
        # Meme lien non reference repete 3 fois dans le HTML (ex: logo en
        # header et en footer) -> une seule ligne, pas 3 doublons.
        html = (
            '<a href="https://example.com/never-referenced">A</a>'
            '<a href="https://example.com/never-referenced">B</a>'
            '<a href="https://example.com/never-referenced">C</a>'
        )
        results = run_url_qa(html, [])
        unexpected = [r for r in results if r["Error Type"] == KO_UNEXPECTED_URL]
        assert len(unexpected) == 1

    def test_no_excel_urls_at_all_still_flags_every_distinct_html_link(self):
        html = (
            '<a href="https://example.com/a">A</a>'
            '<a href="https://example.com/b">B</a>'
        )
        results = run_url_qa(html, [])
        assert len(results) == 2
        assert all(r["Error Type"] == KO_UNEXPECTED_URL for r in results)
