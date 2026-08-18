"""
Detection best-effort du (pays, trigger) d'une campagne, a partir du CONTENU
du classeur Excel (jamais du nom de fichier - trop peu fiable/coherent d'une
personne/campagne a l'autre). Utilise pour pre-remplir le formulaire de
confirmation avant archivage dans l'historique (voir app.py) ; la sequence,
elle, est deja connue de maniere fiable par ailleurs (onglet Excel resolu en
mode wording, section UTM resolue en mode url) et n'a pas besoin d'etre
devinee ici.

Ne leve jamais : une detection ratee retourne simplement None, l'utilisateur
complete alors manuellement dans le formulaire.
"""
import urllib.parse

import pandas as pd

from qa.config import (
    DOMAIN_TO_COUNTRY,
    LANGUAGE_TO_COUNTRY,
    SEQ_NUMBER_PATTERN as _SEQ_NUMBER_RE,
    WORDING_COLUMN_NAME,
)
from qa.excel_loader import _locate_url_sheet_layout, find_column, find_sheet
from qa.history import build_history_tree


def normalize_sequence_name(name: str) -> str:
    """
    Canonicalise un nom de sequence pour que la MEME sequence soit toujours
    rangee dans le meme dossier d'historique, quel que soit l'onglet/vocabulaire
    d'origine :
    - wording (onglet Excel, vocabulaire "LOYAL"/"NON LOYAL") et url (section
      de l'onglet UTM, vocabulaire "FID"/"NON FID" - fidelite) pour la MEME
      sequence numerotee ;
    - LOYAL et NON LOYAL (ou FID et NON FID) entre eux : ce sont les 2
      wordings/audiences possibles d'un seul et meme template HTML, qui
      bascule de l'un a l'autre via une condition AEM selon le destinataire
      (voir qa.excel_loader.find_sibling_sequence_sheet - le moteur QA les
      fusionne deja systematiquement avant le matching, cote wording). Les
      archiver separement dupliquerait a tort l'historique d'une seule et
      meme sequence testee.

    Ex: "SEQ 1 NON FID", "SEQ 1 NON LOYAL", "SEQ 1 LOYAL", "SEQ 1 FID" ->
    tous "SEQ 1". Le texte additionnel autour (ex: "SEQ 4 WELCOME BACK NON
    LOYAL") n'affecte pas le resultat, seul le numero de sequence compte.

    Si aucun numero de sequence n'est trouve, retourne le nom d'origine
    (nettoye des espaces) sans le modifier davantage : mieux vaut ne pas
    fusionner que fusionner a tort.
    """
    if not name:
        return name

    upper = str(name).strip().upper()

    match = _SEQ_NUMBER_RE.search(upper)
    if not match:
        return str(name).strip()

    return f"SEQ {int(match.group(1))}"


def resolve_sequence_alias(country: str, trigger: str, sequence: str) -> str:
    """
    Complement de normalize_sequence_name pour le cas ambigu ou l'onglet UTM
    ne precise pas la fidelite (ex: "SEQ 2", sans "FID"/"LOYAL") alors que la
    sequence correspondante existe deja cote wording avec un qualificatif
    (ex: "SEQ 2 LOYAL" deja archivee pour ce meme pays/trigger) : dans ce cas
    reutilise le nom deja archive plutot que de creer un second dossier pour
    ce qui est tres probablement la meme sequence.

    Ne s'applique que si `sequence` (deja normalise) n'a pas lui-meme de
    qualificatif, et si un seul candidat correspond (evite de fusionner a
    tort si plusieurs variantes existent deja, ex: SEQ 2 LOYAL et SEQ 2 NON
    LOYAL toutes les deux presentes).
    """
    if sequence.endswith("LOYAL"):
        return sequence

    match = _SEQ_NUMBER_RE.search(sequence)
    if not match:
        return sequence

    prefix = f"SEQ {int(match.group(1))}"

    try:
        existing = list(build_history_tree().get(country, {}).get(trigger, {}).keys())
    except Exception:
        return sequence

    candidates = [s for s in existing if s == prefix or s.startswith(prefix + " ")]

    if len(candidates) == 1:
        return candidates[0]
    return sequence


def _country_from_language(column_name: str) -> str:
    if not column_name:
        return None
    upper = str(column_name).upper()
    for keyword, country in LANGUAGE_TO_COUNTRY.items():
        if keyword in upper:
            return country
    return None


def _country_from_url(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(str(url)).netloc.lower()
    except ValueError:
        return None
    if not netloc:
        return None
    # Suffixes les plus longs d'abord (ex: "co.uk" avant "uk").
    for suffix in sorted(DOMAIN_TO_COUNTRY, key=len, reverse=True):
        if netloc == suffix or netloc.endswith("." + suffix):
            return DOMAIN_TO_COUNTRY[suffix]
    return None


def _format_trigger(raw: str) -> str:
    text = str(raw).strip().replace("_", " ").replace("-", " ")
    return " ".join(w.capitalize() for w in text.split()) or None


def _detect_from_utm_tab(excel_file: str) -> dict:
    """Meilleur effort via l'onglet UTM (souvent present meme pour une
    campagne testee en mode wording, le classeur regroupant les deux) :
    colonne CAMPAIGN -> trigger, domaine de la 1ere URL -> pays (fallback)."""
    result = {"country": None, "trigger": None}

    try:
        _, raw, header_row_idx, tracked_col_idx, _ = _locate_url_sheet_layout(excel_file)
    except (ValueError, FileNotFoundError, OSError):
        return result

    if header_row_idx is None:
        return result

    header_row = raw.iloc[header_row_idx]
    campaign_col_idx = next(
        (i for i, v in enumerate(header_row) if isinstance(v, str) and v.strip().upper() == "CAMPAIGN"),
        None
    )

    if campaign_col_idx is not None:
        campaign_values = raw.iloc[header_row_idx + 1:, campaign_col_idx]
        if tracked_col_idx is not None:
            # Ne garde que les lignes qui ont aussi une URL trackee renseignee :
            # exclut la ligne modele "Mandatory" du gabarit (qui n'en a pas),
            # qui sinon peut fausser le vote majoritaire sur un onglet a peu
            # de lignes reelles.
            has_tracked_url = raw.iloc[header_row_idx + 1:, tracked_col_idx].notna()
            campaign_values = campaign_values[has_tracked_url]
        values = campaign_values.dropna()
        if len(values):
            try:
                most_common = values.astype(str).mode().iloc[0]
                result["trigger"] = _format_trigger(most_common)
            except IndexError:
                pass

    if tracked_col_idx is not None:
        urls = raw.iloc[header_row_idx + 1:, tracked_col_idx].dropna()
        if len(urls):
            result["country"] = _country_from_url(str(urls.iloc[0]))

    return result


def detect_classification(excel_file: str, sheet_or_keyword: str, mode: str) -> dict:
    """
    Retourne {"country": str|None, "trigger": str|None}, au mieux de ce qui
    est detectable dans le classeur. Toujours a confirmer/corriger par
    l'utilisateur avant archivage (voir render_archive_section dans app.py).
    """
    country = None

    if mode == "wording" and sheet_or_keyword:
        try:
            sheet = find_sheet(excel_file, sheet_or_keyword)
            df = pd.read_excel(excel_file, sheet_name=sheet, header=1, engine="openpyxl")
            df.columns = df.columns.str.strip()
            wording_column = find_column(df, WORDING_COLUMN_NAME)
            country = _country_from_language(wording_column)
        except (ValueError, FileNotFoundError, OSError):
            pass

    utm_guess = _detect_from_utm_tab(excel_file)

    return {
        "country": country or utm_guess["country"],
        "trigger": utm_guess["trigger"],
    }
