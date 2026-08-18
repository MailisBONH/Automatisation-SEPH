from qa.config import KO_UNEXPECTED_URL, KO_URL_NOT_FOUND, KO_URL_WRONG_COMPONENT
from qa.url_qa import run_url_qa


def excel_entry(url, label="", section=""):
    return {"label": label, "url": url, "Section": section}


def link_with_class(href, cls="audit-component-cta"):
    """Un <a href> avec un ancetre audit-component-*, pour les tests qui ne
    portent pas sur la verification de classe elle-meme (voir
    TestClassMismatchBecomesKo) - sans ca, l'absence de classe declenche
    desormais KO_URL_CLASS_NOT_FOUND et masquerait ce qui est reellement
    teste (matching de l'URL, similarite...)."""
    return f'<div class="{cls}"><a href="{href}">Link</a></div>'


class TestExcelUrlPresentInHtml:
    def test_exact_match_is_ok(self):
        html = link_with_class("https://example.com/?utm_source=x")
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
        html = link_with_class("https://example.com/?utm_content=nonfid")

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert len(results) == 1
        assert results[0]["Status"] == "OK"
        assert results[0]["Match Type"] == "Exact"

    def test_excel_side_conditional_matches_the_other_rendered_branch_too(self):
        excel_url = "https://example.com/?utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>"
        html = link_with_class("https://example.com/?utm_content=fid")

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "OK"

    def test_html_side_conditional_matches_a_plain_excel_url(self):
        # Cas symetrique (moins courant) : le href brut porte encore la
        # syntaxe conditionnelle non resolue.
        html = link_with_class("https://example.com/?utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>")

        results = run_url_qa(html, [excel_entry("https://example.com/?utm_content=fid")])

        assert results[0]["Status"] == "OK"

    def test_conditional_branch_still_functional_match_when_params_reordered(self):
        excel_url = "https://example.com/?utm_source=x&utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>"
        html = link_with_class("https://example.com/?utm_content=fid&utm_source=x")

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "KO"
        assert results[0]["Match Type"] == "Functional match only"

    def test_conditional_url_genuinely_not_rendered_is_still_not_found(self):
        excel_url = "https://example.com/?utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>"
        html = '<a href="https://example.com/totally-unrelated">Shop</a>'

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "KO"
        assert results[0]["Match Type"] == "No match"

    def test_no_match_shows_both_branches_instead_of_picking_one_arbitrarily(self):
        # Regression : quand rien cote HTML ne matche NI l'une NI l'autre
        # branche, il n'y a pas de href de reference pour choisir laquelle
        # afficher - montrer une seule branche au hasard ferait croire
        # qu'une destination precise etait attendue, alors que les deux
        # etaient valides. Les deux doivent apparaitre, pour que la lecture
        # du KO soit immediate.
        excel_url = "https://example.com/?utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>"
        html = '<a href="https://example.com/totally-unrelated">Shop</a>'

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert "https://example.com/?utm_content=fid" in results[0]["Excel Wording"]
        assert "https://example.com/?utm_content=nonfid" in results[0]["Excel Wording"]

    def test_functional_match_still_shows_only_the_matched_branch(self):
        # A l'inverse, quand un href a bien ete rapproche (fonctionnellement)
        # d'UNE branche precise, pas d'ambiguite a lever : seule cette
        # branche doit s'afficher, pas les deux.
        excel_url = "https://example.com/?utm_source=x&utm_content=<% if(cond) { %>fid<% } else { %>nonfid<% } %>"
        html = '<a href="https://example.com/?utm_content=fid&utm_source=x">Shop</a>'

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert "fid" in results[0]["Excel Wording"]
        assert "nonfid" not in results[0]["Excel Wording"]
        assert " ET " not in results[0]["Excel Wording"]

    def test_unexpected_html_link_with_unresolved_conditional_shows_both_branches(self):
        # Regression : un href du HTML qui n'a matche AUCUNE URL Excel (donc
        # signale "Unexpected URL") peut lui-meme porter un bloc AEM non
        # resolu (branches jamais tranchees, cote HTML). Il n'y a pas de
        # reference pour choisir laquelle des deux destinations montrer -
        # les montrer toutes les deux evite de masquer que ce lien peut
        # pointer vers l'une OU l'autre selon l'audience.
        html = (
            '<a href="<% if(cond) { %>https://example.com/a'
            '<% } else { %>https://example.com/b<% } %>">App</a>'
        )
        results = run_url_qa(html, [])

        assert len(results) == 1
        assert results[0]["Error Type"] == KO_UNEXPECTED_URL
        assert "https://example.com/a" in results[0]["HTML Wording"]
        assert "https://example.com/b" in results[0]["HTML Wording"]


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


class TestQueryIgnoredDomains:
    """
    Pour les domaines de URL_IGNORE_QUERY_DOMAINS (ex. homeios.onelink.me,
    liens OneLink/deep-link vers l'app), les parametres de tracking sont
    generes dynamiquement et ne doivent jamais etre compares - seule l'URL
    de base (chemin) compte.
    """

    def test_homeios_link_matches_despite_different_query_params(self):
        excel_url = "https://homeios.onelink.me/oUpm/ewa9ynyg?utm_source=trigger&pid=email_int"
        html = link_with_class("https://homeios.onelink.me/oUpm/ewa9ynyg?other_param=x")

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "OK"
        assert results[0]["Match Type"] == "Exact (paramètres ignorés)"

    def test_homeios_link_matches_when_html_has_no_query_at_all(self):
        excel_url = "https://homeios.onelink.me/oUpm/ewa9ynyg?utm_source=trigger"
        html = link_with_class("https://homeios.onelink.me/oUpm/ewa9ynyg")

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "OK"
        assert results[0]["Match Type"] == "Exact (paramètres ignorés)"

    def test_homeios_link_with_different_path_is_still_no_match(self):
        # Seule la query est ignoree, pas le chemin : deux liens homeios
        # genuinement differents doivent rester KO.
        excel_url = "https://homeios.onelink.me/oUpm/ewa9ynyg"
        html = '<a href="https://homeios.onelink.me/oUpm/totally-different">App</a>'

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "KO"
        assert results[0]["Match Type"] == "No match"

    def test_unrelated_domain_with_different_query_is_not_treated_as_a_match(self):
        # Un domaine hors liste : la query compte toujours normalement
        # (au mieux "Functional match only" si elle differe seulement par
        # l'ordre/encodage, sinon KO).
        excel_url = "https://example.com/page?utm_source=trigger&pid=email_int"
        html = '<a href="https://example.com/page?other_param=x">Shop</a>'

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "KO"
        assert results[0]["Match Type"] == "No match"

    def test_exact_match_still_takes_priority_over_query_ignored_match(self):
        # Si un href correspond EXACTEMENT (query comprise), ce match doit
        # primer sur le mecanisme "query ignoree", pas l'inverse.
        excel_url = "https://homeios.onelink.me/oUpm/ewa9ynyg?utm_source=trigger"
        html = link_with_class(
            "https://homeios.onelink.me/oUpm/ewa9ynyg?other=x"
        ) + link_with_class(
            "https://homeios.onelink.me/oUpm/ewa9ynyg?utm_source=trigger"
        )

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "OK"
        assert results[0]["Match Type"] == "Exact"

    def test_query_ignored_match_explains_a_possibly_low_similarity_in_error_type(self):
        # Un "Exact (parametres ignores)" peut afficher un pourcentage de
        # similarite bas (la query differe totalement), alors que le match
        # est parfaitement valide - "Error Type"/"Raison" doit l'expliquer
        # explicitement, meme si le Status reste OK (pas un vrai KO).
        excel_url = "https://homeios.onelink.me/oUpm/ewa9ynyg?utm_source=trigger&pid=email_int"
        html = link_with_class("https://homeios.onelink.me/oUpm/ewa9ynyg?completely_unrelated_param=xyz")

        results = run_url_qa(html, [excel_entry(excel_url)])

        assert results[0]["Status"] == "OK"
        assert results[0]["Criticality"] == ""
        assert "query ignorée" in results[0]["Error Type"].lower()

    def test_regular_exact_match_carries_no_such_note(self):
        html = link_with_class("https://example.com/shop")
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])

        assert results[0]["Match Type"] == "Exact"
        assert results[0]["Error Type"] == ""


class TestSourceSectionColumn:
    """
    Chaque URL Excel porte sa section d'origine (FID/NON FID, voir
    qa.excel_loader._parse_url_sections) : propagee jusqu'au rapport pour
    savoir de quelle section vient chaque ligne.
    """

    def test_source_section_is_propagated_to_the_report_row(self):
        html = '<a href="https://example.com/shop">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop", section="SEQ 1 NON FID")])
        assert results[0]["Source Section"] == "SEQ 1 NON FID"

    def test_missing_section_defaults_to_empty_string(self):
        html = '<a href="https://example.com/shop">Shop</a>'
        results = run_url_qa(html, [{"label": "", "url": "https://example.com/shop"}])
        assert results[0]["Source Section"] == ""

    def test_unexpected_html_link_has_no_source_section(self):
        # Rien cote Excel a attribuer : jamais rapproche d'aucune section.
        html = '<a href="https://example.com/never-referenced">Untracked</a>'
        results = run_url_qa(html, [])
        assert results[0]["Source Section"] == ""


class TestOccurrencesColumn:
    """
    Nombre de liens HTML dont la destination correspond a cette URL Excel -
    certains liens sont censes se repeter plusieurs fois dans le HTML (ex.
    un meme CTA en plusieurs endroits) ; il n'y a pas de "nombre attendu"
    cote referentiel, mais le compte reel aide a le verifier.
    """

    def test_counts_every_matching_link_not_just_the_first(self):
        html = (
            '<a href="https://example.com/shop">A</a>'
            '<a href="https://example.com/shop">B</a>'
            '<a href="https://example.com/shop">C</a>'
        )
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])
        assert results[0]["Occurrences"] == 3

    def test_single_occurrence_is_still_counted(self):
        html = '<a href="https://example.com/shop">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])
        assert results[0]["Occurrences"] == 1

    def test_no_match_has_zero_occurrences(self):
        html = '<a href="https://example.com/unrelated">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])
        assert results[0]["Occurrences"] == 0

    def test_only_genuinely_matching_links_are_counted(self):
        # Un lien avec un parametre de query supplementaire (destination
        # fonctionnellement differente) ne doit pas etre compte.
        html = (
            '<a href="https://example.com/shop">A</a>'
            '<a href="https://example.com/shop?extra=1">B (different)</a>'
        )
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])
        assert results[0]["Occurrences"] == 1

    def test_query_ignored_domain_occurrences_count_regardless_of_query(self):
        html = (
            '<a href="https://homeios.onelink.me/oUpm/ewa9ynyg?a=1">A</a>'
            '<a href="https://homeios.onelink.me/oUpm/ewa9ynyg?b=2">B</a>'
        )
        results = run_url_qa(html, [excel_entry("https://homeios.onelink.me/oUpm/ewa9ynyg")])
        assert results[0]["Occurrences"] == 2

    def test_unexpected_html_link_counts_its_own_repetitions(self):
        html = (
            '<a href="https://example.com/never-referenced">A</a>'
            '<a href="https://example.com/never-referenced">B</a>'
        )
        results = run_url_qa(html, [])
        assert len(results) == 1
        assert results[0]["Occurrences"] == 2


class TestClassMismatchBecomesKo:
    """
    Comme le wording (KO_WRONG_COMPONENT) : une URL trouvee a la bonne
    destination mais dans un composant HTML different de celui attendu
    (label Excel) reste un vrai probleme, meme si l'URL elle-meme est
    identique - le Status global bascule en KO.
    """

    def test_exact_url_match_but_different_class_is_ko(self):
        html = '<div class="audit-component-footer"><a href="https://example.com/shop">Shop</a></div>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop", label="audit-component-header")])

        assert results[0]["Status"] == "KO"
        assert results[0]["Error Type"] == KO_URL_WRONG_COMPONENT
        assert results[0]["Match Type"] == "Wrong component"

    def test_same_class_on_both_sides_stays_ok(self):
        html = '<div class="audit-component-header"><a href="https://example.com/shop">Shop</a></div>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop", label="audit-component-header")])
        assert results[0]["Status"] == "OK"

    def test_missing_expected_class_does_not_force_a_ko(self):
        # Pas de label cote Excel : rien a comparer, pas de KO force.
        html = '<div class="audit-component-footer"><a href="https://example.com/shop">Shop</a></div>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop", label="")])
        assert results[0]["Status"] == "OK"

    def test_class_mismatch_takes_priority_over_functional_match_only(self):
        # Meme quand le match n'etait deja que fonctionnel (parametres
        # reordonnes), un mismatch de classe reste signale comme tel plutot
        # que masque par "Functional match only".
        html = '<div class="audit-component-footer"><a href="https://example.com/?b=2&a=1">Shop</a></div>'
        results = run_url_qa(html, [excel_entry("https://example.com/?a=1&b=2", label="audit-component-header")])

        assert results[0]["Status"] == "KO"
        assert results[0]["Error Type"] == KO_URL_WRONG_COMPONENT

    def test_no_match_is_not_turned_into_a_class_mismatch(self):
        # Rien trouve du tout : reste "No match", jamais "Wrong component"
        # (il n'y a pas de href trouve dont comparer la classe).
        html = '<a href="https://example.com/unrelated">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop", label="audit-component-header")])
        assert results[0]["Error Type"] == "URL not found"


class TestClassNotFoundBecomesKo:
    """
    Comme le wording (KO_COMPONENT_CLASS_NOT_FOUND) : une URL trouvee mais
    dont l'element HTML n'a AUCUNE classe audit-component detectable ne
    doit jamais rester un OK silencieux - on ne peut alors meme pas
    verifier SI le composant est le bon, peu importe si le label Excel est
    lui-meme connu ou non. Seul un href reellement trouve declenche cette
    verification ("No match" reste inchange, voir
    TestClassMismatchBecomesKo.test_no_match_is_not_turned_into_a_class_mismatch).
    """

    def test_matched_url_without_any_html_class_is_ko(self):
        html = '<a href="https://example.com/shop">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop", label="audit-component-header")])

        assert results[0]["Status"] == "KO"
        assert results[0]["Error Type"] == "URL component class not found in HTML"
        assert results[0]["Match Type"] == "Component class not found in HTML"

    def test_applies_even_when_expected_class_is_also_unknown(self):
        # Aucun label cote Excel non plus : on ne peut toujours pas
        # verifier le composant, donc toujours KO (pas de "les deux sont
        # vides donc ca s'annule").
        html = '<a href="https://example.com/shop">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop", label="")])

        assert results[0]["Status"] == "KO"
        assert results[0]["Error Type"] == "URL component class not found in HTML"

    def test_takes_priority_over_functional_match_only(self):
        html = '<a href="https://example.com/?b=2&a=1">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/?a=1&b=2", label="audit-component-header")])

        assert results[0]["Status"] == "KO"
        assert results[0]["Error Type"] == "URL component class not found in HTML"

    def test_class_mismatch_still_takes_priority_when_a_class_is_present(self):
        # Si une classe HTML EST trouvee mais ne correspond pas au label
        # attendu, c'est "Wrong component" qui s'applique, pas "class not
        # found" (la classe existe, elle est juste differente).
        html = '<div class="audit-component-footer"><a href="https://example.com/shop">Shop</a></div>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop", label="audit-component-header")])
        assert results[0]["Error Type"] == KO_URL_WRONG_COMPONENT


class TestHtmlClassDisplaysNoneWhenEmpty:
    """Comme le wording (voir qa.wording_qa) : la colonne 'HTML Class' ne
    doit jamais rester vide - "none" indique explicitement qu'aucune classe
    n'a pu etre determinee, plutot que de laisser croire qu'elle a ete
    verifiee et trouvee vide."""

    def test_matched_link_without_any_class_shows_none(self):
        html = '<a href="https://example.com/shop">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])
        assert results[0]["HTML Class"] == "none"

    def test_no_match_shows_none_too(self):
        html = '<a href="https://example.com/unrelated">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])
        assert results[0]["HTML Class"] == "none"

    def test_unexpected_html_link_without_class_shows_none(self):
        html = '<a href="https://example.com/never-referenced">Untracked</a>'
        results = run_url_qa(html, [])
        assert results[0]["HTML Class"] == "none"

    def test_class_is_shown_as_is_when_present(self):
        html = '<div class="audit-component-cta"><a href="https://example.com/shop">Shop</a></div>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])
        assert results[0]["HTML Class"] == "audit-component-cta"


class TestSimilarityIsARealPercentage:
    """La colonne 'Similarity' reflete desormais une vraie similarite
    caractere par caractere (difflib), pas juste 100%/0% selon qu'un href a
    ete trouve ou non."""

    def test_exact_match_is_100_percent(self):
        html = '<a href="https://example.com/shop">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])
        assert results[0]["Similarity"] == "100%"

    def test_no_match_is_0_percent(self):
        html = '<a href="https://example.com/totally-unrelated-page">Shop</a>'
        results = run_url_qa(html, [excel_entry("https://example.com/shop")])
        assert results[0]["Similarity"] == "0%"

    def test_functional_match_with_reordered_params_is_less_than_100_percent(self):
        # Meme destination (memes parametres, ordre different) -> match
        # fonctionnel, mais les chaines brutes different par leur ordre :
        # la similarite caractere par caractere doit refleter cet ecart,
        # pas rester a 100% comme avant (l'ancienne formule etait binaire).
        html = link_with_class("https://example.com/shop?b=2&a=1")
        results = run_url_qa(html, [excel_entry("https://example.com/shop?a=1&b=2")])

        assert results[0]["Match Type"] == "Functional match only"
        similarity = int(results[0]["Similarity"].rstrip("%"))
        assert 0 < similarity < 100
