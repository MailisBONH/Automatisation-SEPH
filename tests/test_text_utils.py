from qa.text_utils import (
    clean_text,
    expand_conditional_variants,
    is_valid,
    normalize,
    normalize_currency,
    similarity_ratio,
)


class TestNormalize:
    def test_none_returns_empty_string(self):
        assert normalize(None) == ""

    def test_strips_aem_directive_blocks(self):
        # Regression: normalize() used to overwrite each cleaning step with
        # the original `text` instead of chaining on `s`, so none of this
        # actually got removed in production despite the docstring claiming
        # it did.
        raw = "<% if(n>1){%>points<%}else{%>point<%}%> earned"
        assert normalize(raw) == "points point earned"

    def test_strips_dollar_brace_variables(self):
        assert normalize("Get 10 ${amount} points now") == "Get 10 points now"

    def test_strips_bracket_placeholders(self):
        # The whitespace-before-punctuation cleanup also removes the space
        # left behind by the placeholder removal.
        assert normalize("Offer valid until [gift validity end date].") == "Offer valid until."

    def test_strips_xxx_placeholder(self):
        assert normalize("Save XXX today") == "Save today"

    def test_converts_br_to_space(self):
        assert normalize("Line one<br/>Line two") == "Line one Line two"

    def test_unescapes_html_entities(self):
        assert normalize("Terms &amp; conditions") == "Terms & conditions"

    def test_strips_invisible_characters(self):
        assert normalize("10‎€ off") == "10€ off"

    def test_collapses_whitespace(self):
        assert normalize("  a   b\n\nc  ") == "a b c"


class TestExpandConditionalVariants:
    def test_text_without_conditional_returns_single_variant(self):
        assert expand_conditional_variants("Discover now") == ["Discover now"]

    def test_none_returns_single_empty_variant(self):
        assert expand_conditional_variants(None) == [""]

    def test_if_else_produces_one_variant_per_branch(self):
        raw = "<% if(n>1){%>Dir fehlen noch Punkte<%}else{%>Dir fehlt noch Punkt<%}%> für dein Upgrade"
        variants = expand_conditional_variants(raw)
        assert variants == [
            "Dir fehlen noch Punkte für dein Upgrade",
            "Dir fehlt noch Punkt für dein Upgrade",
        ]

    def test_if_without_else_produces_included_and_removed_variants(self):
        raw = "Hello<% if(n>0){%> beautiful<%}%> world"
        variants = expand_conditional_variants(raw)
        assert variants == ["Hello beautiful world", "Hello world"]

    def test_nested_variable_placeholder_is_kept_inside_branch(self):
        # La variable <%=...%> a l'interieur d'une branche doit rester dans
        # la variante brute (elle sera nettoyee ensuite par normalize()).
        raw = "<% if(n>1){%>Noch <%=n%> Punkte<%}else{%>Noch <%=n%> Punkt<%}%>"
        variants = expand_conditional_variants(raw)
        assert variants == ["Noch <%=n%> Punkte", "Noch <%=n%> Punkt"]

    def test_sequential_conditionals_produce_cartesian_product(self):
        raw = "<%=n%><% if(n>=1000){%> Punkte<%}else{%> / 1000 Punkte<%}%><% if(m>1){%> Geschenke<%}else{%> Geschenk<%}%>"
        variants = expand_conditional_variants(raw)
        assert len(variants) == 4
        assert "<%=n%> Punkte Geschenke" in variants
        assert "<%=n%> / 1000 Punkte Geschenk" in variants

    def test_condition_with_nested_parentheses_is_still_resolved(self):
        # Regression : [^)]* dans la condition s'arretait a la 1ere ")"
        # rencontree, donc une condition avec des parentheses imbriquees
        # (parenthesage supplementaire, appel de fonction...) faisait
        # completement echouer la detection du bloc if/else - silencieusement,
        # sans erreur, juste aucune expansion.
        raw = "<% if((n)>1){%>Dir fehlen noch Punkte<%}else{%>Dir fehlt noch Punkt<%}%>"
        assert expand_conditional_variants(raw) == ["Dir fehlen noch Punkte", "Dir fehlt noch Punkt"]

    def test_condition_with_function_call_is_still_resolved(self):
        raw = "<% if(recipient.getPoints(true)>1){%>Dir fehlen noch Punkte<%}else{%>Dir fehlt noch Punkt<%}%>"
        assert expand_conditional_variants(raw) == ["Dir fehlen noch Punkte", "Dir fehlt noch Punkt"]

    def test_nested_parens_do_not_swallow_a_later_sequential_conditional(self):
        raw = "<% if((n)>=1000){%> Punkte<%}else{%> / 1000 Punkte<%}%><% if((m)>1){%> Geschenke<%}else{%> Geschenk<%}%>"
        variants = expand_conditional_variants(raw)
        assert len(variants) == 4
        assert " Punkte Geschenke" in variants
        assert " / 1000 Punkte Geschenk" in variants

    def test_variants_are_deduplicated(self):
        # Les deux branches sont identiques une fois le texte hors-condition
        # retire : une seule variante doit rester.
        raw = "<% if(n>1){%>same<%}else{%>same<%}%>"
        assert expand_conditional_variants(raw) == ["same"]

    def test_if_else_nested_inside_an_if_only_branch_is_resolved_correctly(self):
        # Regression : un if/else VRAIMENT imbrique (pas seulement sequentiel)
        # dans la branche if d'un bloc englobant sans else faisait
        # auparavant confondre la fermeture/le else DU BLOC IMBRIQUE avec
        # celle du bloc englobant (recherche regex non-greedy la plus proche,
        # sans notion de profondeur) - la branche "outer=true, inner=false"
        # n'etait alors jamais produite, et une variante contenait un residu
        # de tag AEM non nettoye ("Z<%}%>").
        raw = "<% if(a){%>X<% if(b){%>Y<%}else{%>Z<%}%><%}%>"
        variants = expand_conditional_variants(raw)
        assert set(variants) == {"XY", "XZ", ""}
        assert not any("<%" in v for v in variants)

    def test_if_else_nested_inside_the_else_branch_is_resolved_correctly(self):
        raw = "<% if(a){%>X<%}else{%><% if(b){%>Y<%}else{%>Z<%}%><%}%>"
        variants = expand_conditional_variants(raw)
        assert set(variants) == {"X", "Y", "Z"}
        assert not any("<%" in v for v in variants)


class TestNormalizeCurrency:
    def test_removes_pipe_separator_between_amounts(self):
        assert normalize_currency("10 AED | 10 SAR") == normalize_currency("10 AED 10 SAR")

    def test_removes_capital_i_separator(self):
        assert normalize_currency("AED I 10") == "AED 10"


class TestIsValid:
    def test_rejects_too_short(self):
        assert is_valid("ab") is False

    def test_rejects_css_like_value(self):
        assert is_valid("font-family: Arial") is False

    def test_rejects_pure_number(self):
        assert is_valid("1234") is False

    def test_rejects_hex_color(self):
        assert is_valid("#FF0000") is False

    def test_accepts_normal_sentence(self):
        assert is_valid("Discover our new arrivals") is True

    def test_rejects_mostly_technical_characters(self):
        assert is_valid("<div class='x'>") is False

    def test_rejects_css_or_js_style_comment_block(self):
        # Regression : le pattern etait mistype (r"^/\/\*", equivalent a
        # r"^//\*" - un slash EN TROP) et ne matchait donc jamais un
        # commentaire "/* ... */" reellement leake dans le texte extrait
        # (ex. depuis un <style>/<script> mal ferme).
        assert is_valid("/* Comment block style */") is False


class TestSimilarityRatio:
    def test_both_empty_returns_1(self):
        assert similarity_ratio("", "") == 1.0

    def test_none_inputs_treated_as_empty(self):
        assert similarity_ratio(None, None) == 1.0
        assert similarity_ratio(None, "") == 1.0

    def test_default_is_case_insensitive(self):
        assert similarity_ratio("Discover now", "discover now") == 1.0

    def test_case_sensitive_true_preserves_case_differences(self):
        assert similarity_ratio("Discover now", "discover now", case_sensitive=True) < 1.0

    def test_partial_match_returns_a_fraction_between_0_and_1(self):
        ratio = similarity_ratio("Discover our new arrivals", "Discover our new arrival")
        assert 0.0 < ratio < 1.0

    def test_completely_different_text_is_low(self):
        ratio = similarity_ratio("Discover now", "Buy shoes today")
        assert ratio < 0.5


class TestCleanText:
    def test_strips_aem_pattern(self):
        assert clean_text("Hey <%= recipient.name %> welcome") == "Hey welcome"

    def test_unescapes_entities(self):
        assert clean_text("Sun &amp; sand") == "Sun & sand"

    def test_empty_input(self):
        assert clean_text("") == ""
        assert clean_text(None) == ""
