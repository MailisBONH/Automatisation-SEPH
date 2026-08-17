from collections import defaultdict

from qa.config import (
    SIM_CLOSEST_MATCH_ACCEPT,
    SIM_SHARED_BRANCH_EXACT,
    KO_WRONG_COMPONENT,
    KO_WORDING_MISMATCH,
    KO_WORDING_RELOCATED,
    KO_UNEXPECTED_WORDING,
    KO_MISSING_WORDING,
)
from qa.matching import (
    criticality_for,
    find_aggregated_match,
    find_best_match,
    find_closest_html_match,
    find_closest_match,
    find_split_match,
    make_row,
)
from qa.text_utils import clean_display_text, normalize


def _drop_ko_covered_by_ok(results: list, min_length: int = 5) -> list:
    """
    Supprime les lignes KO dont le texte HTML est deja entierement contenu
    dans le texte HTML d'une autre ligne validee OK. Ce cas se produit quand
    un meme contenu est extrait a la fois en un seul bloc complet (qui
    matche correctement) et en fragments plus courts qui, compares
    individuellement, ne matchent pas leur propre wording Excel : le
    fragment court est deja couvert par le bloc valide, ce n'est pas un
    ecart reel a signaler separement.
    """
    ok_html_norms = [
        normalize(r["HTML Wording"]).lower()
        for r in results
        if r["Status"] == "OK" and r.get("HTML Wording")
    ]
    ok_html_norms = [t for t in ok_html_norms if t]

    filtered = []
    for r in results:
        if r["Status"] == "KO":
            html_norm = normalize(str(r.get("HTML Wording", ""))).lower()
            if len(html_norm) >= min_length and any(
                html_norm in ok and html_norm != ok for ok in ok_html_norms
            ):
                continue
        filtered.append(r)
    return filtered


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


        # A) Matching direct — on ne verrouille ce resultat que s'il est OK.
        # Un candidat de la bonne classe mais textuellement tres different
        # (KO) ne doit pas empecher d'essayer l'agregation/le split, qui
        # peuvent trouver un meilleur match pour ce meme fragment HTML.
        direct = find_best_match(item, excel_wordings, set(matched_excel_counts.keys()))
        if direct and direct[1] == "OK":
            idx, status, error, sim = direct
            row = make_row(excel_wordings[idx], item, h_text, status, error, sim, "Direct")
            row["_html_order"] = i
            results.append(row)
            matched_excel_counts[idx] += 1
            i += 1
            continue

        # B) Agregation (N HTML → 1 Excel) — meme logique : on ne verrouille
        # que si le resultat est OK.
        agg = find_aggregated_match(html_texts, i, excel_wordings, matched_excel_counts)
        if agg and agg[2] == "OK":
            nb, idx, status, error, sim = agg
            agg_text = " ".join(html_texts[i + k]["text"] for k in range(nb))
            row = make_row(excel_wordings[idx], item, agg_text, status, error, sim, f"Agrege ({nb} blocs)")
            row["_html_order"] = i
            results.append(row)
            matched_excel_counts[idx] += 1
            for k in range(nb):
                html_consumed.add(i + k)
            i += nb
            continue

        # C) Split (HTML ⊂ Excel)
        split = find_split_match(item, excel_wordings, matched_excel_counts)
        if split:
            idx, status, error, sim = split
            row = make_row(excel_wordings[idx], item, h_text, "OK", "", sim, "Split (HTML ⊂ Excel)")
            row["_html_order"] = i
            results.append(row)
            i += 1
            continue

        # Ni Direct, ni Agregation, ni Split n'ont trouve de OK : on retombe
        # sur le meilleur KO deja trouve (Direct en priorite, puis
        # Agregation) plutot que de l'ignorer et de sauter directement au
        # closest-match generique. On ne marque PAS matched_excel_counts ici
        # (contrairement au cas OK) : ce KO ne "consomme" pas la cellule
        # Excel, un autre fragment HTML (plus tard dans la boucle) doit
        # pouvoir encore la matcher correctement via Direct/Agregation/Split.
        if direct:
            idx, status, error, sim = direct
            row = make_row(excel_wordings[idx], item, h_text, status, error, sim, "Direct")
            row["_html_order"] = i
            results.append(row)
            i += 1
            continue

        if agg:
            nb, idx, status, error, sim = agg
            agg_text = " ".join(html_texts[i + k]["text"] for k in range(nb))
            row = make_row(excel_wordings[idx], item, agg_text, status, error, sim, f"Agrege ({nb} blocs)")
            row["_html_order"] = i
            results.append(row)
            for k in range(nb):
                html_consumed.add(i + k)
            i += nb
            continue

        # D) Aucun match exact — chercher le wording Excel le plus proche
        closest = find_closest_match(item, excel_wordings, matched_excel_counts)
        if closest:

            c_idx = closest["idx"]
            c_sim = closest["similarity"]

            ex_closest = excel_wordings[c_idx]

            expected_class = ex_closest.get("Technical")
            found_class = item.get("component_class")

            class_match = expected_class == found_class

            if not class_match:
                status = "KO"
                error_type = KO_WRONG_COMPONENT

            elif c_sim >= SIM_CLOSEST_MATCH_ACCEPT:
                status = "OK"
                error_type = ""

            else:
                status = "KO"
                error_type = KO_WORDING_MISMATCH

            criticality = "" if status == "OK" else criticality_for(error_type)

            if status == "OK":
                match_type = "Closest Match Accepted"
            else:
                match_type = "Closest Match"

            results.append({
                "Component Name": ex_closest["Component"],
                "Element": ex_closest["Element"],
                "Expected Class": expected_class,
                "HTML Class": found_class,
                "Excel Wording": clean_display_text(ex_closest["Wording"], h_text),
                "HTML Wording": clean_display_text(h_text, ex_closest["Wording"]),
                "Status": status,
                "Error Type": error_type,
                "Similarity": f"{round(c_sim * 100)}%",
                "Criticality": criticality,
                "Match Type": match_type,
                "_html_order": i
            })
        else:
            results.append({
                "Component Name": "",
                "Element": "",
                "Expected Class": expected_class or "",
                "HTML Class": found_class or "",
                "Excel Wording": "",
                "HTML Wording": clean_display_text(h_text),
                "Status": status,
                "Error Type": KO_UNEXPECTED_WORDING,
                "Similarity": "0%",
                "Criticality": criticality_for(KO_UNEXPECTED_WORDING),
                "Match Type": "No match",
                "_html_order": i
            })
        i += 1

    # Une meme cellule Excel peut deja avoir un KO "best-effort" pose par le
    # bloc A/D ci-dessus (voir commentaire plus haut : ce KO ne marque pas
    # matched_excel_counts, pour laisser une chance a un fragment HTML
    # ulterieur). Si le fragment le plus proche trouve ci-dessous est
    # EXACTEMENT ce meme KO deja rapporte, on ne le repromeut pas en OK : le
    # KO existant reflete deja un vrai ecart (ex: ponctuation) sur ce texte
    # precis, pas une simple branche if/else non consommee.
    ko_pairs = {
        (r.get("Excel Wording"), r.get("HTML Wording"))
        for r in results if r["Status"] == "KO"
    }

    # Excel non couverts : chercher le texte HTML le plus proche pour chaque
    for idx, ex in enumerate(excel_wordings):
        if idx not in matched_excel_counts:
            closest_html = find_closest_html_match(ex, html_texts)

            if closest_html is not None:
                best_h_idx, best_h_sim = closest_html
                closest_item = html_texts[best_h_idx]
                excel_display = clean_display_text(ex["Wording"], closest_item["text"])
                html_display = clean_display_text(closest_item["text"], ex["Wording"])

                # Cas d'un Excel qui modelise les 2 branches d'un meme
                # <% if %>...<% } else { %>...<% } %> en 2 lignes separees,
                # alors qu'un seul fragment HTML "partage" (meme classe
                # technique) satisfait les deux : la branche non
                # "consommee" en premier existe bel et bien, litteralement,
                # dans ce fragment - ce n'est pas un ecart reel. Seuil
                # volontairement quasi-parfait (SIM_SHARED_BRANCH_EXACT, pas
                # SIM_CLOSEST_MATCH_ACCEPT) : un match seulement "tres bon"
                # (ex: 98% a cause d'une ponctuation en trop) reflete un vrai
                # ecart sur CETTE branche precise, qui ne doit pas etre
                # masque juste parce qu'une branche soeur a bien matche.
                same_class = (
                    bool(ex.get("Technical"))
                    and closest_item.get("component_class") == ex.get("Technical")
                )

                if (
                    same_class
                    and best_h_sim >= SIM_SHARED_BRANCH_EXACT
                    and (excel_display, html_display) not in ko_pairs
                ):
                    results.append({
                        "Component Name": ex["Component"],
                        "Element": ex["Element"],
                        "Expected Class": ex.get("Technical"),
                        "HTML Class": closest_item.get("component_class"),
                        "Excel Wording": excel_display,
                        "HTML Wording": html_display,
                        "Status": "OK",
                        "Error Type": "",
                        "Similarity": f"{round(best_h_sim * 100)}%",
                        "Criticality": "",
                        "Match Type": "Shared conditional branch",
                        "_html_order": best_h_idx
                    })
                    continue

                results.append({
                    "Component Name": ex["Component"],
                    "Element": ex["Element"],
                    "Expected Class": ex.get("Technical"),
                    "HTML Class": closest_item.get("component_class"),
                    "Excel Wording": excel_display,
                    "HTML Wording": html_display,
                    "Status": "KO",
                    "Error Type": KO_WORDING_RELOCATED,
                    "Similarity": f"{round(best_h_sim * 100)}%",
                    "Criticality": criticality_for(KO_WORDING_RELOCATED),
                    "Match Type": "Closest match",
                    "_html_order": best_h_idx
                })
            else:
                results.append({
                    "Component Name": ex["Component"],
                    "Element": ex["Element"],
                    "Expected Class": ex.get("Technical"),
                    "HTML Class": "",
                    "Excel Wording": clean_display_text(ex["Wording"]),
                    "HTML Wording": "",
                    "Status": "KO",
                    "Error Type": KO_MISSING_WORDING,
                    "Similarity": "0%",
                    "Criticality": criticality_for(KO_MISSING_WORDING),
                    "Match Type": "No match",
                    "_html_order": len(html_texts)
                })

    # Supprime les KO dont le texte HTML est deja couvert par un match OK
    # ailleurs (fragment redondant d'un bloc plus large deja valide).
    results = _drop_ko_covered_by_ok(results)

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
