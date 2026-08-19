from qa.url_utils import is_query_ignored_domain, normalize_url, strip_query


class TestIsQueryIgnoredDomain:
    def test_configured_domain_is_ignored(self):
        assert is_query_ignored_domain("https://homeios.onelink.me/oUpm/ewa9ynyg?x=1") is True

    def test_configured_domain_case_insensitive(self):
        assert is_query_ignored_domain("https://HOMEIOS.ONELINK.ME/oUpm/ewa9ynyg") is True

    def test_unrelated_domain_is_not_ignored(self):
        assert is_query_ignored_domain("https://example.com/page?x=1") is False

    def test_lookalike_domain_is_not_matched_by_substring(self):
        # "evilhomeios.onelink.me" contient "homeios.onelink.me" comme pure
        # sous-chaine (sans separateur "." juste avant) - ne doit PAS
        # matcher : seule une vraie relation domaine/sous-domaine compte.
        assert is_query_ignored_domain("https://evilhomeios.onelink.me/x") is False

    def test_empty_or_none_is_not_ignored(self):
        assert is_query_ignored_domain("") is False
        assert is_query_ignored_domain(None) is False


class TestStripQuery:
    def test_removes_query_string_entirely(self):
        assert strip_query("https://homeios.onelink.me/oUpm/ewa9ynyg?utm_source=x&pid=y") == \
            "https://homeios.onelink.me/oUpm/ewa9ynyg"

    def test_no_query_string_is_unchanged(self):
        assert strip_query("https://homeios.onelink.me/oUpm/ewa9ynyg") == \
            "https://homeios.onelink.me/oUpm/ewa9ynyg"

    def test_decodes_html_entities_and_percent_encoding(self):
        assert strip_query("https://example.com/path?a=1&amp;b=2") == "https://example.com/path"

    def test_empty_string_returns_empty(self):
        assert strip_query("") == ""

    def test_malformed_url_does_not_raise(self):
        # urlparse() lui-meme leve ValueError sur un netloc IPv6 invalide -
        # ne doit jamais faire planter tout le run pour un seul lien casse.
        assert strip_query("http://[::1/path") == "http://[::1/path"


class TestNormalizeUrl:
    """
    Normalisation fonctionnelle : deux URLs pointant vers la meme
    destination (memes parametres, ordre/encodage different) doivent
    produire exactement la meme chaine normalisee - c'est ce que
    qa.url_qa.run_url_qa compare pour decider d'un match "fonctionnel".
    """

    def test_reordered_parameters_normalize_identically(self):
        a = normalize_url("https://example.com/page?utm_source=x&utm_medium=y")
        b = normalize_url("https://example.com/page?utm_medium=y&utm_source=x")
        assert a == b

    def test_percent_encoding_is_decoded(self):
        assert normalize_url("https://example.com/page?a=hello%20world") == \
            normalize_url("https://example.com/page?a=hello world")

    def test_html_entities_are_decoded(self):
        assert normalize_url("https://example.com/page?a=1&amp;b=2") == \
            normalize_url("https://example.com/page?a=1&b=2")

    def test_url_without_query_string_is_unchanged_apart_from_trailing_slash_rules(self):
        assert normalize_url("https://example.com/shop") == "https://example.com/shop"

    def test_leading_and_trailing_whitespace_is_stripped(self):
        assert normalize_url("  https://example.com/shop  ") == "https://example.com/shop"

    def test_empty_or_none_returns_empty_string(self):
        assert normalize_url("") == ""
        assert normalize_url(None) == ""

    def test_blank_value_parameters_are_preserved_not_dropped(self):
        # keep_blank_values=True : "?utm_content=" (vide) doit rester
        # distinct de son absence totale, pas etre silencieusement retire.
        assert "utm_content=" in normalize_url("https://example.com/?utm_content=&utm_source=x")

    def test_genuinely_different_destinations_do_not_normalize_identically(self):
        assert normalize_url("https://example.com/a") != normalize_url("https://example.com/b")

    def test_malformed_url_does_not_raise(self):
        # Meme repli que strip_query : ne doit jamais faire planter tout le
        # run pour un seul lien casse dans le HTML.
        assert normalize_url("http://[::1/path") == "http://[::1/path"
