from qa.text_utils import clean_text, is_valid, normalize, normalize_currency


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


class TestCleanText:
    def test_strips_aem_pattern(self):
        assert clean_text("Hey <%= recipient.name %> welcome") == "Hey welcome"

    def test_unescapes_entities(self):
        assert clean_text("Sun &amp; sand") == "Sun & sand"

    def test_empty_input(self):
        assert clean_text("") == ""
        assert clean_text(None) == ""
