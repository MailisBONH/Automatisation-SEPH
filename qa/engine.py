from qa.config import CAMPAIGN_KEYWORD, DEBUG_MODE, EXCEL_FILE, HTML_FILE
from qa.excel_loader import (
    EXCEL_READ_ERRORS,
    load_excel_urls,
    load_excel_wordings,
    load_generic_wordings,
    load_sibling_sequence_wordings,
    load_sibling_url_section,
)
from qa.html_extraction import load_and_extract_html_texts, load_raw_html
from qa.report import save_report
from qa.url_qa import run_url_qa
from qa.url_utils import normalize_url
from qa.wording_qa import run_wording_qa

# Exceptions realistes qu'un appel a run_qa/run_qa_for_sequence peut lever en
# lisant ses fichiers d'entree : EXCEL_READ_ERRORS (classeur introuvable,
# onglet/colonne ambigu, fichier corrompu...) plus UnicodeDecodeError (le
# HTML est toujours lu en UTF-8, voir qa.html_extraction.load_raw_html) - a
# catcher ensemble par tout appelant (voir home.py) qui doit degrader
# proprement (message clair) plutot que planter sur un fichier illisible.
QA_RUN_READ_ERRORS = EXCEL_READ_ERRORS + (UnicodeDecodeError,)


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
            # Cle sur la totalite des variantes (pas seulement norm_variants[0],
            # voir "norm" dans qa.excel_loader) : un wording frere dont seule la
            # PREMIERE branche coincide avec un wording deja present (mais dont
            # une autre branche differe reellement) n'est pas un vrai doublon -
            # cette branche differente doit etre fusionnee, pas silencieusement
            # perdue.
            def _wording_key(w):
                return (tuple(w.get("norm_variants") or [w["norm"]]), w.get("Technical"))

            existing_keys = {_wording_key(w) for w in excel_wordings}
            new_sibling_wordings = [
                w for w in sibling_wordings
                if _wording_key(w) not in existing_keys
            ]
            skipped = len(sibling_wordings) - len(new_sibling_wordings)
            if skipped and DEBUG_MODE:
                print(
                    f"➖ {skipped} wording(s) de l'onglet frère '{sibling_sheet}' ignoré(s) "
                    "(identiques à l'onglet séquence, rien de spécifique à l'audience)"
                )
            if new_sibling_wordings and DEBUG_MODE:
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
                if DEBUG_MODE:
                    print(
                        f"➖ {len(overridden)} wording(s) de l'onglet séquence ignoré(s) "
                        "(composant couvert par l'onglet générique)"
                    )
                excel_wordings = [w for w in excel_wordings if w.get("Technical") not in generic_classes]

            if DEBUG_MODE:
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
            if sibling_section and sibling_urls:
                if DEBUG_MODE:
                    print(
                        f"🔀 Section frère '{sibling_section}' détectée (même séquence, audience "
                        "opposée) : URLs fusionnées avant dédoublonnage."
                    )
                excel_urls = excel_urls + sibling_urls

        # Deduplique par URL normalisee (ordre/encodage des parametres
        # ignore) : une meme destination ne doit produire qu'UNE ligne de
        # resultat, meme si elle apparait sous 2 labels ou 2 sections
        # differentes (typiquement un lien identique repris tel quel entre
        # FID et NON FID) - sinon elle produirait 2 lignes OK/KO redondantes
        # pour le meme constat. S'applique que la section ait un frere ou
        # non - une meme section peut deja lister la meme URL 2 fois sous
        # des labels differents.
        #
        # Si les doublons portent des labels DIFFERENTS, on les FUSIONNE
        # (join " / ") plutot que de garder arbitrairement le premier : le
        # lien peut legitimement vivre dans l'un OU l'autre composant selon
        # l'audience qui a rendu ce HTML (meme principe que la fusion des
        # wordings freres ci-dessus) - jeter le second label ferait
        # remonter un faux KO "mauvais composant" des que le HTML place le
        # lien dans le composant du label ecarte. qa.url_qa.run_url_qa
        # accepte un match sur N'IMPORTE LEQUEL des labels d'une entree
        # ainsi fusionnee.
        merged_by_key = {}
        order = []
        for u in excel_urls:
            is_dict = isinstance(u, dict)
            raw_url = u.get("url", "") if is_dict else str(u)
            label = (u.get("label", "") if is_dict else "") or ""
            section = u.get("Section", "") if is_dict else ""
            key = normalize_url(raw_url)
            if key not in merged_by_key:
                merged_by_key[key] = {"url": raw_url, "labels": [], "Section": section}
                order.append(key)
            if label and label not in merged_by_key[key]["labels"]:
                merged_by_key[key]["labels"].append(label)

        deduped_urls = [
            {
                "url": merged_by_key[key]["url"],
                "label": " / ".join(merged_by_key[key]["labels"]),
                "Section": merged_by_key[key]["Section"],
            }
            for key in order
        ]

        skipped = len(excel_urls) - len(deduped_urls)
        if skipped and DEBUG_MODE:
            print(f"➖ {skipped} URL(s) dupliquée(s) (même destination normalisée) fusionnée(s)")
        excel_urls = deduped_urls

        results = run_url_qa(raw_html, excel_urls)

    else:
        raise ValueError("Mode inconnu")

    return results, sheet


def run_qa(html_file=None, excel_file=None, keyword="", mode="wording", include_generic=False):

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
