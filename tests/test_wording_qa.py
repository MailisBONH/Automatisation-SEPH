from qa.config import KO_COMPONENT_CLASS_NOT_FOUND, KO_WORDING_MISMATCH
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


class TestFinalDedupCoversOkRows:
    def test_split_matched_row_is_not_also_duplicated_via_fallback(self):
        # Regression : find_split_match ne "consomme" jamais la cellule
        # Excel (par design, pour laisser d'autres fragments la matcher
        # aussi). Si personne d'autre ne la reclame, elle est ensuite
        # retrouvee "non couverte" en fin de pipeline et repromue via le
        # fallback (Shared conditional branch), ce qui pourrait produire une
        # 2e ligne identique pour le meme texte - frequent quand la classe
        # technique n'est connue ni cote Excel ni cote HTML (technical=None).
        # Classe HTML inconnue -> KO_COMPONENT_CLASS_NOT_FOUND (pas d'OK sans
        # preuve de classe), et une seule ligne, pas un doublon.
        excel_wordings = [excel_row("Twoje punkty", technical=None)]
        html_texts = [html_item("Twoje punkty", component_class=None)]

        results = run_wording_qa(html_texts, excel_wordings)

        assert len(results) == 1
        assert results[0]["Status"] == "KO"
        assert results[0]["Error Type"] == KO_COMPONENT_CLASS_NOT_FOUND


class TestLowQualityBestEffortIsHonestlyUnexpectedWording:
    """
    Regression : le "meilleur effort" de compare() (Direct/Agrege) n'a
    aucun plancher de qualite - meme un texte totalement sans rapport
    ressortait associe au wording Excel "le moins pire" trouve dans la
    meme classe technique (ex: "Twoje punkty" affiche comme "meilleur
    candidat" contre "Gold", "Nagrody"... des textes sans aucun rapport,
    juste parce qu'aucun autre wording de la classe loyalty-section ne
    matchait mieux). Sous SIM_CLOSEST_MATCH_MIN, ce rapprochement doit etre
    remplace par un KO honnete ("Unexpected wording"), sans pretendre a un
    rapport avec un wording precis.
    """

    def test_low_similarity_direct_guess_becomes_unexpected_wording(self):
        html_texts = [html_item("Gold", component_class="audit-component-loyalty")]
        excel_wordings = [excel_row("Twoje punkty", technical="audit-component-loyalty")]

        results = run_wording_qa(html_texts, excel_wordings)

        row = next(r for r in results if r["HTML Wording"] == "Gold")
        assert row["Status"] == "KO"
        assert row["Error Type"] == "Unexpected wording (not in Excel)"
        assert row["Excel Wording"] == ""

    def test_aggregation_priority_over_low_quality_direct_is_preserved(self):
        # Regression : downgrader le "meilleur effort" Direct en
        # "Unexpected wording" ne doit pas pour autant laisser
        # l'agregation "avaler" un fragment qui aurait pu matcher
        # parfaitement seul au tour suivant (voir
        # TestCascadeDoesNotLockInOnBadDirect, qui verifie deja ce
        # comportement de bout en bout).
        html_texts = [
            html_item("New", component_class="audit-component-x"),
            html_item("Discover our new arrivals today", component_class="audit-component-x"),
        ]
        excel_wordings = [excel_row("Discover our new arrivals today", technical="audit-component-x")]

        results = run_wording_qa(html_texts, excel_wordings)

        row = result_for(results, "Discover our new arrivals today")
        assert row["Status"] == "OK"
        assert row["Match Type"] == "Direct"

        unrelated = next(r for r in results if r["HTML Wording"] == "New")
        assert unrelated["Error Type"] == "Unexpected wording (not in Excel)"


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


class TestUnknownHtmlClassIsNotTreatedAsWrongComponent:
    """
    Regression : un element HTML dont la classe technique n'a pas pu etre
    detectee (ex. un titre generique dont la resolution via
    title_to_component a echoue - component_class=None) ne doit pas etre
    signale "Wording in wrong component" (ni "Wording relocated") juste
    parce que None != la classe Excel attendue - on ne sait pas que le
    composant est faux, on sait juste que sa classe est inconnue. Mais on ne
    peut pas non plus le confirmer OK sans preuve : ce cas doit ressortir en
    KO dedie (KO_COMPONENT_CLASS_NOT_FOUND), meme quand le texte matche
    parfaitement.
    """

    def test_perfect_text_match_with_unknown_html_class_is_ko_component_not_found(self):
        html_texts = [html_item("Korzyści programu", component_class=None)]
        excel_wordings = [excel_row("Korzyści programu", technical="audit-component-program-benefit")]

        results = run_wording_qa(html_texts, excel_wordings)

        # find_split_match ne "consomme" pas la cellule Excel (par design,
        # voir son docstring), donc ce meme texte identique peut aussi etre
        # re-matche via le fallback "Excel non couverts" ; peu importe le
        # nombre exact de lignes produites, toutes doivent porter le meme
        # KO dedie a la classe manquante, jamais "wrong component"/"relocated".
        assert results
        assert all(r["Status"] == "KO" for r in results)
        assert all(r["Error Type"] == KO_COMPONENT_CLASS_NOT_FOUND for r in results)
        assert all(r["HTML Class"] == "none" for r in results)

    def test_real_mismatch_with_unknown_html_class_still_ko_as_mismatch_not_wrong_component(self):
        # Classe inconnue -> pas de "wrong component", mais un texte vraiment
        # different reste KO (juge sur la similarite, comme d'habitude).
        html_texts = [html_item("Something totally unrelated", component_class=None)]
        excel_wordings = [excel_row("Korzyści programu", technical="audit-component-program-benefit")]

        results = run_wording_qa(html_texts, excel_wordings)

        row = next(r for r in results if r["HTML Wording"] == "Something totally unrelated")
        assert row["Status"] == "KO"
        assert row["Error Type"] != "Wording in wrong component"

    def test_known_mismatched_classes_are_still_ko_never_falsely_ok(self):
        # Les deux classes SONT connues et different reellement : ce texte
        # ne doit jamais ressortir OK par erreur. (find_closest_match filtre
        # deja les candidats de classe differente pendant sa recherche
        # interne, donc ce cas remonte via "Unexpected wording"/"Wording
        # relocated" plutot que via "Wording in wrong component" - qui, une
        # fois ce fix applique, ne peut plus se produire que pour une classe
        # HTML inconnue suivie d'une reconciliation ailleurs, jamais pour
        # deux classes connues-et-differentes.)
        html_texts = [html_item("Korzyści programu", component_class="audit-component-banner-right")]
        excel_wordings = [excel_row("Korzyści programu", technical="audit-component-program-benefit")]

        results = run_wording_qa(html_texts, excel_wordings)

        assert results
        assert all(r["Status"] == "KO" for r in results)

    def test_duplicate_wording_across_merged_sheets_with_unknown_class_is_ko_component_not_found(self):
        # Cas reel : meme wording present a l'identique dans l'onglet
        # sequence et l'onglet frere LOYAL/NON LOYAL fusionne (voir
        # qa.excel_loader.find_sibling_sequence_sheet), et l'element HTML
        # n'a pas de classe detectee. Un texte identique cote Excel et cote
        # HTML ne doit jamais ressortir en "wrong component"/"wording
        # relocated" (des faux-negatifs precis, differents de la classe
        # simplement inconnue), et ne doit pas etre duplique - mais doit
        # bien rester KO_COMPONENT_CLASS_NOT_FOUND, peu importe combien de
        # lignes Excel identiques y pointent.
        html_texts = [html_item("Korzyści programu", component_class=None)]
        excel_wordings = [
            excel_row("Korzyści programu", technical="audit-component-program-benefit"),
            excel_row("Korzyści programu", technical="audit-component-program-benefit"),
        ]

        results = run_wording_qa(html_texts, excel_wordings)

        assert results
        assert all(r["Status"] == "KO" for r in results)
        assert all(r["Error Type"] == KO_COMPONENT_CLASS_NOT_FOUND for r in results)


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


class TestAggregatedMatch:
    def test_two_consecutive_blocks_aggregate_into_a_single_ok_match(self):
        # Bloc B (find_aggregated_match) verrouille en OK des que la
        # concatenation de N blocs HTML consecutifs egale une cellule Excel,
        # meme si aucun des deux blocs pris seul n'y correspond.
        html_texts = [
            html_item("Discover our", component_class="audit-component-hero"),
            html_item("new arrivals today", component_class="audit-component-hero"),
        ]
        excel_wordings = [excel_row("Discover our new arrivals today", technical="audit-component-hero")]

        results = run_wording_qa(html_texts, excel_wordings)

        row = result_for(results, "Discover our new arrivals today")
        assert row["Status"] == "OK"
        assert row["Match Type"] == "Agrege (2 blocs)"


class TestBestEffortFallback:
    """
    Quand Direct/Agregation/Split ne trouvent aucun OK, la cascade retombe
    sur le meilleur candidat KO deja trouve (voir _best_effort_row) plutot
    que de sauter directement au closest-match generique - a condition que
    sa similarite reste au-dessus de SIM_CLOSEST_MATCH_MIN (sinon
    _unexpected_wording_row, voir TestCascadeDoesNotLockInOnBadDirect et
    consorts pour ce seuil).
    """

    def test_direct_candidate_below_ok_but_above_min_is_reported_as_is(self):
        html_texts = [html_item("Discover our new arrivals today", component_class="audit-component-hero")]
        excel_wordings = [excel_row("Discover our latest deals today", technical="audit-component-hero")]

        results = run_wording_qa(html_texts, excel_wordings)

        row = result_for(results, "Discover our new arrivals today")
        assert row["Status"] == "KO"
        assert row["Match Type"] == "Direct"
        assert row["Excel Wording"] == "Discover our latest deals today"

    # Pas d'equivalent "aggregation" pour le test ci-dessus : direct et le
    # premier essai d'agregation partagent la MEME condition de succes
    # (find_best_match sur la classe de item/first_item - un seul et meme
    # found_class dans les deux cas), donc direct est toujours truthy des
    # que l'agregation pourrait l'etre - le bloc "if agg:" (fallback KO
    # d'agregation) ne peut alors jamais s'executer, "if direct:" l'ayant
    # deja intercepte juste avant. Verifie par une tentative de test qui
    # echouait systematiquement en pratique - laisse volontairement sans
    # test plutot que d'en forcer un artificiel sur un chemin mort.


class TestClosestMatchRow:
    """
    Bloc D (_closest_match_row) : atteint uniquement quand Direct/Agregation/
    Split n'ont RIEN trouve du tout (pas meme un KO) pour ce fragment -
    typiquement quand aucun wording Excel ne partage la classe technique du
    HTML. find_closest_match exclut deja tout candidat de classe connue et
    differente de sa recherche interne (voir sa docstring et
    TestKnownMismatchedClassesNeverFalselyOk plus haut) : le cas
    "class_mismatch" de _closest_match_row n'est donc atteignable que par
    appel direct de la fonction, jamais via ce pipeline - pas teste ici.
    """

    def test_unverifiable_component_is_ko_even_with_near_perfect_text(self):
        # Classe HTML inconnue : meme un texte quasi-identique au wording
        # Excel ne peut pas etre valide OK, faute de pouvoir confirmer le
        # composant.
        html_texts = [html_item("Discover our new arrivals today", component_class=None)]
        excel_wordings = [excel_row("Discover our new arrival today", technical="audit-component-hero")]

        results = run_wording_qa(html_texts, excel_wordings)

        row = result_for(results, "Discover our new arrivals today")
        assert row["Status"] == "KO"
        assert row["Error Type"] == KO_COMPONENT_CLASS_NOT_FOUND
        assert row["Match Type"] == "Closest Match"

    def test_similarity_at_or_above_accept_threshold_is_ok(self):
        html_texts = [html_item("Discover our new arrivals today", component_class="audit-component-hero")]
        excel_wordings = [excel_row("Discover our new arrivals soon", technical=None)]

        results = run_wording_qa(html_texts, excel_wordings)

        row = result_for(results, "Discover our new arrivals today")
        assert row["Status"] == "OK"
        assert row["Match Type"] == "Closest Match Accepted"

    def test_similarity_below_accept_threshold_is_wording_mismatch(self):
        html_texts = [html_item("Discover our new arrivals today", component_class="audit-component-hero")]
        excel_wordings = [excel_row("Discover our latest deals today", technical=None)]

        results = run_wording_qa(html_texts, excel_wordings)

        row = result_for(results, "Discover our new arrivals today")
        assert row["Status"] == "KO"
        assert row["Error Type"] == KO_WORDING_MISMATCH
        assert row["Match Type"] == "Closest Match"


class TestDebugModeGatesSummaryPrint:
    """Le resume console Total/OK/KO/Score (deja affiche par ailleurs dans
    l'UI via st.metric, voir home.py) doit se taire quand DEBUG_MODE est
    desactive - regression test pour l'incoherence corrigee (qa.html_extraction
    respectait deja ce flag, qa.wording_qa affichait toujours son resume)."""

    def test_summary_is_not_printed_when_debug_mode_is_disabled(self, monkeypatch, capsys):
        import qa.wording_qa as wording_qa_module
        monkeypatch.setattr(wording_qa_module, "DEBUG_MODE", False)

        run_wording_qa(
            [html_item("Hallo", component_class="audit-component-a")],
            [excel_row("Hallo", technical="audit-component-a")],
        )

        assert capsys.readouterr().out == ""

    def test_summary_is_printed_when_debug_mode_is_enabled(self, monkeypatch, capsys):
        import qa.wording_qa as wording_qa_module
        monkeypatch.setattr(wording_qa_module, "DEBUG_MODE", True)

        run_wording_qa(
            [html_item("Hallo", component_class="audit-component-a")],
            [excel_row("Hallo", technical="audit-component-a")],
        )

        assert "Total:" in capsys.readouterr().out
