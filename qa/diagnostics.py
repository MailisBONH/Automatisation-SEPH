"""
Verifications de coherence entre le referentiel Excel et le HTML, destinees
a etre affichees dans le diagnostic de l'app avant de lancer la QA
complete (ou en complement du rapport).
"""


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
