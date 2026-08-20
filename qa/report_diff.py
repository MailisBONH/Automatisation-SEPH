"""
Diff entre les 2 derniers rapports archives d'une sequence (voir
qa.archive.list_reports, deja limite a KEEP_REPORTS=2 sur disque) : au-dela
du score global (deja suivi par qa.history), identifie precisement QUELS
composants/wordings/URLs ont regresse, ete corriges, ou change de nature
d'une comparaison a l'autre - utile pour trier une regression sans devoir
comparer les deux tableaux a la main.

Purement local (relit des fichiers Excel deja generes, aucun appel reseau,
contrairement a la verification HTTP des liens abandonnee precedemment) et
deterministe : le meme couple de rapports produit toujours le meme diff.
"""
import pandas as pd

from qa.archive import list_reports
from qa.report import URL_HEADER_LABELS

# Les rapports URL archives portent des en-tetes en francais dans le fichier
# Excel (voir qa.report.URL_HEADER_LABELS, applique a l'ecriture) ; les
# rapports wording gardent les cles internes anglaises. Cette table inverse
# remet les colonnes URL sur les memes cles que le reste du pipeline
# (qa.url_qa), pour que diff_reports() n'ait besoin de connaitre qu'un seul
# jeu de noms de colonnes, quel que soit le mode.
_URL_LABEL_TO_KEY = {v: k for k, v in URL_HEADER_LABELS.items()}


def _normalize_url_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns=_URL_LABEL_TO_KEY)


def _row_key(row: dict, mode: str) -> tuple:
    """
    Identifie une ligne de rapport a travers 2 runs, independamment de son
    ordre d'apparition ou du fait que le wording/l'URL attendu ait lui-meme
    change entre les deux (auquel cas la ligne ressort en "added"/"removed"
    plutot que "changed" - un vrai changement de referentiel n'est pas la
    meme chose qu'un changement de resultat de QA).

    - wording : (Component Name, Element, Source Sheet) - le meme triplet
      qui identifie une ligne dans le referentiel Excel (voir "Format
      attendu du fichier Excel" dans le README) ; Source Sheet distingue
      un meme Component/Element porte par l'onglet sequence, l'onglet
      frere LOYAL/NON LOYAL ou l'onglet GENERIC.
    - url : la classe attendue (label du bloc UTM, ex "HEADER") est la cle
      la plus stable si la ligne est rattachee a une entree Excel ; une
      ligne "Unexpected URL (not in Excel)" n'en a pas, on retombe alors
      sur le href HTML lui-meme (rien d'autre ne l'identifie).

    Best effort, pas une garantie d'unicite absolue (ex: deux lignes
    partageant exactement la meme cle a l'interieur d'un meme run, cas rare,
    verraient la derniere l'emporter) - un diff entre 2 runs archives est un
    outil de triage, pas une preuve formelle.
    """
    if mode == "wording":
        return (row.get("Component Name") or "", row.get("Element") or "", row.get("Source Sheet") or "")

    expected_class = row.get("Expected Class")
    if expected_class:
        return ("expected", row.get("Source Section") or "", expected_class)
    return ("unexpected", row.get("Source Section") or "", row.get("HTML Wording") or "")


def diff_reports(previous_rows: list, current_rows: list, mode: str) -> dict:
    """
    Compare deux jeux de resultats (rows, dicts au format interne - voir
    qa.wording_qa/qa.url_qa) d'un meme mode ("wording" ou "url"). Retourne :
    {
        "regressed": [...],  # etait OK, est maintenant KO
        "fixed": [...],      # etait KO, est maintenant OK
        "changed": [...],    # KO->KO mais Error Type ou wording/URL trouve different
        "added": [...],      # cle presente seulement dans current_rows
        "removed": [...],    # cle presente seulement dans previous_rows
        "unchanged_ok": int, "unchanged_ko": int,
    }
    Chaque entree de regressed/fixed/changed/added/removed est
    {"previous": row|None, "current": row|None} (un seul cote est None pour
    added/removed).
    """
    previous_by_key = {_row_key(row, mode): row for row in previous_rows}
    current_by_key = {_row_key(row, mode): row for row in current_rows}

    regressed, fixed, changed, added = [], [], [], []
    unchanged_ok = unchanged_ko = 0

    for key, current_row in current_by_key.items():
        previous_row = previous_by_key.get(key)
        if previous_row is None:
            added.append({"previous": None, "current": current_row})
            continue

        prev_status, cur_status = previous_row.get("Status"), current_row.get("Status")
        entry = {"previous": previous_row, "current": current_row}

        if prev_status == "OK" and cur_status == "KO":
            regressed.append(entry)
        elif prev_status == "KO" and cur_status == "OK":
            fixed.append(entry)
        elif prev_status == "KO" and cur_status == "KO":
            if (previous_row.get("Error Type") != current_row.get("Error Type")
                    or previous_row.get("HTML Wording") != current_row.get("HTML Wording")):
                changed.append(entry)
            else:
                unchanged_ko += 1
        else:
            unchanged_ok += 1

    removed = [
        {"previous": previous_row, "current": None}
        for key, previous_row in previous_by_key.items()
        if key not in current_by_key
    ]

    return {
        "regressed": regressed, "fixed": fixed, "changed": changed,
        "added": added, "removed": removed,
        "unchanged_ok": unchanged_ok, "unchanged_ko": unchanged_ko,
    }


def load_last_two_reports_diff(country: str, trigger: str, sequence: str, mode: str) -> dict:
    """
    Charge les 2 rapports les plus recents archives pour (country, trigger,
    sequence, mode) - voir qa.archive.list_reports, deja trie du plus
    recent au plus ancien - et retourne leur diff (voir diff_reports).
    None si moins de 2 rapports de ce type sont disponibles (rien a
    comparer). mode="combined" n'est pas supporte : ce rapport fusionne
    2 onglets Wording/URL distincts plutot qu'un seul "Details" comparable.
    """
    reports = list_reports(country, trigger, sequence, mode=mode)
    if len(reports) < 2:
        return None

    current_df = pd.read_excel(reports[0], sheet_name="Details", engine="openpyxl").fillna("")
    previous_df = pd.read_excel(reports[1], sheet_name="Details", engine="openpyxl").fillna("")

    if mode == "url":
        current_df = _normalize_url_columns(current_df)
        previous_df = _normalize_url_columns(previous_df)

    return diff_reports(previous_df.to_dict("records"), current_df.to_dict("records"), mode)
