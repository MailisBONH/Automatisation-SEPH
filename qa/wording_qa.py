import difflib
from collections import defaultdict

from qa.matching import find_aggregated_match, find_best_match, find_closest_match, find_split_match, make_row
from qa.text_utils import normalize, normalize_currency


# =========================
# PIPELINE WORDING
# =========================
def run_wording_qa(html_texts, excel_wordings):
    results = []

    matched_excel_counts = defaultdict(int)

    html_consumed = set()


    i = 0
    while i < len(html_texts):
        if i in html_consumed:
            i += 1
            continue

        item = html_texts[i]
        h_text = item["text"]


        expected_class = ""
        found_class = item.get("component_class", "")
        status = "KO"
        criticality = "High"


        # A) Matching direct — on ne verrouille ce resultat que s'il est OK.
        # Un candidat de la bonne classe mais textuellement tres different
        # (KO) ne doit pas empecher d'essayer l'agregation/le split, qui
        # peuvent trouver un meilleur match pour ce meme fragment HTML.
        direct = find_best_match(item, excel_wordings, set(matched_excel_counts.keys()))
        if direct and direct[1] == "OK":
            idx, status, error, sim = direct
            results.append(make_row(excel_wordings[idx], item, h_text,
                                     status, error, sim, "Direct"))
            matched_excel_counts[idx] += 1
            i += 1
            continue

        # B) Agregation (N HTML → 1 Excel) — meme logique : on ne verrouille
        # que si le resultat est OK.
        agg = find_aggregated_match(html_texts, i, excel_wordings, matched_excel_counts)
        if agg and agg[2] == "OK":
            nb, idx, status, error, sim = agg
            agg_text = " ".join(html_texts[i + k]["text"] for k in range(nb))
            results.append(make_row(excel_wordings[idx], item, agg_text,
                                     status, error, sim, f"Agrege ({nb} blocs)"))
            matched_excel_counts[idx] += 1
            for k in range(nb):
                html_consumed.add(i + k)
            i += nb
            continue

        # C) Split (HTML ⊂ Excel)
        split = find_split_match(h_text, excel_wordings, matched_excel_counts)
        if split:
            idx, status, error, sim = split
            results.append(make_row(excel_wordings[idx], item, h_text,
                                     "OK", "", sim, "Split (HTML ⊂ Excel)"))
            i += 1
            continue

        # Ni Direct, ni Agregation, ni Split n'ont trouve de OK : on retombe
        # sur le meilleur KO deja trouve (Direct en priorite, puis
        # Agregation) plutot que de l'ignorer et de sauter directement au
        # closest-match generique.
        if direct:
            idx, status, error, sim = direct
            results.append(make_row(excel_wordings[idx], item, h_text,
                                     status, error, sim, "Direct"))
            matched_excel_counts[idx] += 1
            i += 1
            continue

        if agg:
            nb, idx, status, error, sim = agg
            agg_text = " ".join(html_texts[i + k]["text"] for k in range(nb))
            results.append(make_row(excel_wordings[idx], item, agg_text,
                                     status, error, sim, f"Agrege ({nb} blocs)"))
            matched_excel_counts[idx] += 1
            for k in range(nb):
                html_consumed.add(i + k)
            i += nb
            continue

        # D) Aucun match exact — chercher le wording Excel le plus proche
        closest = find_closest_match(item, excel_wordings, matched_excel_counts)
        if closest:

            c_idx = closest["idx"]
            c_sim = closest["similarity"]
            class_match = closest["class_match"]

            ex_closest = excel_wordings[c_idx]

            expected_class = ex_closest.get("Technical")
            found_class = item.get("component_class")

            class_match = expected_class == found_class

            if not class_match:
                status = "KO"
                error_type = "Wording in wrong component"

            elif c_sim >= 0.85:
                status = "OK"
                error_type = ""

            else:
                status = "KO"
                error_type = "Wording differs"


            criticality = (
                ""
                if status == "OK"
                else "High"
            )


            if status == "OK":
                match_type = "Closest Match Accepted"
            else:
                match_type = "Closest Match"

            results.append({
                "Component Name": ex_closest["Component"],
                "Element": ex_closest["Element"],
                "Expected Class": expected_class,
                "HTML Class": found_class,
                "Excel Wording": ex_closest["Wording"],
                "HTML Wording": h_text,
                "Status": status,
                "Error Type": error_type,
                "Similarity": f"{round(c_sim * 100)}%",
                "Criticality": criticality,
                "Match Type": match_type
            })
        else:
            results.append({
                "Component Name": "",
                "Element": "",
                "Expected Class": expected_class or "",
                "HTML Class": found_class or "",
                "Excel Wording": "",
                "HTML Wording": h_text,
                "Status": status,
                "Error Type": "Not in Excel",
                "Similarity": "0%",
                "Criticality": criticality,
                "Match Type": "No match"
            })
        i += 1

    # Excel non couverts : chercher le texte HTML le plus proche pour chaque
    for idx, ex in enumerate(excel_wordings):
        if idx not in matched_excel_counts:
            # Chercher le texte HTML le plus proche (parmi tous, meme deja consommes)
            e_norm = ex["norm"]
            e_curr = ex["curr"]
            best_h_idx = None
            best_h_sim = 0.0
            for hi, html_item in enumerate(html_texts):
                h_norm = normalize(html_item["text"])
                h_curr = normalize_currency(html_item["text"])
                sim = difflib.SequenceMatcher(None, e_norm.lower(), h_norm.lower()).ratio()
                if len(h_norm) >= 8 and len(e_norm) >= 8:
                    ratio = min(len(h_norm), len(e_norm)) / max(len(h_norm), len(e_norm))
                    if ratio >= 0.25 and (h_norm.lower() in e_norm.lower() or
                                           e_norm.lower() in h_norm.lower()):
                        sim = max(sim, 0.70)
                if e_curr and h_curr:
                    sim2 = difflib.SequenceMatcher(None, e_curr.lower(), h_curr.lower()).ratio()
                    sim = max(sim, sim2)
                if sim > best_h_sim:
                    best_h_sim = sim
                    best_h_idx = hi

            if best_h_idx is not None and best_h_sim >= 0.55:
                closest_html = html_texts[best_h_idx]
                results.append({
                    "Component Name": ex["Component"],
                    "Element": ex["Element"],
                    "Excel Wording": ex["Wording"],
                    "HTML Wording": closest_html["text"],
                    "Status": "KO",
                    "Error Type": "Wording differs",
                    "Similarity": f"{round(best_h_sim * 100)}%",
                    "Criticality": "High",
                    "Match Type": "Closest match"
                })
            else:
                results.append({
                    "Component Name": ex["Component"],
                    "Element": ex["Element"],
                    "Excel Wording": ex["Wording"],
                    "HTML Wording": "",
                    "Status": "KO",
                    "Error Type": "Not in HTML",
                    "Similarity": "0%",
                    "Criticality": "Low",
                    "Match Type": "No match"
                })

    # Deduplication des KO : supprimer les doublons HTML+Excel identiques
    # (peut arriver quand un HTML non matche et un Excel non matche pointent l'un vers l'autre)
    seen_pairs = set()
    deduped = []
    for r in results:
        key = (str(r.get("HTML Wording", "")), str(r.get("Excel Wording", "")))
        if r["Status"] == "KO" and key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(r)
    results = deduped


    total = len(results)
    ok = sum(1 for r in results if r["Status"] == "OK")


    for r in results:
        for idx, count in matched_excel_counts.items():
            if r["Excel Wording"] == excel_wordings[idx]["Wording"]:
                r["Occurrences"] = count


    print("\n" + "=" * 70)
    print(f"✅ Total: {total} | OK: {ok} | KO: {total-ok} | "
          f"Score: {round((ok/total)*100, 2) if total else 0}%")
    print("=" * 70)
    return results
