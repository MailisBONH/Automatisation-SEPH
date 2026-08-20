"""
Verifications de coherence entre le referentiel Excel et le HTML, destinees
a etre affichees dans le diagnostic de l'app avant de lancer la QA
complete (ou en complement du rapport).
"""
from collections import Counter

from langdetect import DetectorFactory, LangDetectException, detect

from qa.config import LANGUAGE_DETECTION_MIN_CLUSTER_SIZE, LANGUAGE_DETECTION_MIN_TEXT_LENGTH

# langdetect utilise un tirage aleatoire interne ; on fixe la graine pour
# avoir un resultat reproductible (important pour les tests).
DetectorFactory.seed = 0


def find_unused_excel_classes(html_texts: list, excel_wordings: list) -> list:
    """
    Classes techniques (TECHNICAL NAME) presentes dans l'Excel mais absentes
    de tout element extrait du HTML - signe frequent que le nom de la classe
    a diverge entre l'Excel et le vrai template AEM (ex: l'Excel declare
    "audit-component-reassurance-bloc" alors que le HTML utilise
    "audit-component-advantage-card").
    """
    html_classes = {item.get("component_class") for item in html_texts if item.get("component_class")}
    excel_classes = {w.get("Technical") for w in excel_wordings if w.get("Technical")}
    return sorted(excel_classes - html_classes)


def find_duplicate_components(excel_wordings: list) -> list:
    """
    Composants (COMPONENT NAME) qui apparaissent avec plusieurs classes
    techniques (TECHNICAL NAME) differentes dans le meme referentiel -
    signe frequent d'un bloc duplique par erreur (copier-coller depuis une
    autre sequence/langue) plutot que d'une vraie variation legitime.
    Retourne une liste de (component_name, [classes_techniques...]).
    """
    classes_by_component = {}
    for w in excel_wordings:
        comp = w.get("Component")
        technical = w.get("Technical")
        if not comp or not technical:
            continue
        comp_key = str(comp).strip()
        classes_by_component.setdefault(comp_key, set()).add(technical)

    return sorted(
        (comp, sorted(classes)) for comp, classes in classes_by_component.items() if len(classes) > 1
    )


def find_empty_wordings(excel_wordings: list) -> list:
    """
    Wordings dont le texte de reference est vide ou ne contient que des
    espaces - une cellule laissee vide par erreur dans le referentiel
    (traduction jamais faite, ligne ajoutee sans contenu). Independant du
    HTML : detectable a la seule lecture de l'Excel.
    """
    return [
        {"Component": w.get("Component"), "Element": w.get("Element")}
        for w in excel_wordings
        if not str(w.get("Wording", "")).strip()
    ]


def find_whitespace_anomalies(excel_wordings: list) -> list:
    """
    Wordings dont le texte brut porte une anomalie d'espacement qui passe
    facilement inapercue a la relecture visuelle d'une cellule Excel :
    espace(s) en debut/fin, double espace interne, ou espace insecable
    (\\xa0, souvent colle involontairement lors d'un copier-coller depuis
    Word/un CMS). Retourne une liste de {Component, Element, Wording, issue}
    (une entree par anomalie, un wording peut donc apparaitre plusieurs fois
    s'il cumule plusieurs anomalies).
    """
    anomalies = []
    for w in excel_wordings:
        text = str(w.get("Wording", ""))
        if not text:
            continue
        issues = []
        if text != text.strip():
            issues.append("espace en début/fin")
        if "  " in text.strip():
            issues.append("double espace")
        if "\xa0" in text:
            issues.append("espace insécable")
        for issue in issues:
            anomalies.append({
                "Component": w.get("Component"), "Element": w.get("Element"),
                "Wording": text, "issue": issue,
            })
    return anomalies


def find_unbalanced_aem_blocks(excel_wordings: list) -> list:
    """
    Wordings dont le nombre de balises AEM ouvrantes ('<%') et fermantes
    ('%>') ne correspond pas - signe frequent d'un bloc conditionnel
    <% if %>...<% } %> mal recopie (balise oubliee/tronquee lors d'un
    copier-coller depuis le gabarit) qui produirait un texte incoherent une
    fois deplie par qa.text_utils.expand_conditional_variants().
    """
    anomalies = []
    for w in excel_wordings:
        text = str(w.get("Wording", ""))
        if text.count("<%") != text.count("%>"):
            anomalies.append({"Component": w.get("Component"), "Element": w.get("Element"), "Wording": text})
    return anomalies


def find_wrong_language_wordings(
    excel_wordings: list,
    min_text_length: int = LANGUAGE_DETECTION_MIN_TEXT_LENGTH,
    min_cluster_size: int = LANGUAGE_DETECTION_MIN_CLUSTER_SIZE,
) -> list:
    """
    Detecte les wordings dont la langue semble differente de la langue
    dominante du referentiel (ex: du texte italien reste dans un onglet
    allemand, copie-colle depuis une autre campagne/marche).

    - Les textes courts sont ignores (min_text_length) : la detection de
      langue n'est pas fiable sur quelques mots (ex: "Neu" ou noms de
      marque courts se font souvent detecter dans une mauvaise langue par
      coincidence).
    - Une langue minoritaire n'est signalee que si elle apparait au moins
      min_cluster_size fois : une detection isolee est le plus souvent un
      faux positif (mot emprunte, nom propre...), alors qu'un vrai bloc de
      contenu dans la mauvaise langue se retrouve sur plusieurs lignes.

    Retourne une liste de dicts {Component, Element, Wording, detected_lang,
    expected_lang}.
    """
    candidates = [w for w in excel_wordings if len(str(w.get("Wording", ""))) >= min_text_length]
    if len(candidates) < 3:
        return []

    detected = []
    for w in candidates:
        try:
            lang = detect(str(w["Wording"]))
        except LangDetectException:
            lang = None
        detected.append((w, lang))

    lang_counts = Counter(lang for _, lang in detected if lang)
    if not lang_counts:
        return []

    dominant_lang, _ = lang_counts.most_common(1)[0]

    return [
        {
            "Component": w.get("Component"),
            "Element": w.get("Element"),
            "Wording": w.get("Wording"),
            "detected_lang": lang,
            "expected_lang": dominant_lang,
        }
        for w, lang in detected
        if lang and lang != dominant_lang and lang_counts[lang] >= min_cluster_size
    ]
