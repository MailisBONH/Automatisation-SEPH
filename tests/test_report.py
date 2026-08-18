from qa.report import build_rich_diff, build_rich_diff_char


def _joined(rich):
    return "".join(block.text for block in rich)


def _bold_parts(rich):
    return [block.text for block in rich if block.font.b]


class TestBuildRichDiffChar:
    """
    Diff caractere par caractere pour le rapport URL (contrairement a
    build_rich_diff, qui decoupe par mots - inadapte a une URL, qui n'a pas
    d'espaces).
    """

    def test_identical_strings_have_no_bold_part(self):
        rich1, rich2 = build_rich_diff_char("https://example.com/shop", "https://example.com/shop")
        assert _bold_parts(rich1) == []
        assert _bold_parts(rich2) == []
        assert _joined(rich1) == "https://example.com/shop"
        assert _joined(rich2) == "https://example.com/shop"

    def test_single_character_typo_is_highlighted_alone(self):
        # "shop" vs "shpo" : seuls "o"/"p" different, pas l'URL entiere -
        # exactement ce qu'un decoupage par mots ne peut pas faire ici.
        rich1, rich2 = build_rich_diff_char("shop", "shpo")
        assert _joined(rich1) == "shop"
        assert _joined(rich2) == "shpo"
        # Les caracteres identiques ("sh" au debut) ne sont jamais en gras.
        assert "".join(block.text for block in rich1 if not block.font.b).startswith("sh")

    def test_completely_different_strings_are_fully_bold(self):
        rich1, rich2 = build_rich_diff_char("abc", "xyz")
        assert _bold_parts(rich1) == ["abc"]
        assert _bold_parts(rich2) == ["xyz"]

    def test_empty_strings_produce_no_blocks(self):
        rich1, rich2 = build_rich_diff_char("", "")
        assert list(rich1) == []
        assert list(rich2) == []

    def test_none_is_treated_as_empty_string(self):
        rich1, rich2 = build_rich_diff_char(None, "abc")
        assert list(rich1) == []
        assert _joined(rich2) == "abc"


class TestBuildRichDiffWordStillWorksAsBefore:
    """Regression : le diff mot-a-mot existant (wording) ne doit pas etre
    affecte par l'ajout de la version caractere par caractere."""

    def test_word_level_diff_highlights_whole_differing_word(self):
        rich1, rich2 = build_rich_diff("Discover our new arrivals", "Discover our old arrivals")
        bold1 = _bold_parts(rich1)
        bold2 = _bold_parts(rich2)
        assert bold1 == ["new "]
        assert bold2 == ["old "]
