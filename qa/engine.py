import os

from qa.config import CAMPAIGN_KEYWORD, EXCEL_FILE, HTML_FILE
from qa.excel_loader import load_excel_urls, load_excel_wordings
from qa.html_extraction import (
    fetch_html_from_aem,
    load_and_extract_html_texts,
    load_raw_html,
    save_html_to_temp,
)
from qa.report import save_report
from qa.url_qa import run_url_qa
from qa.wording_qa import run_wording_qa


# =========================
# PIPELINE PRINCIPAL
# =========================
def run_qa(html_file=None, excel_file=None, keyword="", aem_url=None, username=None, password=None, mode="wording"):

    # ✅ Si URL AEM fournie → priorité
    if aem_url:

        html_content = fetch_html_from_aem(aem_url, username, password)

        os.makedirs("temp", exist_ok=True)
        html_file = "temp/aem_input.html"

        save_html_to_temp(html_content, html_file)


    print("\n" + "=" * 70)
    print("🔎 QA EMAIL - HTML → Excel (text-contribution)")
    print("=" * 70)

    if mode == "wording":
        html_texts = load_and_extract_html_texts(html_file)
        sheet, excel_wordings = load_excel_wordings(excel_file, keyword)
        results = run_wording_qa(html_texts, excel_wordings)

    elif mode == "url":
        raw_html = load_raw_html(html_file)
        sheet, excel_urls = load_excel_urls(excel_file)
        results = run_url_qa(raw_html, excel_urls)

    else:
        raise ValueError("Mode inconnu")

    # 4. Générer le rapport
    report_path = save_report(results, sheet)

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
