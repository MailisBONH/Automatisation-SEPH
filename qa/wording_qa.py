from collections import defaultdict

from qa.config import (
    SIM_CLOSEST_MATCH_ACCEPT,
    SIM_CLOSEST_MATCH_MIN,
    SIM_SHARED_BRANCH_EXACT,
    KO_WRONG_COMPONENT,
    KO_COMPONENT_CLASS_NOT_FOUND,
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


def _unexpected_wording_row(h_text: str, expected_class, found_class, html_order: int) -> dict:
    """
    Ligne KO "Unexpected wording (not in Excel)" pour un fragment HTML dont
    aucun wording Excel de la meme classe n'atteint le seuil minimal de
    similarite (SIM_CLOSEST_MATCH_MIN). Utilisee a la place du "meilleur
    effort" de compare() (Direct/Agrege) quand ce dernier est sous ce seuil :
    au-dessous, le candidat "le moins pire" n'a aucune valeur informative -
    ce n'est pas un wording legerement incorrect a corriger, juste le moins
    eloigne parmi des non-matchs (voir find_best_match). Afficher ce
    rapprochement arbitraire comme "Content mismatch contre tel wording"
    induit en erreur ; autant dire honnetement qu'aucune correspondance
    plausible n'a ete trouvee.
    """
    return {
        "Component Name": "",
        "Element": "",
        "Source Sheet": "",
        "Expected Class": expected_class or "",
        "HTML Class": found_class or "none",
        "Excel Wording": "",
        "HTML Wording": clean_display_text(h_text),
        "Status": "KO",
        "Error Type": KO_UNEXPECTED_WORDING,
        "Similarity": "0%",
        "Criticality": criticality_for(KO_UNEXPECTED_WORDING),
        "Match Type": "No match",
        "_html_order": html_order
    }


def _best_effort_row(direct_or_agg, excel_wordings, item, text, expected_class, found_class, html_order, match_type):
    """
    Ligne "meilleur effort" pour un candidat Direct ou Agregation qui n'a
    pas atteint OK (voir run_wording_qa, blocs A/B) : sous le seuil minimal
    SIM_CLOSEST_MATCH_MIN, ce candidat n'a aucune valeur diagnostique -
    l'afficher associe a ce wording precis serait trompeur (voir
    _unexpected_wording_row) ; au-dessus, on rapporte le KO tel quel.
    Factorise ce qui etait duplique a l'identique entre le fallback Direct
    et le fallback Agregation.
    """
    idx, status, error, sim = direct_or_agg
    if sim < SIM_CLOSEST_MATCH_MIN * 100:
        return _unexpected_wording_row(text, expected_class, found_class, html_order)
    row = make_row(excel_wordings[idx], item, text, status, error, sim, match_type)
    row["_html_order"] = html_order
    return row


def _closest_match_row(item, h_text, html_order, excel_wordings, matched_excel_counts):
    """
    Bloc D de run_wording_qa : aucun match exact (Direct/Agregation/Split)
    n'a ete trouve pour ce fragment HTML - cherche le wording Excel non
    encore matche le plus proche (find_closest_match), et decide du statut
    final (OK/KO) selon la coherence de classe technique et le seuil
    d'acceptation SIM_CLOSEST_MATCH_ACCEPT :
    - classes connues et differentes -> KO_WRONG_COMPONENT (le composant
      est probablement le mauvais, meme si le texte est proche).
    - classe HTML non detectee (ex. titre generique non resolu) -> KO
      COMPONENT_CLASS_NOT_FOUND, meme si la similarite textuelle est
      excellente : impossible de confirmer le composant.
    - sinon, OK au-dessus du seuil, KO_WORDING_MISMATCH en-dessous.
    Retombe sur _unexpected_wording_row si aucun closest-match plausible
    n'existe (aucun wording Excel n'atteint SIM_CLOSEST_MATCH_MIN).
    """
    expected_class = ""
    found_class = item.get("component_class", "")

    closest = find_closest_match(item, excel_wordings, matched_excel_counts)
    if not closest:
        return _unexpected_wording_row(h_text, expected_class, found_class, html_order)

    ex_closest = excel_wordings[closest["idx"]]
    c_sim = closest["similarity"]

    expected_class = ex_closest.get("Technical")
    found_class = item.get("component_class")

    class_known = bool(expected_class) and bool(found_class)
    class_mismatch = class_known and expected_class != found_class

    if class_mismatch:
        status, error_type = "KO", KO_WRONG_COMPONENT
    elif not found_class:
        status, error_type = "KO", KO_COMPONENT_CLASS_NOT_FOUND
    elif c_sim >= SIM_CLOSEST_MATCH_ACCEPT:
        status, error_type = "OK", ""
    else:
        status, error_type = "KO", KO_WORDING_MISMATCH

    criticality = "" if status == "OK" else criticality_for(error_type)
    match_type = "Closest Match Accepted" if status == "OK" else "Closest Match"

    return {
        "Component Name": ex_closest["Component"],
        "Element": ex_closest["Element"],
        "Source Sheet": ex_closest.get("Sheet", ""),
        "Expected Class": expected_class,
        "HTML Class": found_class or "none",
        "Excel Wording": clean_display_text(ex_closest["Wording"], h_text),
        "HTML Wording": clean_display_text(h_text, ex_closest["Wording"]),
        "Status": status,
        "Error Type": error_type,
        "Similarity": f"{round(c_sim * 100)}%",
        "Criticality": criticality,
        "Match Type": match_type,
        "_html_order": html_order
    }


def _shared_conditional_branch_row(ex, excel_display, html_display, found_class, best_h_sim, best_h_idx) -> dict:
    """Ligne "Shared conditional branch" (KO_COMPONENT_CLASS_NOT_FOUND si la
    classe HTML est inconnue, sinon OK) - voir _unmatched_excel_rows pour le
    contexte complet de ce cas."""
    if found_class:
        status, error_type, criticality = "OK", "", ""
    else:
        status = "KO"
        error_type = KO_COMPONENT_CLASS_NOT_FOUND
        criticality = criticality_for(KO_COMPONENT_CLASS_NOT_FOUND)

    return {
        "Component Name": ex["Component"],
        "Element": ex["Element"],
        "Source Sheet": ex.get("Sheet", ""),
        "Expected Class": ex.get("Technical"),
        "HTML Class": found_class or "none",
        "Excel Wording": excel_display,
        "HTML Wording": html_display,
        "Status": status,
        "Error Type": error_type,
        "Similarity": f"{round(best_h_sim * 100)}%",
        "Criticality": criticality,
        "Match Type": "Shared conditional branch",
        "_html_order": best_h_idx
    }


def _unmatched_excel_row(ex, html_texts, ko_pairs) -> dict:
    """
    Pour un wording Excel jamais matche par la boucle principale (voir
    _unmatched_excel_rows) : cherche le fragment HTML le plus proche
    (find_closest_html_match). Sans aucun candidat -> KO_MISSING_WORDING.
    Avec un candidat tres proche (SIM_SHARED_BRANCH_EXACT) sur la meme
    classe (ou sans classe HTML connue) -> cas d'une branche if/else
    "soeur" deja couverte par un fragment partage, voir
    _shared_conditional_branch_row et le commentaire de
    _unmatched_excel_rows. Sinon -> KO_WORDING_RELOCATED (wording
    introuvable, mais texte proche trouve ailleurs).
    """
    closest_html = find_closest_html_match(ex, html_texts)
    if closest_html is None:
        return {
            "Component Name": ex["Component"],
            "Element": ex["Element"],
            "Source Sheet": ex.get("Sheet", ""),
            "Expected Class": ex.get("Technical"),
            "HTML Class": "none",
            "Excel Wording": clean_display_text(ex["Wording"]),
            "HTML Wording": "",
            "Status": "KO",
            "Error Type": KO_MISSING_WORDING,
            "Similarity": "0%",
            "Criticality": criticality_for(KO_MISSING_WORDING),
            "Match Type": "No match",
            "_html_order": len(html_texts)
        }

    best_h_idx, best_h_sim = closest_html
    closest_item = html_texts[best_h_idx]
    excel_display = clean_display_text(ex["Wording"], closest_item["text"])
    html_display = clean_display_text(closest_item["text"], ex["Wording"])

    # Cas d'un Excel qui modelise les 2 branches d'un meme
    # <% if %>...<% } else { %>...<% } %> en 2 lignes separees, alors qu'un
    # seul fragment HTML "partage" (meme classe technique) satisfait les
    # deux : la branche non "consommee" en premier existe bel et bien,
    # litteralement, dans ce fragment - ce n'est pas un ecart reel. Seuil
    # volontairement quasi-parfait (SIM_SHARED_BRANCH_EXACT, pas
    # SIM_CLOSEST_MATCH_ACCEPT) : un match seulement "tres bon" (ex: 98% a
    # cause d'une ponctuation en trop) reflete un vrai ecart sur CETTE
    # branche precise, qui ne doit pas etre masque juste parce qu'une
    # branche soeur a bien matche.
    #
    # Si la classe HTML n'a pas ete detectee (ex. titre generique non
    # resolu), on ne peut plus se contenter du seuil de similarite
    # quasi-parfait pour valider en OK : on ne peut pas confirmer le
    # composant, donc c'est un KO explicite (KO_COMPONENT_CLASS_NOT_FOUND)
    # plutot qu'une hypothese optimiste. Un KO_WRONG_COMPONENT reste pose
    # par _closest_match_row (pas ici) quand les deux classes sont connues
    # mais differentes.
    expected_class = ex.get("Technical")
    found_class = closest_item.get("component_class")
    class_known = bool(expected_class) and bool(found_class)
    same_class = class_known and expected_class == found_class
    shared_branch = (
        (not found_class or same_class)
        and best_h_sim >= SIM_SHARED_BRANCH_EXACT
        and (excel_display, html_display) not in ko_pairs
    )

    if shared_branch:
        return _shared_conditional_branch_row(ex, excel_display, html_display, found_class, best_h_sim, best_h_idx)

    return {
        "Component Name": ex["Component"],
        "Element": ex["Element"],
        "Source Sheet": ex.get("Sheet", ""),
        "Expected Class": ex.get("Technical"),
        "HTML Class": found_class or "none",
        "Excel Wording": excel_display,
        "HTML Wording": html_display,
        "Status": "KO",
        "Error Type": KO_WORDING_RELOCATED,
        "Similarity": f"{round(best_h_sim * 100)}%",
        "Criticality": criticality_for(KO_WORDING_RELOCATED),
        "Match Type": "Closest match",
        "_html_order": best_h_idx
    }


def _unmatched_excel_rows(excel_wordings, html_texts, matched_excel_counts, results_so_far) -> list:
    """
    Section finale de run_wording_qa : une ligne par wording Excel jamais
    matche par la boucle principale (idx absent de matched_excel_counts),
    voir _unmatched_excel_row. ko_pairs (KO deja rapportes par la boucle
    principale) est fige au moment de l'appel, a partir de results_so_far :
    une meme cellule Excel peut deja avoir un KO "best-effort" pose par
    _best_effort_row/_closest_match_row (ce KO ne marque pas
    matched_excel_counts, pour laisser une chance a un fragment HTML
    ulterieur) ; si le fragment le plus proche trouve ici correspond
    EXACTEMENT a ce meme KO deja rapporte, on ne le repromeut pas en OK - le
    KO existant reflete deja un vrai ecart sur ce texte precis, pas une
    simple branche if/else non consommee.
    """
    ko_pairs = {
        (r.get("Excel Wording"), r.get("HTML Wording"))
        for r in results_so_far if r["Status"] == "KO"
    }

    return [
        _unmatched_excel_row(ex, html_texts, ko_pairs)
        for idx, ex in enumerate(excel_wordings)
        if idx not in matched_excel_counts
    ]


def _dedupe_by_status_and_pair(results: list) -> list:
    """
    Deduplique les lignes identiques (meme statut, meme paire HTML/Excel
    affichee) : peut arriver par ex. quand un fragment deja valide via
    Split ou Direct (qui ne "consomme" pas forcement la cellule Excel, voir
    find_split_match) est aussi retrouve comme wording "non couvert" par
    _unmatched_excel_rows et repromu separement - meme constat pour un HTML
    et un Excel non matches qui pointent l'un vers l'autre. Garde la 1ere
    occurrence rencontree, peu importe le Match Type exact.
    """
    seen_pairs = set()
    deduped = []
    for r in results:
        key = (r["Status"], str(r.get("Excel Wording", "")), str(r.get("HTML Wording", "")))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(r)
    return deduped


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
            # find_split_match ne verifie la classe technique que si les DEUX
            # cotes en ont une connue (voir sa docstring) : si le HTML n'a
            # aucune classe detectee, on ne peut pas confirmer le composant,
            # donc pas de OK sans preuve - meme logique que le closest-match
            # ci-dessous.
            if found_class:
                row = make_row(excel_wordings[idx], item, h_text, "OK", "", sim, "Split (HTML ⊂ Excel)")
            else:
                row = make_row(excel_wordings[idx], item, h_text, "KO", KO_COMPONENT_CLASS_NOT_FOUND, sim, "Split (HTML ⊂ Excel)")
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
            results.append(_best_effort_row(direct, excel_wordings, item, h_text, expected_class, found_class, i, "Direct"))
            i += 1
            continue

        if agg:
            nb = agg[0]
            agg_text = " ".join(html_texts[i + k]["text"] for k in range(nb))
            results.append(_best_effort_row(
                agg[1:], excel_wordings, item, agg_text, expected_class, found_class, i, f"Agrege ({nb} blocs)"
            ))
            for k in range(nb):
                html_consumed.add(i + k)
            i += nb
            continue

        # D) Aucun match exact — chercher le wording Excel le plus proche
        results.append(_closest_match_row(item, h_text, i, excel_wordings, matched_excel_counts))
        i += 1

    # Excel non couverts : chercher le texte HTML le plus proche pour chacun.
    results += _unmatched_excel_rows(excel_wordings, html_texts, matched_excel_counts, results)

    # Supprime les KO dont le texte HTML est deja couvert par un match OK
    # ailleurs (fragment redondant d'un bloc plus large deja valide).
    results = _drop_ko_covered_by_ok(results)
    results = _dedupe_by_status_and_pair(results)

    total = len(results)
    ok = sum(1 for r in results if r["Status"] == "OK")

    print("\n" + "=" * 70)
    print(f"✅ Total: {total} | OK: {ok} | KO: {total-ok} | "
          f"Score: {round((ok/total)*100, 2) if total else 0}%")
    print("=" * 70)
    return results
