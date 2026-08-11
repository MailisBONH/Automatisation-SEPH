import difflib

from qa.config import MIN_TEXT_LENGTH
from qa.text_utils import normalize, normalize_currency


# =========================
# COMPARAISON
# =========================
def compare(html_text: str, excel_text: str) -> tuple:
    """
    Retour :
    (Status, ErrorType, Similarity, MatchType)
    """

    h = normalize(html_text)
    e = normalize(excel_text)

    # -----------------------------
    # Cas vides
    # -----------------------------
    if not h and not e:
        return ("OK", None, 100, "Empty")

    if not h or not e:
        return ("KO", "Text differs", 0, "Empty mismatch")

    # -----------------------------
    # Exact match uniquement
    # -----------------------------
    if h == e:
        return ("OK", None, 100, "Exact")

    # -----------------------------
    # Différence de casse uniquement
    # -----------------------------
    if h.lower() == e.lower():
        return ("KO", "Case Error", 99, "Case diff")

    # -----------------------------
    # Similarité principale
    # -----------------------------
    sim = difflib.SequenceMatcher(
        None,
        h.lower(),
        e.lower()
    ).ratio()

    similarity = round(sim * 100)

    # Très proche = Typo
    if sim >= 0.95:
        return ("KO", "Typo", similarity, "Near exact")

    # Proche = Typo
    if sim >= 0.80:
        return ("KO", "Typo", similarity, "Fuzzy")

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

        if sim2 >= 0.95:
            return ("KO", "Typo", similarity2, "Currency typo")

    # -----------------------------
    # Tout le reste
    # -----------------------------
    return (
        "KO",
        "Text differs",
        similarity,
        "No match"
    )


# =========================
# MATCHING
# =========================
def find_best_match(html_item: dict, excel_wordings: list, matched_excel_idx: set):
    """
    Cherche uniquement parmi les wordings Excel dont la classe technique
    correspond à la component_class de l'élément HTML.
    Si aucun wording de la bonne classe ne matche → None (sera traité comme KO).
    """
    found_class = html_item.get("component_class")
    h_text = html_item["text"]
    best = None

    def _scan(indices):
        nonlocal best
        for idx in indices:
            ex = excel_wordings[idx]

            # ── NOUVEAU : filtrage strict par classe ──────────────────────────
            # On ne compare que les wordings dont la classe attendue correspond
            # à la classe du composant HTML où le texte a été trouvé.
            if ex.get("Technical") != found_class:
                continue
            # ─────────────────────────────────────────────────────────────────

            status, error, sim, match_type = compare(h_text, ex["Wording"])

            if best is None or sim > best[3]:
                best = (idx, status, error, sim)

            if status == "OK":
                return (idx, status, error, sim)

    _scan(range(len(excel_wordings)))
    return best


def find_aggregated_match(html_items, start_idx, excel_wordings, matched_excel_idx, max_window=3):
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

        best = find_best_match(chunk_item, excel_wordings, matched_excel_idx)
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
        e_norm = ex["norm"]
        if not e_norm:
            continue

        expected_class = ex.get("Technical")
        if found_class and expected_class and found_class != expected_class:
            continue

        ratio = len(h_norm) / len(e_norm) if len(e_norm) > 0 else 0
        if h_norm.lower() in e_norm.lower() and ratio >= min_ratio:
            return (idx, "OK", "", round(ratio * 100))

    return None


def find_closest_match(html_item: dict, excel_wordings: list, matched_excel_idx: set,
                       min_sim: float = 0.55):
    """
    Cherche le wording Excel le plus similaire au texte HTML, meme en dessous du
    seuil OK. Utilise pour les KO 'Wording differs' afin d'afficher le wording
    attendu en face du wording HTML incorrect.

    - Cherche d'abord via normalize() standard
    - Puis via normalize_currency() en fallback
    - Retourne (idx, sim) si la meilleure similarite >= min_sim, sinon None.
    """

    h_text = html_item["text"]
    found_class = html_item.get("component_class")

    h_norm = normalize(h_text)
    h_curr = normalize_currency(h_text)
    if not h_norm:
        return None

    best_idx = None
    best_sim = 0.0

    for idx, ex in enumerate(excel_wordings):


        expected_class = ex.get("Technical")

        if found_class and expected_class:
            if found_class != expected_class:
                continue


        # On inclut les cellules deja matchees : le "Not in HTML" peut pointer
        # vers une cellule deja matchee par un autre element HTML.
        e_norm = ex["norm"]
        e_curr = ex["curr"]

        sim = difflib.SequenceMatcher(None, h_norm.lower(), e_norm.lower()).ratio()

        # Containment partiel (meme ratio relache)
        if len(h_norm) >= 8 and len(e_norm) >= 8:
            ratio = min(len(h_norm), len(e_norm)) / max(len(h_norm), len(e_norm))
            if ratio >= 0.25 and (h_norm.lower() in e_norm.lower() or
                                   e_norm.lower() in h_norm.lower()):
                sim = max(sim, 0.70)

        # Fallback devise
        if e_curr and h_curr:
            sim2 = difflib.SequenceMatcher(None, h_curr.lower(), e_curr.lower()).ratio()
            sim = max(sim, sim2)

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


def make_row(ex, html_item, html_text, status, error, sim, match_type):
    crit = "" if status == "OK" else ("Medium" if error == "Typo" else "High")
    expected_class = ex.get("Technical")
    found_class = html_item.get("component_class")

    return {
        "Component Name": ex["Component"],
        "Element": ex["Element"],
        "Expected Class": expected_class,
        "HTML Class": found_class,
        "Excel Wording": ex["Wording"],
        "HTML Wording": html_text,
        "Status": status,
        "Error Type": error,
        "Similarity": f"{round(sim, 2)}%",
        "Criticality": crit,
        "Match Type": match_type,
        "Occurrences": ""  # placeholder
    }
