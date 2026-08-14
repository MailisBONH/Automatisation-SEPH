import html

from qa.config import KO_URL_PARAM_MISMATCH, KO_URL_NOT_FOUND
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
        "Occurrences": "",
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
    """
    links = extract_anchor_links(raw_html)

    results = []

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
            results.append(_make_url_row(
                expected_class, found_class, excel_url, found_href, "OK", "", "", "Exact"
            ))
        elif functional_match:
            results.append(_make_url_row(
                expected_class, found_class, excel_url, found_href, "KO",
                KO_URL_PARAM_MISMATCH, criticality_for(KO_URL_PARAM_MISMATCH), "Functional match only"
            ))
        else:
            results.append(_make_url_row(
                expected_class, "", excel_url, "", "KO",
                KO_URL_NOT_FOUND, criticality_for(KO_URL_NOT_FOUND), "No match"
            ))

    total = len(results)
    ok = sum(1 for r in results if r["Status"] == "OK")

    print("\n" + "=" * 70)
    print(f"✅ URLs - Total: {total} | OK: {ok} | KO: {total-ok} | "
          f"Score: {round((ok/total)*100, 2) if total else 0}%")
    print("=" * 70)

    return results
