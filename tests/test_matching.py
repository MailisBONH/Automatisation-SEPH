from qa.matching import (
    compare,
    find_best_match,
    find_closest_match,
    find_split_match,
    index_wordings_by_technical_class,
)


def excel_row(wording, technical=None, component="Comp", element="Elem"):
    from qa.text_utils import expand_conditional_variants, normalize, normalize_currency

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


class TestCompare:
    def test_exact_match_is_ok(self):
        status, error, sim, match_type = compare("Discover now", "Discover now")
        assert status == "OK"
        assert sim == 100

    def test_case_only_difference_is_ko(self):
        status, error, sim, match_type = compare("discover now", "Discover Now")
        assert status == "KO"
        assert error == "Case mismatch"

    def test_near_exact_is_minor_typo(self):
        # >=95% similarity (single extra letter on a longer string).
        status, error, sim, match_type = compare("Discover now and saves", "Discover now and save")
        assert status == "KO"
        assert error == "Minor typo"

    def test_fuzzy_is_fuzzy_mismatch(self):
        # 80-95% similarity (noticeable but recognizable difference).
        status, error, sim, match_type = compare("Discover now!", "Discover now.")
        assert status == "KO"
        assert error == "Fuzzy mismatch"

    def test_very_different_text_is_content_mismatch(self):
        status, error, sim, match_type = compare("Discover now", "Buy shoes today")
        assert status == "KO"
        assert error == "Content mismatch"

    def test_both_empty_is_ok(self):
        status, error, sim, match_type = compare("", "")
        assert status == "OK"

    def test_one_empty_is_ko(self):
        status, error, sim, match_type = compare("Discover now", "")
        assert status == "KO"
        assert error == "Text missing"


class TestCompareWithConditionals:
    """
    Regression : le rendu final d'un bloc AEM <% if %>...<% } else { %>...<% } %>
    n'affiche jamais qu'une seule branche a la fois. Avant, normalize()
    concatenait les deux branches en une phrase incoherente ("Dir fehlen
    noch PunkteDir fehlt noch Punkt...") qui ne matchait ni l'une ni
    l'autre. compare() doit maintenant reconnaitre un match sur N'IMPORTE
    LAQUELLE des branches possibles.
    """

    HTML_UPGRADE_TEXT = (
        "<% if(recipient.counter.upgrade.upgraderemainingpoints>1 ) { %>"
        "Dir fehlen noch <%=recipient.counter.upgrade.upgraderemainingpoints%> Punkte"
        "<% } else { %>"
        "Dir fehlt noch <%=recipient.counter.upgrade.upgraderemainingpoints%> Punkt"
        "<% } %> für dein Gold Upgrade"
    )

    def test_matches_excel_wording_containing_only_the_plural_branch(self):
        status, error, sim, match_type = compare(
            self.HTML_UPGRADE_TEXT, "Dir fehlen noch [X] Punkte für dein Gold Upgrade"
        )
        assert status == "OK"

    def test_matches_excel_wording_containing_only_the_singular_branch(self):
        status, error, sim, match_type = compare(
            self.HTML_UPGRADE_TEXT, "Dir fehlt noch [X] Punkt für dein Gold Upgrade"
        )
        assert status == "OK"

    def test_matches_excel_wording_that_also_contains_the_raw_conditional(self):
        # Le cas ou l'Excel a ete rempli en copiant le if/else brut du HTML
        # (traduit dans chaque branche) doit aussi matcher.
        html = "<% if(n>1){%>Deine offenen Geschenke<%}else{%>Dein offenes Geschenk<%}%>"
        excel = "<% if(n>1){%>Deine offenen Geschenke<%}else{%>Dein offenes Geschenk<%}%>"
        status, error, sim, match_type = compare(html, excel)
        assert status == "OK"

    def test_still_ko_when_neither_branch_matches(self):
        status, error, sim, match_type = compare(self.HTML_UPGRADE_TEXT, "Something else entirely")
        assert status == "KO"


class TestFindBestMatch:
    def test_matches_only_same_technical_class(self):
        wordings = [
            excel_row("Discover now", technical="audit-component-a"),
            excel_row("Discover now", technical="audit-component-b"),
        ]
        item = html_item("Discover now", component_class="audit-component-b")
        result = find_best_match(item, wordings, set())
        assert result is not None
        idx, status, error, sim = result
        assert idx == 1
        assert status == "OK"

    def test_no_candidate_of_matching_class_returns_none(self):
        wordings = [excel_row("Discover now", technical="audit-component-a")]
        item = html_item("Discover now", component_class="audit-component-b")
        assert find_best_match(item, wordings, set()) is None

    def test_two_unknown_classes_are_not_treated_as_a_matching_pair(self):
        # Regression : "None != None" est faux, donc sans verification
        # explicite que les DEUX classes sont connues, un element HTML sans
        # classe detectee (component_class=None) et un wording Excel sans
        # classe technique renseignee (Technical=None) etaient a tort
        # traites comme "la meme classe" et compares, produisant un
        # "meilleur effort" KO arbitraire contre un texte totalement sans
        # rapport (voir l'onglet loyalty-section polonais dans la conv.).
        wordings = [excel_row("Completely unrelated wording here", technical=None)]
        item = html_item("Totally different text", component_class=None)
        assert find_best_match(item, wordings, set()) is None

    def test_class_index_gives_the_same_result_as_a_full_scan(self):
        # index_wordings_by_technical_class (voir qa.wording_qa.run_wording_qa)
        # ne doit etre qu'une optimisation de vitesse - jamais changer le
        # resultat par rapport au parcours complet (class_index=None).
        wordings = [
            excel_row("Discover now", technical="audit-component-a"),
            excel_row("Discover now", technical="audit-component-b"),
            excel_row("Unrelated wording", technical=None),
        ]
        item = html_item("Discover now", component_class="audit-component-b")
        class_index = index_wordings_by_technical_class(wordings)

        assert find_best_match(item, wordings, set(), class_index) == find_best_match(item, wordings, set())

    def test_class_index_returns_none_for_a_class_with_no_wordings(self):
        wordings = [excel_row("Discover now", technical="audit-component-a")]
        item = html_item("Discover now", component_class="audit-component-b")
        class_index = index_wordings_by_technical_class(wordings)

        assert find_best_match(item, wordings, set(), class_index) is None

    def test_missing_html_class_returns_none_without_needing_the_index(self):
        wordings = [excel_row("Discover now", technical="audit-component-a")]
        item = html_item("Discover now", component_class=None)

        assert find_best_match(item, wordings, set(), index_wordings_by_technical_class(wordings)) is None


class TestIndexWordingsByTechnicalClass:
    def test_groups_indices_by_technical_class(self):
        wordings = [
            excel_row("A", technical="audit-component-a"),
            excel_row("B", technical="audit-component-b"),
            excel_row("C", technical="audit-component-a"),
        ]
        index = index_wordings_by_technical_class(wordings)
        assert index == {"audit-component-a": [0, 2], "audit-component-b": [1]}

    def test_wordings_without_a_technical_class_are_not_indexed(self):
        wordings = [excel_row("A", technical=None), excel_row("B", technical="")]
        assert index_wordings_by_technical_class(wordings) == {}


class TestFindSplitMatch:
    def test_short_fragment_below_threshold_is_rejected(self):
        # Regression: "Unlimited" (9 chars) inside a ~27-char cell used to be
        # accepted as an OK match at a 30% length ratio, with the similarity
        # reported as a raw 0-1 ratio ("0.33%") instead of a percentage.
        wordings = [excel_row("Entdecke Sephora Unlimited:", technical="audit-component-loyalty-section")]
        item = html_item("Unlimited", component_class="audit-component-loyalty-section")
        assert find_split_match(item, wordings, set()) is None

    def test_large_fragment_above_threshold_is_accepted_with_percentage_similarity(self):
        wordings = [excel_row("Get a gift before the offer ends", technical="audit-component-x")]
        # Same text minus a short trailing placeholder -> high length ratio.
        item = html_item("Get a gift before the offer", component_class="audit-component-x")
        result = find_split_match(item, wordings, set())
        assert result is not None
        idx, status, error, sim = result
        assert status == "OK"
        assert 0 < sim <= 100  # a real percentage, not a 0-1 fraction

    def test_rejects_coincidental_match_in_wrong_component(self):
        # A short fragment that happens to appear inside a cell belonging to
        # a completely different, known component should not match.
        wordings = [
            excel_row(
                "Clean at Sephora: formulations with carefully selected ingredients across the whole range",
                technical="audit-component-footer",
            )
        ]
        item = html_item("Clean at", component_class="audit-component-axis")
        assert find_split_match(item, wordings, set()) is None

    def test_allows_match_when_html_class_unknown(self):
        # When the HTML side has no detected class (no audit-component-*
        # wrapper), the class guard must not block a legitimate high-ratio
        # containment match.
        wordings = [excel_row("Exclusive offers available all year for members", technical="audit-component-program-benefit")]
        item = html_item("Exclusive offers available all year for", component_class=None)
        result = find_split_match(item, wordings, set())
        assert result is not None
        assert result[1] == "OK"

    def test_skips_already_matched_excel_index(self):
        wordings = [excel_row("Get a gift before the offer ends", technical="audit-component-x")]
        item = html_item("Get a gift before the offer", component_class="audit-component-x")
        assert find_split_match(item, wordings, {0}) is None


class TestFindClosestMatch:
    def test_high_similarity_same_class_is_accepted(self):
        wordings = [excel_row("Discover our new arrivals today", technical="audit-component-x")]
        item = html_item("Discover our new arrival today", component_class="audit-component-x")
        result = find_closest_match(item, wordings, set())
        assert result is not None
        assert result["idx"] == 0

    def test_below_min_sim_returns_none(self):
        wordings = [excel_row("Completely unrelated content here", technical="audit-component-x")]
        item = html_item("Totally different words", component_class="audit-component-x")
        assert find_closest_match(item, wordings, set()) is None

    def test_matches_the_closest_branch_of_a_conditional_excel_wording(self):
        # Le wording Excel contient le if/else brut (traduit dans chaque
        # branche) ; le HTML ne montre que la branche singulier au rendu.
        wordings = [excel_row(
            "<% if(n>1){%>Deine offenen Geschenke sind toll<%}else{%>Dein offenes Geschenk ist toll<%}%>",
            technical="audit-component-x",
        )]
        item = html_item("Dein offenes Geschenk ist toll", component_class="audit-component-x")
        result = find_closest_match(item, wordings, set())
        assert result is not None
        assert result["similarity"] >= 0.99
