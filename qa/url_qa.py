import difflib
import html

from qa.config import (
    KO_URL_PARAM_MISMATCH,
    KO_URL_NOT_FOUND,
    KO_UNEXPECTED_URL,
    KO_URL_WRONG_COMPONENT,
    KO_URL_CLASS_NOT_FOUND,
    INFO_URL_QUERY_IGNORED,
)
from qa.html_extraction import extract_anchor_links
from qa.matching import criticality_for
from qa.text_utils import clean_display_text, clean_text, expand_conditional_variants
from qa.url_utils import is_query_ignored_domain, normalize_url, strip_query


# =========================
# PIPELINE URL
# =========================
def _display_all_variants(raw_text: str) -> str:
    """
    Comme clean_display_text(raw_text) sans texte de reference, mais montre
    TOUTES les branches d'un bloc conditionnel AEM au lieu d'en choisir une
    seule arbitrairement (la 1ere) - utilise pour le cote Excel d'un KO
    "No match", et pour le cote HTML d'un "Unexpected URL (not in Excel)".

    Jointure par " ET ", pas "OU" : les deux branches sont deux destinations
    reellement rendues selon l'audience (l'une OU l'autre au moment du
    rendu), mais le referentiel Excel doit documenter les DEUX pour couvrir
    tous les cas - "OU" laisserait croire qu'une seule des deux suffirait a
    satisfaire le referentiel.
    """
    variants = [clean_text(v) for v in expand_conditional_variants(raw_text)]
    deduped = []
    for v in variants:
        if v not in deduped:
            deduped.append(v)
    return " ET ".join(deduped)


def _char_similarity(a: str, b: str) -> float:
    """Similarite caractere par caractere (0-1, difflib.SequenceMatcher) -
    contrairement au wording, une URL n'a pas d'espaces pour decouper en
    "mots" ; comparer caractere par caractere est le seul decoupage qui ait
    un sens ici."""
    a, b = str(a or ""), str(b or "")
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _make_url_row(expected_class, found_class, excel_url, found_href, status, error, criticality, match_type,
                   source_section="", occurrences=0):
    # clean_display_text()/_display_all_variants() ne font rien si
    # excel_url/found_href ne contient pas de bloc conditionnel AEM (cas
    # courant). S'il y en a un : quand un href a ete trouve (OK ou
    # "Functional match only"), clean_display_text affiche la branche
    # resolue la plus proche de ce href - jamais le code AEM brut, meme
    # logique que qa.matching.make_row cote wording. Quand rien n'a ete
    # trouve ("No match"), il n'y a pas de href de reference pour choisir
    # UNE branche plutot que l'autre : les montrer toutes les deux cote
    # Excel est plus simple a diagnostiquer qu'un choix arbitraire qui en
    # masquerait une.
    if found_href:
        excel_display = clean_display_text(excel_url, found_href)
    else:
        excel_display = _display_all_variants(excel_url)

    html_display = clean_display_text(found_href, excel_url)

    return {
        "Component Name": "",
        "Element": "",
        "Source Section": source_section,
        "Expected Class": expected_class,
        # "none" (pas vide) quand aucune classe n'a ete trouvee - meme
        # convention que le wording (voir qa.wording_qa), pour ne jamais
        # laisser croire a tort qu'une classe a bien ete verifiee.
        "HTML Class": found_class or "none",
        "Excel Wording": excel_display,
        "HTML Wording": html_display,
        "Status": status,
        "Error Type": error,
        "Similarity": f"{round(_char_similarity(excel_display, html_display) * 100)}%",
        "Criticality": criticality,
        "Match Type": match_type,
        "Occurrences": occurrences,
    }


def _as_entry(excel_entry) -> tuple:
    """Accepte un dict {"label", "url", "Section"} (nouveau format) ou une
    simple chaine URL (retro-compat)."""
    if isinstance(excel_entry, dict):
        return excel_entry.get("url", ""), excel_entry.get("label", ""), excel_entry.get("Section", "")
    return str(excel_entry), "", ""


def _count_occurrences(excel_variants, norm_excel_variants, links) -> int:
    """
    Nombre de liens HTML dont la destination correspond a cette URL Excel
    (exactement, fonctionnellement, ou via un domaine a query ignoree - les
    3 memes criteres que le matching principal), tous comptes - pas
    seulement le premier trouve. Certains liens sont normalement repetes
    plusieurs fois dans le HTML (ex. un meme CTA en plusieurs endroits) ;
    il n'y a pas de "nombre attendu" cote referentiel, mais afficher le
    compte reel aide a verifier qu'un lien censement repete l'est bien.
    """
    count = 0
    for link in links:
        href_unescaped = html.unescape(link["href"].strip())
        html_variants = expand_conditional_variants(href_unescaped)

        exact = any(hv == ev for hv in html_variants for ev in excel_variants)
        query_ignored = any(
            is_query_ignored_domain(ev) and strip_query(hv) == strip_query(ev)
            for hv in html_variants for ev in excel_variants
        )
        functional = exact or query_ignored or any(
            normalize_url(hv) == nev for hv in html_variants for nev in norm_excel_variants
        )

        if exact or query_ignored or functional:
            count += 1

    return count


def run_url_qa(raw_html: str, excel_urls: list) -> list:
    """
    Pour chaque URL de reference (colonne UTM de l'Excel), cherche une
    correspondance parmi les liens <a href="..."> du HTML :
    - Exact  : URL identique une fois les entites HTML decodees (&amp; -> &).
      AEM encode systematiquement les hrefs ainsi ; ce n'est pas une
      difference de contenu reelle -> OK
    - Fonctionnel seulement (meme destination/parametres mais ordre des
      parametres ou pourcent-encodage different) -> KO, criticite moderee
      (le tracking reste correct mais l'URL n'est pas rendue a l'identique)
    - Aucune correspondance -> KO, criticite haute

    Comme le wording, une URL (cote Excel ou cote HTML) peut contenir un
    bloc conditionnel AEM (<% if %>...<% } else { %>...<% } %>) qui bascule
    d'une destination/d'un parametre a l'autre selon l'audience - le rendu
    final n'affiche jamais qu'une seule branche a la fois. expand_conditional_variants()
    deplie chaque cote en une variante par branche, et n'importe quelle
    combinaison HTML/Excel qui matche compte comme un match (meme principe
    que qa.matching.compare()).

    excel_urls : liste de dicts {"label": <nom du bloc dans l'onglet UTM,
    ex "HEADER"/"ALGO">, "url": <URL de reference>}. "label" sert de classe
    attendue, comparee a la classe (audit-component-xxx) du bloc HTML
    englobant le lien trouve.

    Pour certains domaines (URL_IGNORE_QUERY_DOMAINS dans qa/config.py, ex.
    homeios.onelink.me - liens OneLink/deep-link vers l'app dont les
    parametres sont generes dynamiquement, jamais figes a l'identique dans
    le referentiel), la query string n'est jamais comparee du tout : seule
    l'URL de base (chemin) compte, et un match sur ce seul chemin est traite
    comme un match plein (OK), pas comme un "Functional match only".

    Verifie aussi le sens inverse : tout lien HTML jamais retrouve comme
    correspondance (exacte ou fonctionnelle) d'une URL Excel est signale en
    KO "Unexpected URL (not in Excel)" (ex. une URL ajoutee au HTML sans
    mise a jour du referentiel). Deduplique par valeur de href : un meme
    lien repete plusieurs fois dans le HTML (logo, reseaux sociaux...) ne
    doit ressortir qu'une seule fois, pas une fois par occurrence.
    """
    links = extract_anchor_links(raw_html)

    results = []
    matched_hrefs = set()

    for excel_entry in excel_urls:
        excel_url, expected_class, source_section = _as_entry(excel_entry)
        excel_variants = expand_conditional_variants(excel_url)
        norm_excel_variants = [normalize_url(v) for v in excel_variants]
        occurrences = _count_occurrences(excel_variants, norm_excel_variants, links)

        exact_href, exact_class = None, ""
        ignored_query_href, ignored_query_class = None, ""
        functional_href, functional_class = None, ""

        for link in links:
            href = link["href"].strip()
            href_unescaped = html.unescape(href)
            html_variants = expand_conditional_variants(href_unescaped)

            if exact_href is None and any(hv == ev for hv in html_variants for ev in excel_variants):
                exact_href = href
                exact_class = link["component_class"]
                break  # rien de mieux possible pour cette URL Excel

            if ignored_query_href is None and any(
                is_query_ignored_domain(ev) and strip_query(hv) == strip_query(ev)
                for hv in html_variants for ev in excel_variants
            ):
                ignored_query_href = href
                ignored_query_class = link["component_class"]

            if functional_href is None:
                norm_html_variants = [normalize_url(v) for v in html_variants]
                if any(hv == ev for hv in norm_html_variants for ev in norm_excel_variants):
                    functional_href = href
                    functional_class = link["component_class"]

        # Meilleur candidat trouve, par ordre de priorite (Exact > domaine a
        # query ignoree > fonctionnel), avec son statut/type "de base".
        if exact_href is not None:
            found_href, found_class = exact_href, exact_class
            base_status, base_error, base_crit, base_match_type = "OK", "", "", "Exact"
        elif ignored_query_href is not None:
            found_href, found_class = ignored_query_href, ignored_query_class
            # Statut OK, mais avec une note dans "Error Type"/"Raison" (pas
            # un vrai KO) : la Similarity peut afficher un pourcentage bas
            # ici (la query differe souvent totalement), ce qui parait a
            # tort suspect sans expliquer que seul le chemin compte pour ce
            # domaine.
            base_status, base_error, base_crit, base_match_type = (
                "OK", INFO_URL_QUERY_IGNORED, "", "Exact (paramètres ignorés)"
            )
        elif functional_href is not None:
            found_href, found_class = functional_href, functional_class
            base_status, base_error, base_crit, base_match_type = (
                "KO", KO_URL_PARAM_MISMATCH, criticality_for(KO_URL_PARAM_MISMATCH), "Functional match only"
            )
        else:
            found_href, found_class = "", ""
            base_status, base_error, base_crit, base_match_type = (
                "KO", KO_URL_NOT_FOUND, criticality_for(KO_URL_NOT_FOUND), "No match"
            )

        # Classe technique en desaccord (les deux connues et differentes) :
        # le lien pointe peut-etre a la bonne destination, mais dans le
        # mauvais composant - un vrai probleme, meme si la destination
        # elle-meme est correcte (meme principe que KO_WRONG_COMPONENT cote
        # wording). Ne s'applique que si un href a effectivement ete trouve
        # (rien a comparer sinon) et si les DEUX classes sont connues :
        # "None != None" serait sinon a tort traite comme "differentes".
        class_known = bool(expected_class) and bool(found_class)
        class_mismatch = bool(found_href) and class_known and expected_class != found_class

        # Classe HTML introuvable (aucune classe audit-component autour du
        # lien trouve) : contrairement au cas ci-dessus, on ne sait meme pas
        # SI le composant est le bon ou le mauvais - impossible de le
        # verifier du tout. Comme KO_COMPONENT_CLASS_NOT_FOUND cote wording,
        # ce doute ne doit jamais se solder par un OK silencieux : peu
        # importe si expected_class est lui-meme connu ou non, tant qu'un
        # href a bien ete trouve (sinon "No match" couvre deja le cas).
        class_not_found = bool(found_href) and not found_class

        if class_mismatch:
            status, error, crit, match_type = (
                "KO", KO_URL_WRONG_COMPONENT, criticality_for(KO_URL_WRONG_COMPONENT), "Wrong component"
            )
        elif class_not_found:
            status, error, crit, match_type = (
                "KO", KO_URL_CLASS_NOT_FOUND, criticality_for(KO_URL_CLASS_NOT_FOUND),
                "Component class not found in HTML"
            )
        else:
            status, error, crit, match_type = base_status, base_error, base_crit, base_match_type

        if found_href:
            matched_hrefs.add(found_href)

        results.append(_make_url_row(
            expected_class, found_class, excel_url, found_href, status, error, crit, match_type,
            source_section, occurrences
        ))

    seen_unmatched = set()
    for link in links:
        href = link["href"].strip()
        if href in matched_hrefs or href in seen_unmatched:
            continue
        seen_unmatched.add(href)
        # Pas de _make_url_row ici : sa formule de Similarity ("100%" des
        # qu'un href est present) suppose un href trouve EN REPONSE a une
        # URL Excel - inadapte ici, ou c'est l'inverse (rien n'etait
        # attendu du tout cote Excel). _display_all_variants() (pas
        # clean_display_text) car ce href n'a ete rapproche d'AUCUNE URL
        # Excel : s'il porte lui-meme un bloc conditionnel AEM non resolu
        # (branches jamais tranchees), il n'y a pas de reference pour
        # choisir laquelle montrer - les montrer toutes les deux evite de
        # masquer que ce lien non reference peut pointer vers l'une OU
        # l'autre destination selon l'audience. Pas de "Source Section"
        # non plus : rien cote Excel a attribuer.
        occurrence_count = sum(1 for l in links if l["href"].strip() == href)
        results.append({
            "Component Name": "",
            "Element": "",
            "Source Section": "",
            "Expected Class": "",
            "HTML Class": link["component_class"] or "none",
            "Excel Wording": "",
            "HTML Wording": _display_all_variants(href),
            "Status": "KO",
            "Error Type": KO_UNEXPECTED_URL,
            "Similarity": "0%",
            "Criticality": criticality_for(KO_UNEXPECTED_URL),
            "Match Type": "Unexpected",
            "Occurrences": occurrence_count,
        })

    total = len(results)
    ok = sum(1 for r in results if r["Status"] == "OK")

    print("\n" + "=" * 70)
    print(f"✅ URLs - Total: {total} | OK: {ok} | KO: {total-ok} | "
          f"Score: {round((ok/total)*100, 2) if total else 0}%")
    print("=" * 70)

    return results
