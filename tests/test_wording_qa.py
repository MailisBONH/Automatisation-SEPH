from qa.text_utils import expand_conditional_variants, normalize, normalize_currency
from qa.wording_qa import _drop_ko_covered_by_ok, run_wording_qa


def excel_row(wording, technical=None, component="Comp", element="Elem"):
    variants = expand_conditional_variants(wording)
    return {
        "Component": component,
        "Element": element,
        "Wording": wording,
        "Technical": technical,
        "norm": normalize(variants[0]),
        "curr": normalize_currency(variants[0]),
        "norm_variants": [normalize(v) for v in variants],
        "curr_variants": [normalize_currency(v) for v in variants],
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


class TestDropKoCoveredByOk:
    def test_ko_fragment_contained_in_ok_html_is_dropped(self):
        results = [
            {"Status": "OK", "HTML Wording": "Contact us for more information about our privacy policy"},
            {"Status": "KO", "HTML Wording": "Contact us", "Excel Wording": "x"},
        ]
        filtered = _drop_ko_covered_by_ok(results)
        assert filtered == [results[0]]

    def test_unrelated_ko_is_kept(self):
        results = [
            {"Status": "OK", "HTML Wording": "Contact us for more information"},
            {"Status": "KO", "HTML Wording": "Totally unrelated text", "Excel Wording": "x"},
        ]
        filtered = _drop_ko_covered_by_ok(results)
        assert results[1] in filtered

    def test_very_short_ko_fragment_is_kept_despite_containment(self):
        # Below min_length: too easy to coincide by chance, kept as a real KO.
        results = [
            {"Status": "OK", "HTML Wording": "The cat sat on the mat today"},
            {"Status": "KO", "HTML Wording": "cat", "Excel Wording": "x"},
        ]
        filtered = _drop_ko_covered_by_ok(results, min_length=5)
        assert results[1] in filtered

    def test_end_to_end_short_link_covered_by_matched_paragraph_is_dropped(self):
        excel_wordings = [
            excel_row(
                "Contact us at support@example.com for any question about your order",
                technical="audit-component-footer",
            )
        ]
        html_texts = [
            html_item("support@example.com", component_class="audit-component-footer"),
            html_item(
                "Contact us at support@example.com for any question about your order",
                component_class="audit-component-footer",
            ),
        ]

        results = run_wording_qa(html_texts, excel_wordings)

        assert not any(r.get("HTML Wording") == "support@example.com" for r in results)
        assert result_for(results, "Contact us at support@example.com for any question about your order")[
            "Status"
        ] == "OK"


class TestConditionalBranchesDisplayAndMatching:
    """
    Regression : un wording HTML avec un bloc AEM <% if %>...<% } else { %>
    ...<% } %> doit matcher correctement (peu importe la branche affichee au
    rendu), et le rapport ne doit jamais montrer de code AEM brut (variables
    <%= %>, syntaxe <% if %>) dans les colonnes Excel/HTML Wording.
    """

    def test_matched_row_never_shows_raw_aem_code(self):
        html_texts = [html_item(
            "<% if(n>1 ) { %>Dir fehlen noch <%=n%> Punkte<% } else { %>Dir fehlt noch <%=n%> Punkt<% } %> für dein Upgrade",
            component_class="audit-component-loyalty",
        )]
        excel_wordings = [excel_row(
            "Dir fehlen noch [XX] Punkte für dein Upgrade", technical="audit-component-loyalty"
        )]

        results = run_wording_qa(html_texts, excel_wordings)

        assert len(results) == 1
        assert results[0]["Status"] == "OK"
        assert "<%" not in results[0]["HTML Wording"]
        assert "<%" not in results[0]["Excel Wording"]
        assert results[0]["HTML Wording"] == "Dir fehlen noch Punkte für dein Upgrade"

    def test_excel_modeled_as_two_rows_both_resolve_ok_via_shared_fragment(self):
        # L'Excel modelise les 2 branches d'un meme if/else en 2 lignes
        # separees (courant pour faciliter la traduction), alors qu'un seul
        # fragment HTML "partage" satisfait les deux : la branche non
        # "consommee" en premier (ici le singulier) ne doit pas ressortir en
        # KO, son texte existe bel et bien dans le fragment deja valide.
        html_texts = [html_item(
            "<% if(n>1 ) { %>Deine offenen Geschenke<% } else { %>Dein offenes Geschenk<% } %>",
            component_class="audit-component-loyalty",
        )]
        excel_wordings = [
            excel_row("Deine offenen Geschenke", technical="audit-component-loyalty"),
            excel_row("Dein offenes Geschenk", technical="audit-component-loyalty"),
        ]

        results = run_wording_qa(html_texts, excel_wordings)

        assert len(results) == 2
        assert all(r["Status"] == "OK" for r in results)
        singular = next(r for r in results if r["Excel Wording"] == "Dein offenes Geschenk")
        assert singular["Match Type"] == "Shared conditional branch"

    def test_real_mismatch_affecting_both_branches_is_not_masked_by_sibling_promotion(self):
        # Regression : un ":" en trop cote HTML (absent de l'Excel) affecte
        # LES DEUX branches -> les deux doivent rester KO. Le seuil de
        # promotion "branche soeur" doit etre volontairement quasi-parfait
        # (SIM_SHARED_BRANCH_EXACT) : un 98% de similarite reflete un vrai
        # ecart, pas juste une branche non consommee, meme pour la branche
        # qui n'a pas ete evaluee en premier par le matching direct.
        html_texts = [html_item(
            "<% if(n>1 ) { %>Deine offenen Geschenke<% } else { %>Dein offenes Geschenk<% } %>:",
            component_class="audit-component-loyalty",
        )]
        excel_wordings = [
            excel_row("Deine offenen Geschenke", technical="audit-component-loyalty"),
            excel_row("Dein offenes Geschenk", technical="audit-component-loyalty"),
        ]

        results = run_wording_qa(html_texts, excel_wordings)

        plural_rows = [r for r in results if r["Excel Wording"] == "Deine offenen Geschenke"]
        assert len(plural_rows) == 1
        assert plural_rows[0]["Status"] == "KO"

        singular_rows = [r for r in results if r["Excel Wording"] == "Dein offenes Geschenk"]
        assert len(singular_rows) == 1
        assert singular_rows[0]["Status"] == "KO"

    def test_shared_branch_promotion_requires_near_perfect_match(self):
        # Le seuil de promotion est SIM_SHARED_BRANCH_EXACT (quasi 100%), pas
        # SIM_CLOSEST_MATCH_ACCEPT (85%) : une branche "assez proche" mais
        # pas exacte doit rester KO, pas etre masquee par la logique
        # "branche soeur".
        html_texts = [html_item(
            "<% if(n>1 ) { %>Deine offenen Geschenke<% } else { %>Dein offenes Geschenk ungefahr<% } %>",
            component_class="audit-component-loyalty",
        )]
        excel_wordings = [
            excel_row("Deine offenen Geschenke", technical="audit-component-loyalty"),
            excel_row("Dein offenes Geschenk", technical="audit-component-loyalty"),
        ]

        results = run_wording_qa(html_texts, excel_wordings)

        singular_rows = [r for r in results if r["Excel Wording"] == "Dein offenes Geschenk"]
        assert len(singular_rows) == 1
        assert singular_rows[0]["Status"] == "KO"

    def test_sibling_promotion_requires_matching_technical_class(self):
        # Un wording "orphelin" ne doit pas etre repromu en OK juste parce
        # qu'un texte similaire existe ailleurs dans une AUTRE classe.
        html_texts = [
            html_item("Dein offenes Geschenk", component_class="audit-component-footer"),
        ]
        excel_wordings = [
            excel_row("Dein offenes Geschenk", technical="audit-component-loyalty"),
        ]

        results = run_wording_qa(html_texts, excel_wordings)

        loyalty_row = next(r for r in results if r["Excel Wording"] == "Dein offenes Geschenk")
        assert loyalty_row["Status"] == "KO"


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
