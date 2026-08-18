import os

from qa.config import CAMPAIGN_KEYWORD, EXCEL_FILE, HTML_FILE
from qa.excel_loader import (
    load_excel_urls,
    load_excel_wordings,
    load_generic_wordings,
    load_sibling_sequence_wordings,
    load_sibling_url_section,
)
from qa.html_extraction import (
    fetch_html_from_aem,
    load_and_extract_html_texts,
    load_raw_html,
    save_html_to_temp,
)
from qa.report import save_report
from qa.url_qa import run_url_qa
from qa.url_utils import normalize_url
from qa.wording_qa import run_wording_qa


# =========================
# PIPELINE PRINCIPAL
# =========================
def run_qa_for_sequence(html_file, excel_file, keyword, mode="wording", include_generic=False):
    """
    Execute l'extraction + le matching pour une sequence (un HTML + un
    onglet/sequence Excel), sans generer de rapport. Utilise par run_qa()
    (sequence unique, rapport genere ensuite) et par les traitements
    multi-sequences (un rapport consolide est genere a part sur l'ensemble
    des sequences, voir qa.report.save_consolidated_report).

    include_generic (mode "wording" uniquement) : si True, les wordings de
    l'onglet "composants génériques" (voir load_generic_wordings) sont
    ajoutés à ceux de la séquence avant le matching. Les wordings de
    l'onglet séquence qui portent la même classe technique qu'un composant
    générique sont retirés au préalable : c'est l'onglet générique qui fait
    foi pour ces composants (évite qu'un wording obsolète/incorrect resté
    dans l'onglet séquence ne pollue le matching).

    Onglet "frère" LOYAL/NON LOYAL (mode "wording" uniquement, toujours
    actif, sans option) : si l'onglet résolu a un frère pour la même
    séquence mais l'audience opposée (ex. "SEQ 1 NON LOYAL" / "SEQ 1
    LOYAL"), ses wordings sont automatiquement ajoutés à ceux de la
    séquence avant le matching — les deux onglets représentent en réalité
    le même template HTML, qui bascule de l'un à l'autre via une condition
    AEM selon l'audience du destinataire (voir "Blocs conditionnels AEM"
    dans le README). Contrairement à l'onglet générique, rien n'est retiré
    de l'onglet séquence : les deux jeux de wordings sont simplement réunis
    (voir qa.excel_loader.find_sibling_sequence_sheet) - SAUF les wordings
    de l'onglet frère STRICTEMENT identiques (même texte normalisé, même
    classe technique) à un wording déjà présent côté séquence : la plupart
    des composants ne changent pas selon l'audience (header, carrousel
    produits...), seuls quelques-uns diffèrent réellement. L'égalité doit
    être exacte (pas une similarité approchée) : un pavé de mentions
    légales recopié avec une variation mineure (ex. un nom de marque
    différent) reste un écart réel qu'il faut signaler séparément, pas un
    doublon à masquer silencieusement.
    """
    if mode == "wording":
        html_texts = load_and_extract_html_texts(html_file)
        sheet, excel_wordings = load_excel_wordings(excel_file, keyword)

        sibling_sheet, sibling_wordings = load_sibling_sequence_wordings(excel_file, sheet)
        if sibling_sheet:
            existing_keys = {(w["norm"], w.get("Technical")) for w in excel_wordings}
            new_sibling_wordings = [
                w for w in sibling_wordings
                if (w["norm"], w.get("Technical")) not in existing_keys
            ]
            skipped = len(sibling_wordings) - len(new_sibling_wordings)
            if skipped:
                print(
                    f"➖ {skipped} wording(s) de l'onglet frère '{sibling_sheet}' ignoré(s) "
                    "(identiques à l'onglet séquence, rien de spécifique à l'audience)"
                )
            if new_sibling_wordings:
                print(
                    f"🔀 Onglet frère '{sibling_sheet}' détecté (même séquence, audience "
                    f"opposée) : {len(new_sibling_wordings)} wording(s) réellement "
                    "différent(s) fusionné(s) — le HTML gère probablement les deux via "
                    "une condition AEM."
                )
            excel_wordings = excel_wordings + new_sibling_wordings

        if include_generic:
            generic_sheet, generic_wordings = load_generic_wordings(excel_file)
            generic_classes = {w["Technical"] for w in generic_wordings if w.get("Technical")}

            overridden = [w for w in excel_wordings if w.get("Technical") in generic_classes]
            if overridden:
                print(
                    f"➖ {len(overridden)} wording(s) de l'onglet séquence ignoré(s) "
                    "(composant couvert par l'onglet générique)"
                )
                excel_wordings = [w for w in excel_wordings if w.get("Technical") not in generic_classes]

            print(f"➕ {len(generic_wordings)} wording(s) générique(s) ajoutés depuis l'onglet '{generic_sheet}'")
            excel_wordings = excel_wordings + generic_wordings

        results = run_wording_qa(html_texts, excel_wordings)

    elif mode == "url":
        raw_html = load_raw_html(html_file)
        sheet, section_name, excel_urls = load_excel_urls(excel_file, keyword)

        # Onglet UTM "frere" FID/NON FID (toujours actif, comme pour le
        # wording) : la meme raison s'applique - le HTML teste peut rendre
        # l'un ou l'autre lien/destination selon l'audience du destinataire
        # via la meme condition AEM que le wording, donc tester contre une
        # seule des deux sections ferait ressortir a tort l'URL de l'autre
        # audience comme introuvable.
        if section_name:
            sibling_section, sibling_urls = load_sibling_url_section(excel_file, section_name)
            if sibling_section:
                existing_keys = {(normalize_url(u["url"]), u.get("label")) for u in excel_urls}
                new_sibling_urls = [
                    u for u in sibling_urls
                    if (normalize_url(u["url"]), u.get("label")) not in existing_keys
                ]
                skipped = len(sibling_urls) - len(new_sibling_urls)
                if skipped:
                    print(
                        f"➖ {skipped} URL(s) de la section frère '{sibling_section}' ignorée(s) "
                        "(identiques à la section testée, rien de spécifique à l'audience)"
                    )
                if new_sibling_urls:
                    print(
                        f"🔀 Section frère '{sibling_section}' détectée (même séquence, audience "
                        f"opposée) : {len(new_sibling_urls)} URL(s) réellement différente(s) fusionnée(s)."
                    )
                excel_urls = excel_urls + new_sibling_urls

        results = run_url_qa(raw_html, excel_urls)

    else:
        raise ValueError("Mode inconnu")

    return results, sheet


def run_qa(html_file=None, excel_file=None, keyword="", aem_url=None, username=None, password=None,
           mode="wording", aem_temp_path="temp/aem_input.html", include_generic=False):

    # ✅ Si URL AEM fournie → priorité
    if aem_url:

        html_content = fetch_html_from_aem(aem_url, username, password)

        os.makedirs(os.path.dirname(aem_temp_path) or ".", exist_ok=True)
        html_file = aem_temp_path

        save_html_to_temp(html_content, html_file)


    print("\n" + "=" * 70)
    print("🔎 QA EMAIL - HTML → Excel (text-contribution)")
    print("=" * 70)

    results, sheet = run_qa_for_sequence(html_file, excel_file, keyword, mode, include_generic)

    # 4. Générer le rapport
    report_path = save_report(results, sheet, mode)

    # ✅ IMPORTANT : retourner quelque chose
    return results, report_path, sheet


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    try:
        run_qa(HTML_FILE, EXCEL_FILE, CAMPAIGN_KEYWORD)
    except Exception as e:
        print(f"\n❌ Erreur inattendue : {e}")
        import traceback
        traceback.print_exc()
