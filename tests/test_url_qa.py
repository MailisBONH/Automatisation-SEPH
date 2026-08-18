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


class TestAemConditionalUrls:
    """
    Comme le wording, une URL (cote Excel ou cote HTML) peut contenir un
    bloc conditionnel AEM qui bascule d'une destination a l'autre selon
    l'audience - le rendu final n'affiche jamais qu'une seule branche a la
    fois. N'importe quelle branche doit compter comme un match, comme pour
    qa.matching.compare() cote wording.
    """

    def test_excel_side_conditional_matches_the_rendered_html_branch(self):
        excel_url = "https://example.com/?utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>"
        html = '<a href="https://example.com/?utm_content=nonfid">Shop</a>'

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert len(results) == 1
        assert results[0]["Status"] == "OK"
        assert results[0]["Match Type"] == "Exact"

    def test_excel_side_conditional_matches_the_other_rendered_branch_too(self):
        excel_url = "https://example.com/?utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>"
        html = '<a href="https://example.com/?utm_content=fid">Shop</a>'

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "OK"

    def test_html_side_conditional_matches_a_plain_excel_url(self):
        # Cas symetrique (moins courant) : le href brut porte encore la
        # syntaxe conditionnelle non resolue.
        html = '<a href="https://example.com/?utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>">Shop</a>'

        results = run_url_qa(html, [excel_entry("https://example.com/?utm_content=fid")])

        assert results[0]["Status"] == "OK"

    def test_conditional_branch_still_functional_match_when_params_reordered(self):
        excel_url = "https://example.com/?utm_source=x&utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>"
        html = '<a href="https://example.com/?utm_content=fid&utm_source=x">Shop</a>'

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "KO"
        assert results[0]["Match Type"] == "Functional match only"

    def test_conditional_url_genuinely_not_rendered_is_still_not_found(self):
        excel_url = "https://example.com/?utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>"
        html = '<a href="https://example.com/totally-unrelated">Shop</a>'

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "KO"
        assert results[0]["Match Type"] == "No match"


class TestMailtoLinksAreIgnored:
    """
    Les liens mailto: (adresse de contact/support) ne sont jamais destines
    a etre traces dans le referentiel UTM - ils doivent etre completement
    ignores, pas signales comme "Unexpected URL (not in Excel)".
    """

    def test_mailto_link_is_not_flagged_as_unexpected(self):
        html = '<a href="mailto:support@example.com">Contact</a>'
        results = run_url_qa(html, [])
        assert results == []

    def test_mailto_link_alongside_a_real_unexpected_link(self):
        html = (
            '<a href="mailto:support@example.com">Contact</a>'
            '<a href="https://example.com/never-referenced">Untracked</a>'
        )
        results = run_url_qa(html, [])
        assert len(results) == 1
        assert results[0]["HTML Wording"] == "https://example.com/never-referenced"

    def test_mailto_is_case_insensitive(self):
        html = '<a href="MAILTO:support@example.com">Contact</a>'
        results = run_url_qa(html, [])
        assert results == []
