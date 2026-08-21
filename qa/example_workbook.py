"""Genere un classeur Excel d'exemple illustrant le format attendu par l'outil
QA (voir README, section "Format attendu du fichier Excel") - telecharge
depuis l'aide de l'interface principale (home.py), pour qu'un nouvel
utilisateur voie a quoi doit ressembler son referentiel sans avoir a lire la
documentation complete."""
import io

import pandas as pd


def build_example_workbook_bytes() -> bytes:
    wording_rows = [
        ["", "", "", ""],
        ["COMPONENT NAME", "ELEMENT", "TRANSLATION FRENCH", "TECHNICAL NAME"],
        ["Header", "Title", "Bienvenue chez Sephora", "audit-component-header"],
        ["Header", "Subtitle", "Decouvrez nos nouveautes", "audit-component-header"],
        ["CTA", "Button label", "Decouvrir", "audit-component-cta"],
        ["Footer", "Legal mention", "Sephora SAS - Tous droits reserves", "audit-component-footer"],
    ]
    utm_rows = [
        [None, "!! CHECK !!", None, None, None, None, None, None],
        [None, "URL", "SOURCE", "MEDIUM", "CAMPAIGN", "SEQUENCE", "VERSION", "<< TRACKED URL >>"],
        [None, "Insert url here", "Mandatory", "Mandatory", "TRIGGER", "SEQ", "VERSION", None],
        [
            "CTA", "https://example.com/", "trigger", "email", "revamp_inac", "seq1", "",
            "https://www.sephora.fr/promo?utm_content=cta",
        ],
    ]

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(wording_rows).to_excel(writer, sheet_name="SEQ 1", index=False, header=False)
        pd.DataFrame(utm_rows).to_excel(writer, sheet_name="UTM TRIGGER", index=False, header=False)
    return buffer.getvalue()
