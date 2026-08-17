from bs4 import BeautifulSoup

from qa.html_extraction import extract_texts_from_textcontribution


def _extract(html):
    # Meme parser qu'en production (qa.html_extraction.load_and_extract_html_texts) :
    # "html.parser" ne referme pas implicitement les tags mal formes (ex: un
    # <p> imbrique dans un <p>) comme le ferait un vrai navigateur/lxml.
    soup = BeautifulSoup(html, "lxml")
    return extract_texts_from_textcontribution(soup, {})


def _texts(html):
    return [item["text"] for item in _extract(html)]


class TestExtractTextsFromTextcontribution:
    def test_plain_paragraph_is_extracted_unchanged(self):
        html = '<div class="text-contribution"><p>Discover our new arrivals</p></div>'
        assert _texts(html) == ["Discover our new arrivals"]

    def test_conditional_block_is_preserved_intact_for_later_comparison(self):
        # Regression : le nettoyage AEM etait applique a l'extraction, donc
        # le if/else etait deja detruit (branches concatenees) avant que
        # expand_conditional_variants() (qa/matching.py) ait la moindre
        # chance de l'interpreter. Le texte stocke doit garder le bloc <% if
        # %> intact.
        html = (
            '<div class="text-contribution"><p>'
            "<% if(n>1 ) { %>Dir fehlen noch <%=n%> Punkte"
            "<% } else { %>Dir fehlt noch <%=n%> Punkt<% } %> für dein Upgrade"
            "</p></div>"
        )
        texts = _texts(html)
        assert len(texts) == 1
        assert "<% if" in texts[0]
        assert "<% } else { %>" in texts[0]
        assert "Dir fehlen noch" in texts[0]
        assert "Dir fehlt noch" in texts[0]

    def test_pure_code_fragment_is_still_rejected_as_invalid(self):
        # La validite est toujours jugee sur la version completement
        # nettoyee : un fragment qui n'est que du code technique (rien de
        # lisible une fois le <% %> retire) doit rester rejete.
        html = '<div class="text-contribution"><p><%= recipient.cardcode %></p></div>'
        assert _texts(html) == []

    def test_edit_link_is_ignored(self):
        html = '<div class="text-contribution"><p>Edit</p></div>'
        assert _texts(html) == []

    def test_duplicate_exact_text_is_deduplicated(self):
        html = (
            '<div class="text-contribution">'
            "<p>Discover now</p><span>Discover now</span>"
            "</div>"
        )
        # Les deux fragments individuels identiques sont dedupliques en un
        # seul ; le fallback "texte nu du bloc" ne contribue rien de neuf
        # (sa concatenation se reduit entierement aux enfants deja extraits),
        # donc n'ajoute pas une 3e entree redondante.
        texts = _texts(html)
        assert texts == ["Discover now"]

    def test_inline_emphasis_nested_in_paragraph_is_not_extracted_separately(self):
        # Regression : <b>/<strong>/<em>/<i>/<small> imbriques dans un tag
        # deja extrait (ici <p>) ne doivent pas devenir un fragment isole -
        # leur contenu est deja couvert par le texte complet du <p>, et les
        # extraire separement produit un faux match (ou un faux KO) contre
        # une cellule Excel totalement sans rapport avec ce court fragment.
        html = (
            '<div class="text-contribution">'
            "<p>Sammle Punkte und erhalte alle <b>100 Punkte</b> ein Geschenk "
            "(<b>1€ = 1 Punkt</b>)</p>"
            "</div>"
        )
        texts = _texts(html)
        assert texts == ["Sammle Punkte und erhalte alle 100 Punkte ein Geschenk ( 1€ = 1 Punkt )"]

    def test_inline_emphasis_without_extractable_ancestor_is_still_extracted(self):
        # Sans ancetre deja extrait dans le bloc, <b> reste la seule source
        # de ce texte et doit etre extrait normalement.
        html = '<div class="text-contribution"><b>Important notice</b></div>'
        assert _texts(html) == ["Important notice"]

    def test_fallback_block_text_still_kept_when_it_adds_new_content(self):
        # Si le bloc contient du texte hors de tout tag extractible (ici un
        # texte "nu", pas dans un <p>/<span>/...), le fallback doit toujours
        # le capturer - ce n'est PAS un cas de contenu deja couvert.
        html = '<div class="text-contribution">Some standalone loose text here</div>'
        assert _texts(html) == ["Some standalone loose text here"]

    def test_component_class_is_resolved_from_ancestor(self):
        html = (
            '<div class="text-contribution">'
            '<div class="audit-component-loyalty"><p>Dir fehlen noch Punkte</p></div>'
            "</div>"
        )
        items = _extract(html)
        assert items[0]["component_class"] == "audit-component-loyalty"
