from bs4 import BeautifulSoup

from qa.html_extraction import (
    build_title_to_component_map,
    extract_anchor_links,
    extract_texts_from_textcontribution,
)


def _extract(html):
    # Meme parser qu'en production (qa.html_extraction.load_and_extract_html_texts) :
    # "html.parser" ne referme pas implicitement les tags mal formes (ex: un
    # <p> imbrique dans un <p>) comme le ferait un vrai navigateur/lxml.
    soup = BeautifulSoup(html, "lxml")
    return extract_texts_from_textcontribution(soup, {})


def _extract_with_titles(html):
    # Pipeline complet (voir qa.html_extraction.load_and_extract_html_texts) :
    # la map titre -> composant doit etre construite AVANT l'extraction pour
    # que la resolution des titres generiques fonctionne.
    soup = BeautifulSoup(html, "lxml")
    title_to_component = build_title_to_component_map(soup)
    return extract_texts_from_textcontribution(soup, title_to_component)


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


class TestGenericTitleClassResolution:
    """
    Regression : un titre generique (class="audit-generique-title") n'a pas
    d'ancetre audit-component - c'est justement pour ca qu'il a besoin de la
    resolution via title_to_component (voir build_title_to_component_map).
    Deux bugs empechaient cette resolution de fonctionner en pratique.
    """

    def test_title_inside_text_contribution_is_resolved_before_being_marked_seen(self):
        # Bug 1 : le titre est nested dans un wrapper audit-generique-title,
        # lui-meme nested dans un bloc text-contribution. La boucle
        # principale (find_component_class, aucun ancetre audit-component)
        # le capturait avec component_class=None ET l'ajoutait a `seen`
        # AVANT que la boucle specialisee (plus bas, qui sait resoudre via
        # title_to_component) n'ait la moindre chance de le retraiter - donc
        # le texte "deja vu" restait bloque a None pour toujours.
        html = (
            '<div class="text-contribution">'
            '<div class="audit-generique-title"><h2>Découvrez vos avantages</h2></div>'
            '<div class="audit-component-program-benefit"><p>Profitez de vos points</p></div>'
            "</div>"
        )
        items = _extract_with_titles(html)

        title_item = next(i for i in items if i["text"] == "Découvrez vos avantages")
        assert title_item["component_class"] == "audit-component-program-benefit"

    def test_title_class_on_leaf_tag_without_wrapper_is_still_resolved(self):
        # Bug 2 : la classe audit-generique-title posee directement sur le
        # tag de texte (pas de wrapper autour) - title_tag.find_all(True)
        # dans la boucle specialisee ne renvoie alors AUCUN descendant (il
        # ne s'inclut jamais lui-meme), donc ce titre n'etait jamais traite
        # du tout.
        html = (
            '<div class="text-contribution">'
            '<h2 class="audit-generique-title">Découvrez vos avantages</h2>'
            '<div class="audit-component-program-benefit"><p>Profitez de vos points</p></div>'
            "</div>"
        )
        items = _extract_with_titles(html)

        title_item = next(i for i in items if i["text"] == "Découvrez vos avantages")
        assert title_item["component_class"] == "audit-component-program-benefit"

    def test_title_followed_by_another_title_belongs_to_preceding_component(self):
        # Regle documentee de build_title_to_component_map : un titre suivi
        # d'un autre titre (pas d'un composant) appartient au composant
        # PRECEDENT dans la sequence DOM.
        html = (
            '<div class="text-contribution">'
            '<div class="audit-component-loyalty"><p>Vos points</p></div>'
            '<div class="audit-generique-title"><h2>Premier titre</h2></div>'
            '<div class="audit-generique-title"><h2>Second titre</h2></div>'
            "</div>"
        )
        items = _extract_with_titles(html)

        first = next(i for i in items if i["text"] == "Premier titre")
        second = next(i for i in items if i["text"] == "Second titre")
        assert first["component_class"] == "audit-component-loyalty"
        assert second["component_class"] == "audit-component-loyalty"

    def test_title_with_no_component_anywhere_in_sequence_stays_unresolved(self):
        # Aucun audit-component ni avant ni apres dans tout le document :
        # rien a resoudre, component_class doit rester None (et non planter).
        html = (
            '<div class="text-contribution">'
            '<div class="audit-generique-title"><h2>Titre isole</h2></div>'
            "</div>"
        )
        items = _extract_with_titles(html)

        title_item = next(i for i in items if i["text"] == "Titre isole")
        assert title_item["component_class"] is None


class TestExtractAnchorLinks:
    def test_mailto_links_are_excluded(self):
        html = (
            '<a href="mailto:support@example.com">Contact</a>'
            '<a href="https://example.com/shop">Shop</a>'
        )
        links = extract_anchor_links(html)
        assert [l["href"] for l in links] == ["https://example.com/shop"]

    def test_mailto_exclusion_is_case_insensitive(self):
        html = '<a href="MAILTO:support@example.com">Contact</a>'
        assert extract_anchor_links(html) == []

    def test_regular_link_carries_its_component_class(self):
        html = '<div class="audit-component-cta"><a href="https://example.com/shop">Shop</a></div>'
        links = extract_anchor_links(html)
        assert links == [{"href": "https://example.com/shop", "component_class": "audit-component-cta"}]
