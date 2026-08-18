from qa.url_utils import is_query_ignored_domain, strip_query


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
