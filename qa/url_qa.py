import html

from qa.config import KO_URL_PARAM_MISMATCH, KO_URL_NOT_FOUND, KO_UNEXPECTED_URL
from qa.html_extraction import extract_anchor_links
from qa.matching import criticality_for
from qa.url_utils import normalize_url


# =========================
# PIPELINE URL
# =========================
def _make_url_row(expected_class, found_class, excel_url, found_href, status, error, criticality, match_type):
    return {
        "Component Name": "",
        "Element": "",
        "Expected Class": expected_class,
        "HTML Class": found_class,
        "Excel Wording": excel_url,
        "HTML Wording": found_href,
        "Status": status,
        "Error Type": error,
        "Similarity": "100%" if found_href else "0%",
        "Criticality": criticality,
        "Match Type": match_type,
    }


def _as_entry(excel_entry) -> tuple:
    """Accepte un dict {"label", "url"} (nouveau format) ou une simple chaine URL (retro-compat)."""
    if isinstance(excel_entry, dict):
        return excel_entry.get("url", ""), excel_entry.get("label", "")
    return str(excel_entry), ""


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

    excel_urls : liste de dicts {"label": <nom du bloc dans l'onglet UTM,
    ex "HEADER"/"ALGO">, "url": <URL de reference>}. "label" sert de classe
    attendue, comparee a la classe (audit-component-xxx) du bloc HTML
    englobant le lien trouve.

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
        excel_url, expected_class = _as_entry(excel_entry)
        norm_excel = normalize_url(excel_url)

        exact_match = False
        functional_match = False
        found_href = ""
        found_class = ""

        for link in links:
            href = link["href"].strip()
            href_unescaped = html.unescape(href)

            if excel_url == href_unescaped:
                exact_match = True
                functional_match = True
                found_href = href
                found_class = link["component_class"]
                break

            if not functional_match and normalize_url(href) == norm_excel:
                functional_match = True
                found_href = href
                found_class = link["component_class"]

        if exact_match:
            matched_hrefs.add(found_href)
            results.append(_make_url_row(
                expected_class, found_class, excel_url, found_href, "OK", "", "", "Exact"
            ))
        elif functional_match:
            matched_hrefs.add(found_href)
            results.append(_make_url_row(
                expected_class, found_class, excel_url, found_href, "KO",
                KO_URL_PARAM_MISMATCH, criticality_for(KO_URL_PARAM_MISMATCH), "Functional match only"
            ))
        else:
            results.append(_make_url_row(
                expected_class, "", excel_url, "", "KO",
                KO_URL_NOT_FOUND, criticality_for(KO_URL_NOT_FOUND), "No match"
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
        # attendu du tout cote Excel).
        results.append({
            "Component Name": "",
            "Element": "",
            "Expected Class": "",
            "HTML Class": link["component_class"],
            "Excel Wording": "",
            "HTML Wording": href,
            "Status": "KO",
            "Error Type": KO_UNEXPECTED_URL,
            "Similarity": "0%",
            "Criticality": criticality_for(KO_UNEXPECTED_URL),
            "Match Type": "Unexpected",
        })

    total = len(results)
    ok = sum(1 for r in results if r["Status"] == "OK")

    print("\n" + "=" * 70)
    print(f"✅ URLs - Total: {total} | OK: {ok} | KO: {total-ok} | "
          f"Score: {round((ok/total)*100, 2) if total else 0}%")
    print("=" * 70)

    return results
