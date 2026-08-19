"""
Generateurs de classeurs Excel partages entre plusieurs fichiers de tests
(voir tests/test_excel_loader.py, tests/test_engine.py,
tests/test_classification.py) : avant leur introduction ici, chaque fichier
reimplementait sa propre version quasi identique de ces deux fonctions -
tout changement de structure attendue (ex: nom de colonne) devait alors etre
repercute a plusieurs endroits.

Fonctions ordinaires (pas des fixtures pytest) : conftest.py est charge
automatiquement par pytest, ce qui suffit a rendre `from conftest import ...`
resolvable depuis n'importe quel fichier de tests/ (pas de package
tests/__init__.py, voir pytest.ini).
"""
import pandas as pd


def make_wording_workbook(tmp_path, sheets: dict, filename="wording.xlsx", wording_column="TRANSLATION"):
    """
    sheets: {sheet_name: [[Component, Element, Wording, Technical], ...]} -
    ligne vide en tete + ligne d'en-tetes (header=1 cote lecture), comme un
    vrai onglet wording. wording_column parametrable (ex: "TRANSLATION
    GERMAN") pour les tests de detection de langue (voir
    tests/test_classification.py).
    """
    path = tmp_path / filename
    header_row = ["COMPONENT NAME", "ELEMENT", wording_column, "TECHNICAL NAME"]
    with pd.ExcelWriter(path) as writer:
        for sheet_name, data_rows in sheets.items():
            rows = [["", "", "", ""], header_row] + data_rows
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False, header=False)
    return str(path)


def make_utm_workbook(tmp_path, sections: dict, filename="utm.xlsx", sheet_name="UTM TRIGGER", label="HEADER"):
    """
    sections: {section_name: [url1, url2, ...]} - un onglet UTM avec
    plusieurs sequences empilees, chacune demarree par une ligne
    "separateur" (une seule cellule non vide, dans la colonne "URL" - voir
    qa.excel_loader._parse_url_sections). label : nom du bloc (colonne la
    plus a gauche, voir URL_LABEL_COL_IDX) pose sur chaque URL - parametrable
    car certains tests l'utilisent comme classe technique attendue (ex.
    "audit-component-cta") a faire correspondre a une classe HTML precise.
    """
    path = tmp_path / filename
    rows = [
        [None, "!! CHECK !!", None, None, None, None, None, None],
        [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
        [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
    ]
    for name, urls in sections.items():
        rows.append([None, name, None, None, None, None, None, None])
        for url in urls:
            rows.append([label, "https://example.com/", "trigger", "email", "revamp_inac", "seq1", "", url])

    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False, header=False)
    return str(path)
