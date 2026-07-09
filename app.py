import streamlit as st
import pandas as pd
import os
from qa.engine import run_qa

from datetime import datetime

import plotly.express as px
from collections import Counter


def color_rows(row):
    if row["Status"] == "OK":
        return ["background-color: #d4edda"] * len(row)  # vert clair
    else:
        return ["background-color: #f8d7da"] * len(row)  # rouge clair

st.set_page_config(page_title="QA Email Tool", layout="wide")

st.title("📧 Email QA Tool")

# =========================
# MODE (Wording / URL)
# =========================
mode = st.radio(
    "Type de test",
    ["Wording", "URL"],
    index=0,  # par défaut wording
    horizontal=True
)

# =========================
# UPLOAD FILES
# =========================
col1, col2, col3= st.columns(3)

with col1:

    aem_url = st.text_input("Lien AEM")

with col2:
    username = st.text_input("Identifiant AEM")

with col3:
    password = st.text_input("Mot de passe AEM", type="password")

col4, col5= st.columns(2)

with col4:
    excel_file = st.file_uploader(
        "📊 Drag & drop Excel",
        type=["xlsx"]
    )

with col5:
    html_file = st.file_uploader(
        "📄 Drag & drop HTML (optionnel)",
        type=["html"]
    )

keyword = st.text_input("Campaign keyword", "HugPlan S1 Bronze")

# =========================
# BOUTON COMPARER
# =========================
if st.button("🔍 Comparer"):

    if mode == "URL":
        st.warning("La partie URL n'est pas encore implémentée.")
    
    elif (html_file and excel_file) or (aem_url and excel_file):
        

        if aem_url and (not username or not password):
            st.error("Merci de renseigner les identifiants AEM")
            st.stop()

        os.makedirs("temp", exist_ok=True)

        if(html_file):
            html_path = "temp/input.html"
            with open(html_path, "wb") as f:
                f.write(html_file.read())
       
        excel_path = "temp/input.xlsx"

        # sauvegarde

        with open(excel_path, "wb") as f:
            f.write(excel_file.read())

        st.info("Analyse en cours... 🔄")

        # appel backend
        try:
            results, report_path, sheet_name = run_qa(
                html_file=html_path if not aem_url else None,
                excel_file=excel_path,
                keyword=keyword,
                aem_url=aem_url if aem_url else None,
                username=username if aem_url else None,
                password=password if aem_url else None
            )

            today = datetime.now().strftime("%Y-%m-%d")
            
            import re
            safe_sheet_name = re.sub(r"[^\w\-]", "_", sheet_name)


            st.success("✅ Comparaison terminée")
            

            df = pd.DataFrame(results)
                    
            ok_count = len(df[df["Status"] == "OK"])
            ko_count = len(df[df["Status"] == "KO"])

            total = ok_count + ko_count

            success_rate = round(
                ok_count / total * 100,
                1
            ) if total else 0

            
            st.metric(
                "QA Success Rate",
                f"{success_rate}%"
            )

            status_df = pd.DataFrame({
                "Status": ["OK", "KO"],
                "Count": [ok_count, ko_count]
            })

            fig_status = px.pie(
                status_df,
                values="Count",
                names="Status",
                hole=0.6,
                color="Status",
                title="Résultat global",
                color_discrete_map={
                    "OK": "#28a745",
                    "KO": "#dc3545"
                }
            )

            
            ko_df = df[df["Status"] == "KO"]

            if len(ko_df) > 0:

                error_counts = (
                    ko_df["Error Type"]
                    .fillna("Unknown")
                    .value_counts()
                    .reset_index()
                )

                error_counts.columns = [
                    "Error Type",
                    "Count"
                ]

                fig_errors = px.pie(
                    error_counts,
                    values="Count",
                    names="Error Type",
                    hole=0.6,
                    title="Répartition des erreurs"
                )

                col1, col2 = st.columns(2)

                with col1:
                    st.plotly_chart(
                        fig_status,
                        use_container_width=True
                    )

                with col2:
                    st.plotly_chart(
                        fig_errors,
                        use_container_width=True
                    )


            st.dataframe(df.style.apply(color_rows, axis=1))

            with open(report_path, "rb") as f:
                st.download_button(
                    "⬇️ Télécharger le rapport Excel",
                    data=f,
                    file_name = f"qa_report_{safe_sheet_name}_{today}.xlsx"
                )

        except ValueError as e:
            st.error(str(e))

        
    else:
        st.error("Merci d'uploader le HTML et l'Excel")