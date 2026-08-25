from bs4 import BeautifulSoup

from qa.html_extraction import (
    build_title_to_component_map,
    extract_anchor_links,
    extract_real_html_from_viewsource,
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


class TestExtractRealHtmlFromViewsource:
    """
    Reconstruction du HTML reel a partir d'une vue "view-source" AEM (une
    ligne de code source par ligne de tableau, voir is_view_source_document) -
    chemin de repli emprunte des qu'un fichier de ce type est uploade (voir
    qa.html_extraction.load_raw_html), avant tout le reste de l'extraction.
    """

    def test_reconstructs_html_from_line_content_cells(self):
        view_source = (
            "<table>"
            '<tr><td class="line-number">1</td>'
            '<td class="line-content">&lt;div class="text-contribution"&gt;</td></tr>'
            '<tr><td class="line-number">2</td>'
            '<td class="line-content">&lt;p&gt;Hello &amp; welcome&lt;/p&gt;</td></tr>'
            '<tr><td class="line-number">3</td><td class="line-content">&lt;/div&gt;</td></tr>'
            "</table>"
        )

        result = extract_real_html_from_viewsource(view_source)

        assert result == '<div class="text-contribution">\n<p>Hello & welcome</p>\n</div>'

    def test_returns_the_input_unchanged_when_no_line_content_cells_found(self):
        raw = "<div>Contenu HTML normal, pas une vue source</div>"
        assert extract_real_html_from_viewsource(raw) == raw


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

    def test_identical_text_in_two_different_components_is_extracted_separately(self):
        # Regression : `seen` etait un unique set partage sur TOUT le
        # document (toutes les blocs text-contribution confondus). Un meme
        # wording (ex: un CTA "Shop now") present dans deux composants
        # differents (header et footer) etait alors extrait une seule fois
        # pour le premier composant rencontre, et silencieusement supprime
        # pour le second - alors que ce sont deux ecarts potentiels
        # distincts, chacun attache a son propre composant.
        html = (
            '<div class="text-contribution">'
            '<div class="audit-component-header"><a href="/shop">Shop now</a></div>'
            "</div>"
            '<div class="text-contribution">'
            '<div class="audit-component-hero"><a href="/shop-2">Shop now</a></div>'
            "</div>"
        )
        items = _extract(html)

        matches = [i for i in items if i["text"] == "Shop now"]
        assert len(matches) == 2
        assert {i["component_class"] for i in matches} == {
            "audit-component-header",
            "audit-component-hero",
        }

    def test_individual_link_inside_legal_or_footer_component_is_excluded(self):
        # Un lien individuel dans un composant legal/footer n'est jamais
        # extrait a part - son texte est deja couvert par le paragraphe
        # complet qui l'englobe (extrait separement via le fallback
        # texte-brut-du-bloc).
        html = (
            '<div class="text-contribution">'
            '<div class="audit-component-footer">'
            '<p>Contact us at <a href="/legal">Legal notice</a> for details</p>'
            "</div>"
            "</div>"
        )
        items = _extract(html)
        assert len(items) == 1
        assert items[0]["text"] == "Contact us at Legal notice for details"

    def test_individual_link_inside_any_component_with_extractable_ancestor_is_excluded(self):
        # Regression : le filtrage des liens redondants ne doit plus
        # dependre d'un mot-cle de classe ("legal"/"footer") mais du meme
        # mecanisme structurel que celui utilise pour les tags de pure
        # emphase (_has_extractable_ancestor_within) - un lien imbrique dans
        # un <p> deja extrait est redondant quel que soit le nom du
        # composant qui l'englobe. Ici "audit-component-disclaimer" n'est
        # PAS dans LEGAL_MENTION_CLASS_KEYWORDS : avec l'ancienne approche
        # par mot-cle uniquement, ce lien aurait ete extrait a tort en plus
        # du paragraphe complet.
        html = (
            '<div class="text-contribution">'
            '<div class="audit-component-disclaimer">'
            '<p>See our <a href="/unsubscribe">unsubscribe</a> options below</p>'
            "</div>"
            "</div>"
        )
        items = _extract(html)
        assert len(items) == 1
        assert items[0]["text"] == "See our unsubscribe options below"


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

    def test_title_outside_any_text_contribution_block_is_still_resolved(self):
        # Cas distinct des precedents : le titre generique n'est PAS a
        # l'interieur d'un bloc text-contribution (la boucle principale de
        # extract_texts_from_textcontribution, scopee a block.find_all(True),
        # ne le voit jamais) - traite separement par le second passage sur
        # tout le soup (voir "Traitement des audit-generique-title").
        html = (
            '<div class="audit-generique-title"><h2>Decouvrez vos avantages</h2></div>'
            '<div class="text-contribution">'
            '<div class="audit-component-program-benefit"><p>Profitez de vos points</p></div>'
            "</div>"
        )
        items = _extract_with_titles(html)

        title_item = next(i for i in items if i["text"] == "Decouvrez vos avantages")
        assert title_item["component_class"] == "audit-component-program-benefit"

    def test_title_wrapper_with_no_recognized_descendant_is_silently_skipped(self):
        # Le wrapper audit-generique-title ne contient qu'un tag hors de la
        # liste reconnue (ici <a>, pas dans {h1,h2,h3,p,span,b,strong}) : pas
        # de descendant candidat, et le wrapper lui-meme (<div>) n'est pas
        # non plus un tag reconnu - ce titre est ignore sans planter.
        html = '<div class="audit-generique-title"><a href="#">Lien seul</a></div>'
        items = _extract_with_titles(html)

        assert all(i["text"] != "Lien seul" for i in items)

    def test_multi_descendant_title_outside_block_resolves_component_class(self):
        # Regression : build_title_to_component_map keye sa map sur le
        # texte COMPLET du wrapper audit-generique-title (ex: "Discover your
        # benefits" pour <h2>Discover</h2><span>your benefits</span>), mais
        # la boucle de resolution hors bloc text-contribution cherchait avec
        # le texte PARTIEL de chaque descendant ("Discover" puis "your
        # benefits" separement) - la cle ne correspondait donc jamais a une
        # entree de la map, et component_class resolvait a None a tort pour
        # les deux descendants.
        html = (
            '<div class="audit-generique-title"><h2>Discover</h2><span>your benefits</span></div>'
            '<div class="audit-component-benefit"><p>Some content</p></div>'
        )
        items = _extract_with_titles(html)

        discover = next(i for i in items if i["text"] == "Discover")
        benefits = next(i for i in items if i["text"] == "your benefits")
        assert discover["component_class"] == "audit-component-benefit"
        assert benefits["component_class"] == "audit-component-benefit"

    def test_duplicate_generic_titles_are_deduplicated(self):
        # Deux titres generiques identiques (hors de tout text-contribution) :
        # le second doit etre ignore par le dedoublonnage (`seen`), pas
        # ajoute une seconde fois.
        html = (
            '<div class="audit-generique-title"><h2>Titre repete</h2></div>'
            '<div class="audit-generique-title"><h2>Titre repete</h2></div>'
        )
        items = _extract_with_titles(html)

        matches = [i for i in items if i["text"] == "Titre repete"]
        assert len(matches) == 1


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
