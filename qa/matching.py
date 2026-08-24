import difflib

from qa.config import (
    MIN_TEXT_LENGTH,
    SIM_NEAR_EXACT,
    SIM_FUZZY,
    SIM_CLOSEST_MATCH_MIN,
    SIM_CLOSEST_MATCH_ACCEPT,
    KO_TEXT_MISSING,
    KO_CASE_MISMATCH,
    KO_MINOR_TYPO,
    KO_FUZZY_MISMATCH,
    KO_CURRENCY_FORMAT_MISMATCH,
    KO_CONTENT_MISMATCH,
    CRITICALITY_BY_ERROR_TYPE,
    DEFAULT_KO_CRITICALITY,
)
from qa.text_utils import clean_display_text, expand_conditional_variants, normalize, normalize_currency


def criticality_for(error_type: str) -> str:
    """Criticite associee a un type de KO (voir CRITICALITY_BY_ERROR_TYPE)."""
    return CRITICALITY_BY_ERROR_TYPE.get(error_type, DEFAULT_KO_CRITICALITY)


# =========================
# COMPARAISON
# =========================
def compare(html_text: str, excel_text: str) -> tuple:
    """
    Retour :
    (Status, ErrorType, Similarity, MatchType)

    Si l'un des deux textes contient un bloc conditionnel AEM
    (<% if(...) { %>...<% } else { %>...<% } %>), chaque branche possible
    est comparee independamment (voir expand_conditional_variants) : un
    match sur N'IMPORTE QUELLE combinaison de branches compte comme un
    match, puisque le rendu final n'affiche qu'une seule branche a la fois.
    Retourne la premiere combinaison OK trouvee, sinon la plus similaire.
    """
    html_variants = expand_conditional_variants(html_text)
    excel_variants = expand_conditional_variants(excel_text)

    best = None
    for hv in html_variants:
        for ev in excel_variants:
            result = _compare_single(hv, ev)
            if result[0] == "OK":
                return result
            if best is None or result[2] > best[2]:
                best = result
    return best


def _compare_single(html_text: str, excel_text: str) -> tuple:
    h = normalize(html_text)
    e = normalize(excel_text)

    # -----------------------------
    # Cas vides
    # -----------------------------
    if not h and not e:
        return ("OK", None, 100, "Empty")

    if not h or not e:
        return ("KO", KO_TEXT_MISSING, 0, "Empty mismatch")

    # -----------------------------
    # Exact match uniquement
    # -----------------------------
    if h == e:
        return ("OK", None, 100, "Exact")

    # -----------------------------
    # Différence de casse uniquement
    # -----------------------------
    if h.lower() == e.lower():
        return ("KO", KO_CASE_MISMATCH, 99, "Case diff")

    # -----------------------------
    # Similarité principale
    # -----------------------------
    sim = difflib.SequenceMatcher(
        None,
        h.lower(),
        e.lower()
    ).ratio()

    similarity = round(sim * 100)

    if sim >= SIM_NEAR_EXACT:
        return ("KO", KO_MINOR_TYPO, similarity, "Near exact")

    if sim >= SIM_FUZZY:
        return ("KO", KO_FUZZY_MISMATCH, similarity, "Fuzzy")

    # -----------------------------
    # Cas spécifique devises
    # -----------------------------
    hc = normalize_currency(html_text)
    ec = normalize_currency(excel_text)

    if hc and ec:

        if hc == ec:
            return ("OK", None, 100, "Currency exact")

        sim2 = difflib.SequenceMatcher(
            None,
            hc.lower(),
            ec.lower()
        ).ratio()

        similarity2 = round(sim2 * 100)

        if sim2 >= SIM_NEAR_EXACT:
            return ("KO", KO_CURRENCY_FORMAT_MISMATCH, similarity2, "Currency format")

    # -----------------------------
    # Tout le reste
    # -----------------------------
    return (
        "KO",
        KO_CONTENT_MISMATCH,
        similarity,
        "No match"
    )


# =========================
# MATCHING
# =========================
def index_wordings_by_technical_class(excel_wordings: list) -> dict:
    """
    Regroupe les indices de excel_wordings par classe technique (Technical),
    en ignorant les wordings sans classe connue. A precalculer UNE fois par
    run_wording_qa (voir qa.wording_qa) et passer a find_best_match via son
    parametre class_index, pour eviter de reparcourir l'integralite de
    excel_wordings pour chaque fragment HTML - utile des que le referentiel
    est volumineux (ex: fusion des composants generiques, voir
    include_generic dans qa.engine).
    """
    index = {}
    for idx, ex in enumerate(excel_wordings):
        technical = ex.get("Technical")
        if technical:
            index.setdefault(technical, []).append(idx)
    return index


def find_best_match(html_item: dict, excel_wordings: list, class_index: dict = None):
    """
    Cherche uniquement parmi les wordings Excel dont la classe technique est
    CONNUE et correspond a la component_class (elle aussi connue) de
    l'element HTML. Si l'une des deux (ou les deux) est inconnue, aucun
    wording n'est considere ici (sera traite comme KO, avec une chance
    d'etre rattrape par find_closest_match qui gere ce cas plus finement).
    Si aucun wording de la bonne classe ne matche -> None (sera traite comme KO).

    Ne filtre pas par cellule Excel deja matchee (contrairement a
    find_split_match) : un meme wording Excel peut legitimement etre
    matche OK par plusieurs fragments HTML distincts (ex. un CTA repete
    deux fois dans l'email) - voir run_wording_qa dans qa.wording_qa, qui
    ne "verrouille" un resultat Direct/Agrege qu'une fois OK, sans jamais
    exclure les autres fragments HTML de retenter la meme cellule.

    class_index (optionnel) : index precalcule par index_wordings_by_technical_class,
    pour ne parcourir que les wordings de la classe recherchee au lieu de
    tout excel_wordings - strictement equivalent au parcours complet filtre
    ci-dessous (meme filtre, applique en amont plutot qu'a chaque iteration),
    juste plus rapide sur un gros referentiel. None (defaut) revient au
    parcours complet, pour compatibilite avec tout appel existant qui n'a
    pas encore cet index sous la main.
    """
    found_class = html_item.get("component_class")
    h_text = html_item["text"]
    best = None

    if not found_class:
        # Le filtre ci-dessous exige les DEUX classes connues ET egales :
        # sans classe HTML, chaque candidat serait de toute facon exclu
        # (aucune comparaison a faire, voir docstring ci-dessus).
        return None

    def _scan(indices):
        nonlocal best
        for idx in indices:
            ex = excel_wordings[idx]

            # ── NOUVEAU : filtrage strict par classe ──────────────────────────
            # On ne compare que les wordings dont la classe attendue correspond
            # a la classe du composant HTML ou le texte a ete trouve. Les DEUX
            # doivent etre connues : "None != None" est faux, donc sans cette
            # verification explicite, deux classes INCONNUES des deux cotes
            # seraient a tort traitees comme "la meme classe" (par egalite de
            # deux None) et comparees sans filtre - produisant des "meilleurs
            # candidats" arbitraires (Content mismatch) contre un texte
            # totalement sans rapport. Une classe inconnue est deja geree plus
            # loin dans le pipeline (find_closest_match), de facon bornee par
            # un seuil de similarite minimal - pas ici, ou compare() n'a aucun
            # plancher de qualite pour son suivi "meilleur effort".
            expected_class = ex.get("Technical")
            if not (expected_class and found_class == expected_class):
                continue
            # ─────────────────────────────────────────────────────────────────

            status, error, sim, match_type = compare(h_text, ex["Wording"])

            if best is None or sim > best[3]:
                best = (idx, status, error, sim)

            if status == "OK":
                return (idx, status, error, sim)

    candidate_indices = class_index.get(found_class, []) if class_index is not None else range(len(excel_wordings))
    _scan(candidate_indices)
    return best


def find_aggregated_match(html_items, start_idx, excel_wordings, max_window=3, class_index=None):
    first_item = html_items[start_idx]
    first_norm = normalize(first_item["text"])
    if first_item["tag"] == "a" and len(first_norm) < 20:
        return None

    for window in range(2, max_window + 1):
        end = start_idx + window
        if end > len(html_items):
            break

        chunk_text = " ".join(item["text"] for item in html_items[start_idx:end])
        if len(chunk_text) < MIN_TEXT_LENGTH:
            continue

        # ── NOUVEAU : construire un pseudo html_item avec la classe du premier élément ──
        chunk_item = {
            "text": chunk_text,
            "tag": first_item["tag"],
            "type": first_item["type"],
            "component_class": first_item.get("component_class")  # classe portée par le 1er bloc
        }
        # ────────────────────────────────────────────────────────────────────────────────

        best = find_best_match(chunk_item, excel_wordings, class_index)
        if best is not None:
            idx, status, error, sim = best
            target_len = len(normalize(excel_wordings[idx]["Wording"]))
            chunk_len = len(normalize(chunk_text))
            if target_len > 0 and chunk_len > target_len * 2.0:
                continue
            return (window,) + best

    return None


def find_split_match(html_item: dict, excel_wordings: list, matched_excel_idx: set, min_ratio: float = 0.40):
    """
    Matching par splitting : le texte HTML est une sous-phrase d'une cellule Excel.
    Ex: HTML = 'Get a gift ... before .'
        Excel = 'Get a gift ... before [gift validity end date]'
        Apres normalize(), Excel devient 'Get a gift ... before .'
        → egalite exacte, deja gere dans compare().

    Ce fallback couvre le cas ou la sous-phrase HTML est incluse dans l'Excel normalise
    (placeholder retire, ou fragment HTML representant une bonne partie de la cellule
    combinee). Deux garde-fous contre les faux positifs (un fragment court qui
    apparait par coincidence dans une cellule sans rapport) :
    - si le HTML et la cellule Excel ont chacun une classe technique connue et
      qu'elles different, on ignore (meme logique que find_closest_match) ;
    - le fragment HTML doit couvrir au moins min_ratio de la longueur de la
      cellule Excel (une coincidence sur un mot isole donne un ratio tres bas).

    Ne marque PAS matched_excel_idx (d'autres fragments HTML peuvent couvrir la meme cellule).
    Retourne (idx, status, error, sim) ou None. sim est un pourcentage (0-100).
    """
    h_text = html_item["text"]
    found_class = html_item.get("component_class")

    h_norm = normalize(h_text)
    if not h_norm or len(h_norm) < MIN_TEXT_LENGTH:
        return None

    for idx, ex in enumerate(excel_wordings):
        if idx in matched_excel_idx:
            continue

        expected_class = ex.get("Technical")
        if found_class and expected_class and found_class != expected_class:
            continue

        # Une cellule Excel avec bloc conditionnel a plusieurs variantes
        # (une par branche, voir expand_conditional_variants) : n'importe
        # laquelle suffit a valider le split match.
        for e_norm in ex["norm_variants"]:
            if not e_norm:
                continue
            ratio = len(h_norm) / len(e_norm)
            if h_norm.lower() in e_norm.lower() and ratio >= min_ratio:
                return (idx, "OK", "", round(ratio * 100))

    return None


def _similarity_with_boosts(a_norm: str, a_curr: str, b_norm: str, b_curr: str) -> float:
    """
    Similarite normalisee (0-1) entre deux textes deja normalises, avec deux
    ajustements pour reduire les faux negatifs (utilise par find_closest_match
    et find_closest_html_match, pour eviter de dupliquer cette logique) :
    - containment partiel : si l'un contient l'autre et qu'ils sont de
      longueur comparable (ratio >= 0.25), la similarite est remontee a 0.70 ;
    - fallback devise : si les deux textes ont une forme "devise" normalisee
      (normalize_currency), on retient la meilleure des deux similarites.
    """
    sim = difflib.SequenceMatcher(None, a_norm.lower(), b_norm.lower()).ratio()

    if len(a_norm) >= 8 and len(b_norm) >= 8:
        ratio = min(len(a_norm), len(b_norm)) / max(len(a_norm), len(b_norm))
        if ratio >= 0.25 and (a_norm.lower() in b_norm.lower() or
                               b_norm.lower() in a_norm.lower()):
            sim = max(sim, 0.70)

    if a_curr and b_curr:
        sim2 = difflib.SequenceMatcher(None, a_curr.lower(), b_curr.lower()).ratio()
        sim = max(sim, sim2)

    return sim


def _best_similarity_to_excel(html_text: str, excel_entry: dict) -> float:
    """
    Meilleure similarite entre un texte HTML (avec ses eventuelles variantes
    if/else, voir expand_conditional_variants) et un wording Excel (avec les
    siennes, precalculees dans excel_entry['norm_variants']/['curr_variants']
    par excel_loader). Essaie toutes les combinaisons de branches des deux
    cotes et retient la meilleure : le rendu final n'affiche qu'une seule
    branche a la fois de chaque cote, donc la meilleure correspondance
    possible est le signal pertinent, pas la moyenne ou la 1ere variante.
    """
    best = 0.0
    for h_raw in expand_conditional_variants(html_text):
        h_norm = normalize(h_raw)
        h_curr = normalize_currency(h_raw)
        for e_norm, e_curr in zip(excel_entry["norm_variants"], excel_entry["curr_variants"]):
            sim = _similarity_with_boosts(h_norm, h_curr, e_norm, e_curr)
            if sim > best:
                best = sim
    return best


def find_closest_match(html_item: dict, excel_wordings: list, matched_excel_idx: set,
                       min_sim: float = SIM_CLOSEST_MATCH_MIN):
    """
    Cherche le wording Excel le plus similaire au texte HTML, meme en dessous du
    seuil OK. Utilise pour les KO 'Wording mismatch'/'Wording in wrong component'
    afin d'afficher le wording attendu en face du wording HTML incorrect.

    - Cherche d'abord via normalize() standard
    - Puis via normalize_currency() en fallback
    - Retourne (idx, sim) si la meilleure similarite >= min_sim, sinon None.
    """

    h_text = html_item["text"]
    found_class = html_item.get("component_class")

    h_norm = normalize(h_text)
    if not h_norm:
        return None

    best_idx = None
    best_sim = 0.0

    for idx, ex in enumerate(excel_wordings):


        expected_class = ex.get("Technical")

        if found_class and expected_class:
            if found_class != expected_class:
                continue


        # On inclut les cellules deja matchees : le "Missing wording" peut
        # pointer vers une cellule deja matchee par un autre element HTML.
        sim = _best_similarity_to_excel(h_text, ex)

        if sim > best_sim:
            best_sim = sim
            best_idx = idx

    if best_idx is not None and best_sim >= min_sim:
        return {
            "idx": best_idx,
            "similarity": best_sim,
            "class_match": True
        }

    return None


def find_closest_html_match(excel_wording: dict, html_texts: list,
                             min_sim: float = SIM_CLOSEST_MATCH_MIN):
    """
    Symetrique de find_closest_match : pour un wording Excel non couvert par
    aucun match HTML, cherche le texte HTML le plus proche par similarite
    (tous les textes HTML sont consideres, meme deja consommes par un autre
    match), pour afficher un candidat plausible dans le rapport meme si le
    vrai wording attendu est absent. Retourne (idx dans html_texts, sim
    0-1) ou None si rien n'atteint min_sim.
    """
    best_idx = None
    best_sim = 0.0

    for idx, html_item in enumerate(html_texts):
        sim = _best_similarity_to_excel(html_item["text"], excel_wording)

        if sim > best_sim:
            best_sim = sim
            best_idx = idx

    if best_idx is not None and best_sim >= min_sim:
        return best_idx, best_sim

    return None


def make_row(ex, html_item, html_text, status, error, sim, match_type):
    crit = "" if status == "OK" else criticality_for(error)
    expected_class = ex.get("Technical")
    found_class = html_item.get("component_class")

    return {
        "Component Name": ex["Component"],
        "Element": ex["Element"],
        "Source Sheet": ex.get("Sheet", ""),
        "Expected Class": expected_class,
        "HTML Class": found_class or "none",
        "Excel Wording": clean_display_text(ex["Wording"], html_text),
        "HTML Wording": clean_display_text(html_text, ex["Wording"]),
        "Status": status,
        "Error Type": error,
        "Similarity": f"{round(sim, 2)}%",
        "Criticality": crit,
        "Match Type": match_type,
    }
