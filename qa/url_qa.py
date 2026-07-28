import html

from qa.url_utils import extract_hrefs, normalize_url


# =========================
# PIPELINE URL
# =========================
def _make_url_row(excel_url, found_href, status, error, criticality, match_type):
    return {
        "Component Name": "",
        "Element": "",
        "Expected Class": "",
        "HTML Class": "",
        "Excel Wording": excel_url,
        "HTML Wording": found_href,
        "Status": status,
        "Error Type": error,
        "Similarity": "100%" if found_href else "0%",
        "Criticality": criticality,
        "Match Type": match_type,
        "Occurrences": "",
    }


def run_url_qa(raw_html: str, excel_urls: list) -> list:
    """
    Pour chaque URL de reference (colonne UTM de l'Excel), cherche une
    correspondance parmi les href du HTML :
    - Exact  : URL identique une fois les entites HTML decodees (&amp; -> &).
      AEM encode systematiquement les hrefs ainsi ; ce n'est pas une
      difference de contenu reelle -> OK
    - Fonctionnel seulement (meme destination/parametres mais ordre des
      parametres ou pourcent-encodage different) -> KO, criticite moderee
      (le tracking reste correct mais l'URL n'est pas rendue a l'identique)
    - Aucune correspondance -> KO, criticite haute
    """
    hrefs = extract_hrefs(raw_html)

    results = []

    for excel_url in excel_urls:
        norm_excel = normalize_url(excel_url)

        exact_match = False
        functional_match = False
        found_href = ""

        for href in hrefs:
            href = href.strip()
            href_unescaped = html.unescape(href)

            if excel_url == href_unescaped:
                exact_match = True
                functional_match = True
                found_href = href
                break

            if not functional_match and normalize_url(href) == norm_excel:
                functional_match = True
                found_href = href

        if exact_match:
            results.append(_make_url_row(
                excel_url, found_href, "OK", "", "", "Exact"
            ))
        elif functional_match:
            results.append(_make_url_row(
                excel_url, found_href, "KO", "URL differs (ordre/encodage parametres)",
                "Medium", "Functional match only"
            ))
        else:
            results.append(_make_url_row(
                excel_url, "", "KO", "URL not found", "High", "No match"
            ))

    total = len(results)
    ok = sum(1 for r in results if r["Status"] == "OK")

    print("\n" + "=" * 70)
    print(f"✅ URLs - Total: {total} | OK: {ok} | KO: {total-ok} | "
          f"Score: {round((ok/total)*100, 2) if total else 0}%")
    print("=" * 70)

    return results
