# QA Email Checker

Outil interne de QA pour campagnes emailing : il compare le **wording réel d'un email HTML** (issu d'AEM / Adobe Campaign) avec le **référentiel Excel** validé par les équipes marketing, et remonte automatiquement les écarts (texte différent, faute, mauvais composant, wording manquant...).

Interface Streamlit + moteur de comparaison Python, avec export du résultat en rapport Excel formaté.

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Architecture du projet](#architecture-du-projet)
- [Installation](#installation)
- [Lancer l'application](#lancer-lapplication)
- [Utilisation](#utilisation)
- [Traiter plusieurs séquences en une fois](#traiter-plusieurs-séquences-en-une-fois)
- [Composants génériques](#composants-génériques)
- [Onglet frère LOYAL / NON LOYAL](#onglet-frère-loyal--non-loyal)
- [Format attendu du fichier Excel](#format-attendu-du-fichier-excel)
- [Comment fonctionne le matching](#comment-fonctionne-le-matching)
- [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url)
- [Rapport de sortie](#rapport-de-sortie)
- [Historique par pays / trigger / séquence](#historique-par-pays--trigger--séquence)
- [Nettoyage automatique des fichiers temporaires](#nettoyage-automatique-des-fichiers-temporaires)
- [Configuration](#configuration)
- [Limitations connues / roadmap](#limitations-connues--roadmap)

## Fonctionnalités

- **Upload du fichier HTML** (export "view-source" AEM ou HTML brut).
- **Extraction automatique du wording** depuis le HTML (parsée avec `lxml`, qui referme implicitement les tags mal formés comme le ferait un vrai navigateur — ex. un `<p>` imbriqué dans un `<p>`) :
  - lecture des blocs `class="text-contribution"` et `class="audit-generique-title"` (marqueurs AEM) — un titre générique sans classe `audit-component-*` directe est rattaché à son composant via sa position dans la séquence DOM (voir [Classe HTML introuvable](#classe-html-introuvable-component-class-not-found-in-html)),
  - reconstruction du HTML réel si le fichier fourni est une vue "view-source" AEM,
  - nettoyage des artefacts techniques pour l'affichage : variables `${...}`, placeholders `[xxx]`, caractères invisibles (LRM/RLM), entités HTML — les blocs AEM `<% %>` / `<%= %>` sont conservés jusqu'à la comparaison (voir "Blocs conditionnels AEM" plus bas) puis nettoyés uniquement à l'affichage,
  - les tags de pure emphase (`<b>`, `<strong>`, `<em>`, `<i>`, `<small>`) imbriqués dans un élément déjà extrait (ex. dans un `<p>`), et le texte brut d'un bloc quand il n'apporte rien de plus que ses enfants déjà extraits, ne sont pas dupliqués en entrées séparées,
  - les liens (`<a>`) individuels à l'intérieur d'un composant de mentions légales/footer (classe contenant `"legal"` ou `"footer"`, voir `LEGAL_MENTION_CLASS_KEYWORDS`) sont ignorés : leur texte est déjà couvert par le paragraphe complet qui les englobe.
- **Matching tolérant** entre HTML et Excel, avec plusieurs stratégies en cascade (direct, agrégation de blocs, sous-phrase, meilleure correspondance approximative), un traitement dédié aux devises (séparateurs `|`, `/`, `I`), et la résolution des blocs conditionnels AEM (`<% if %>...<% } else { %>...<% } %>`, voir "Blocs conditionnels AEM" plus bas).
- **Dashboard Streamlit** : taux de succès global, répartition des erreurs par type (camemberts Plotly), tableau interactif (AgGrid) avec diff mot-à-mot en couleur entre le wording attendu et le wording trouvé.
- **Rapport Excel exportable** (`Summary` / `Details` / `KO Only`) avec mise en forme conditionnelle (couleurs par statut/criticité) et diff en rich text.
- **Mode URL** : vérification des liens (`href`) du HTML par rapport à la liste d'URLs trackées du référentiel Excel (correspondance exacte ou fonctionnelle sur les paramètres UTM), avec un rapport dédié — colonnes `Section (FID/NON FID)`, `Classe attendue (Excel)`, `Classe trouvée (HTML)`, `URL attendue (Excel)`, `URL trouvée (HTML)`, `Status`, `Raison`, `Similarité (%)`, `Occurrences (HTML)` — voir [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url).
- **Mode "Wording + URL"** : lance les deux comparaisons en une fois sur le même HTML/Excel (un onglet Wording et une séquence UTM à choisir), avec un score combiné puis le détail des deux, et un rapport combiné archivé automatiquement. Compatible avec le mode multi-séquences.
- **Diagnostic à l'upload** : dès que l'Excel est chargé, l'app affiche les onglets détectés, si l'onglet/la colonne attendue est trouvée, et — en mode Wording — le taux de remplissage de `TECHNICAL NAME`, les composants dupliqués avec des classes techniques différentes (`qa/diagnostics.find_duplicate_components`), les wordings dont la langue détectée diffère de la langue dominante du référentiel (`qa/diagnostics.find_wrong_language_wordings`, ex. du texte resté dans une autre langue après un copier-coller), et (si le HTML est aussi uploadé) les classes déclarées dans l'Excel introuvables dans le HTML (`qa/diagnostics.find_unused_excel_classes`) — pour repérer un référentiel incomplet ou désynchronisé du vrai template avant même de lancer la comparaison.
- **Sélection guidée, sans présélection automatique** : les menus déroulants (onglet Excel / séquence) affichent un texte d'invite (`— Sélectionnez —`) plutôt que de présélectionner silencieusement le premier onglet du classeur — évite de lancer une comparaison sur la mauvaise campagne par inattention.
- **Mode multi-séquences** : traiter plusieurs séquences d'une même campagne (plusieurs fichiers HTML + un seul Excel) en une fois, avec un rapport Excel consolidé et un récapitulatif par séquence — voir [Traiter plusieurs séquences en une fois](#traiter-plusieurs-séquences-en-une-fois).
- **Composants génériques** (mode Wording) : si le référentiel Wording d'une campagne ne couvre pas les composants communs à toutes les séquences (footer, mentions légales, bloc fidélité...), une case à cocher permet de les fusionner automatiquement depuis un onglet séparé — voir [Composants génériques](#composants-génériques).
- **Onglet frère LOYAL / NON LOYAL** (mode Wording, toujours actif, sans case à cocher) : si l'onglet résolu a un frère pour la même séquence mais l'audience opposée (ex. `SEQ 1 NON LOYAL` / `SEQ 1 LOYAL`), ses wordings réellement différents sont automatiquement ajoutés avant le matching — les deux onglets représentent en général le même template HTML, qui bascule de l'un à l'autre via une condition AEM — voir [Onglet frère LOYAL / NON LOYAL](#onglet-frère-loyal--non-loyal).
- **Logs de debug dans l'UI** : le détail de l'extraction (auparavant visible seulement dans la console du serveur) est affiché dans un expander repliable après chaque comparaison, et a été réduit à l'essentiel (plus de trace ligne par ligne de chaque texte extrait).
- **Fichiers temporaires isolés par session** : chaque session de navigateur utilise ses propres fichiers dans `temp/`, deux analyses lancées en parallèle ne s'écrasent plus.
- **Historique par pays / trigger / séquence** : chaque comparaison est archivée automatiquement (pays et trigger détectés depuis le contenu du classeur) dans une arborescence de dossiers navigable depuis une page dédiée, avec rapport combiné Wording + URL et graphique d'évolution du score — voir [Historique par pays / trigger / séquence](#historique-par-pays--trigger--séquence).

## Architecture du projet

```
app.py                     Point d'entrée : navigation multipage (st.navigation) entre home.py et pages/1_Historique.py
home.py                     Interface Streamlit principale : upload, lancement de la QA, affichage, export, archivage automatique
pages/
└── 1_Historique.py          Page Historique : navigation Pays > Trigger > Séquence, rapports + graphique d'évolution
qa/
├── engine.py               Orchestrateur : run_qa_for_sequence() (1 sequence) et run_qa() (+ rapport)
├── config.py                Constantes de configuration (colonnes Excel, regex, seuils, tables pays)
├── text_utils.py             Nettoyage/normalisation de texte (clean_text, normalize, normalize_currency, expand_conditional_variants, clean_display_text)
├── html_extraction.py         Extraction du wording depuis le HTML (parser lxml)
├── excel_loader.py            Lecture de la feuille Excel référentielle (+ find_sibling_sequence_sheet/load_sibling_sequence_wordings et find_sibling_url_section/load_sibling_url_section pour l'onglet/la section frère LOYAL/NON LOYAL, list_wording_sheet_choices/list_url_sequence_choices pour les menus de sélection filtrés/regroupés) — classeur mis en cache (_open_excel_file) par chemin+date de modification+taille, pour ne pas le reparser à chaque interaction Streamlit
├── matching.py                 compare() + stratégies de matching HTML <-> Excel (mode Wording)
├── wording_qa.py                Pipeline de QA "wording" (run_wording_qa)
├── url_utils.py                  Extraction des href + normalisation fonctionnelle des URLs
├── url_qa.py                    Pipeline de QA "url" (run_url_qa)
├── diagnostics.py                 Verifications de coherence Excel/HTML pour le panneau de diagnostic
├── classification.py               Détection best-effort pays/trigger + normalisation des noms de séquence (FID/LOYAL)
├── archive.py                       Classement des rapports dans output/<Pays>/<Trigger>/<Séquence>/, rotation par type
├── combined_report.py                Fusion des derniers rapports Wording + URL d'une séquence en un rapport combiné
├── history.py                        Historique local des scores (record_run, load_history, build_history_tree, rewrite/delete_classification, prune_history...)
├── cleanup.py                         Suppression automatique des fichiers de temp/ plus vieux que 24h
└── report.py                        Génération des rapports Excel (save_report, save_consolidated_report, sort_results_by_html_order réutilisée par home.py pour l'aperçu)
temp/                       Fichiers uploadés temporairement, isolés par session (non versionné)
output/                     Rapports Excel générés (classés par Pays/Trigger/Séquence) + historique des scores (non versionné)
tests/                      Suite pytest (voir Tests)
```

### Flux d'exécution (`run_qa`)

1. `run_qa_for_sequence` exécute l'extraction + le matching pour une sequence, selon le `mode` :
   - **`wording`** : `html_extraction.load_and_extract_html_texts` parse le HTML et retourne la liste des textes trouvés (`{text, tag, type, component_class}`) ; `excel_loader.load_excel_wordings` sélectionne l'onglet Excel correspondant au `keyword` de campagne et retourne la liste des wordings attendus (`{Component, Element, Wording, Technical, norm, curr}`). Si un onglet frère LOYAL/NON LOYAL existe pour cette séquence, ses wordings réellement différents y sont ajoutés (toujours actif, voir [Onglet frère LOYAL / NON LOYAL](#onglet-frère-loyal--non-loyal)). Si `include_generic=True`, `excel_loader.load_generic_wordings` charge en plus l'onglet des composants génériques et ses wordings sont ajoutés à la liste avant le matching (voir [Composants génériques](#composants-génériques)). `wording_qa.run_wording_qa` compare le tout au HTML.
   - **`url`** : `html_extraction.load_raw_html` retourne le HTML brut (reconstruit si nécessaire) ; `excel_loader.load_excel_urls` sélectionne l'onglet dont le nom contient `"UTM"` et retourne la liste des URLs trackées attendues pour la section résolue. Si une section frère FID/NON FID existe pour cette séquence, ses URLs réellement différentes y sont ajoutées (toujours actif, voir [Onglet frère LOYAL / NON LOYAL](#onglet-frère-loyal--non-loyal)). `url_qa.run_url_qa` compare le tout au HTML.
2. `report.save_report` génère le rapport Excel dans `output/` (même format de sortie pour les deux modes).

En mode multi-séquences, l'app appelle directement `run_qa_for_sequence` une fois par (fichier HTML, séquence), sans passer par `run_qa`, puis génère un seul rapport consolidé via `report.save_consolidated_report` (voir [Traiter plusieurs séquences en une fois](#traiter-plusieurs-séquences-en-une-fois)).

## Installation

Prérequis : Python 3.10+.

```bash
git clone https://github.com/MailisBO/Automatisation-SEPH.git
cd Automatisation-SEPH
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

## Lancer l'application

```bash
streamlit run app.py
```

L'application s'ouvre dans le navigateur (par défaut sur `http://localhost:8501`).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

La suite couvre les fonctions de normalisation et le dépliage des blocs conditionnels AEM (`qa/text_utils.py`), l'extraction HTML — parsing `lxml`, dédoublonnage des tags d'emphase imbriqués et du fallback bloc, résolution d'un titre générique vers son composant y compris quand il est nested dans un bloc `text-contribution` ou porté directement par le tag de texte sans wrapper (`qa/html_extraction.py`) —, les stratégies de matching (`qa/matching.py`) et le pipeline complet (`qa/wording_qa.py`), y compris des tests de non-régression pour les bugs de cascade déjà corrigés (verrouillage prématuré sur un KO, starvation du Split par un faux KO de secours, branches if/else "sœurs" non consommées, promotion OK qui masquerait un vrai écart, classes techniques inconnues des deux côtés traitées à tort comme identiques, "meilleur effort" de mauvaise qualité affiché comme un rapprochement trompeur plutôt qu'un KO honnête, classe HTML introuvable traitée à tort comme un OK plutôt qu'un `Component class not found in HTML`). Elle couvre aussi la résolution d'onglet/colonne Excel, l'onglet/la section frère LOYAL/NON LOYAL (wording **et** URL) et le filtrage/regroupement des menus de sélection Wording/URL (`qa/excel_loader.py`), le câblage de ces deux fusions automatiques et le dédoublonnage par URL normalisée (peu importe le label) dans le pipeline (`qa/engine.py`, `tests/test_engine.py`), la QA URL — y compris la détection inverse des liens HTML absents de l'Excel, les blocs conditionnels AEM dans une URL, l'exclusion des liens `mailto:`, les domaines à query ignorée, la section source (FID/NON FID), le compte d'occurrences dans le HTML, le mismatch de classe technique (`URL in wrong component`), la classe HTML introuvable (`URL component class not found in HTML`) et la similarité caractère par caractère, ainsi que la normalisation fonctionnelle d'URL elle-même — réordonnancement des paramètres, décodage, tolérance aux URLs malformées (`qa/url_qa.py`, `qa/html_extraction.py`, `qa/url_utils.py`, `tests/test_url_utils.py`) —, la génération des rapports Excel : diff caractère par caractère, tri par ordre d'apparition et par criticité, calcul du score, structure du classeur (Summary/Details/KO Only) pour un rapport simple et un rapport consolidé multi-séquences (`qa/report.py`, `tests/test_report.py`), le cache du classeur Excel (invalidé si le fichier est réuploadé au même chemin, voir `tests/test_excel_loader.py`), la détection pays/trigger (domaine prioritaire sur la langue en cas de conflit) et la réconciliation (fusion, y compris la migration rétroactive) des noms de séquence (`qa/classification.py`), le classement et la rotation des rapports, y compris la purge des rapports non classés à la racine de `output/` (`qa/archive.py`), le rapport combiné (`qa/combined_report.py`), l'historique par pays/trigger/séquence — y compris le renommage/fusion et la suppression (`qa/history.py`) — et le nettoyage automatique de `temp/` (`qa/cleanup.py`).

L'interface Streamlit (`home.py`, `pages/1_Historique.py`) est couverte par `tests/test_home.py` et `tests/test_historique_page.py` : fonctions pures (diff mot-à-mot/caractère par caractère, préparation du tableau affiché, regroupement des éléments à archiver pour un run) testées directement, l'orchestration d'archivage (`_do_archive`, `ensure_archived` — archivage automatique dès que pays/trigger sont détectés, ouverture de la pop-up de complétion sinon, idempotence, déclenchement du rapport combiné), et tests de fumée via `streamlit.testing.v1.AppTest` pour le démarrage de l'app, la réaction des widgets au changement de mode, et la navigation complète (pays → trigger → séquence, y compris renommage/fusion) sur la page Historique.

Les générateurs de classeurs Excel de test réutilisés par plusieurs fichiers (`make_wording_workbook`, `make_utm_workbook`) vivent dans `tests/conftest.py`, pour éviter que chacun ne réimplémente sa propre version.

## Utilisation

1. **Type de test** : `Wording`, `URL`, ou `Wording + URL` pour lancer les deux à la fois sur le même HTML/Excel.
2. Uploader le **fichier HTML** (export "view-source" AEM ou HTML brut).
3. Uploader le **fichier Excel référentiel** (`.xlsx`) : dès qu'il est chargé, le champ suivant devient un **menu déroulant** proposant directement les onglets du classeur (mode Wording) ou les séquences détectées dans l'onglet UTM (mode URL) — plus besoin de taper un mot-clé exact. Tant qu'aucun Excel n'est uploadé, un champ texte fait office de repli (**"Onglet Excel (campagne)"** en mode Wording, **"Séquence (onglet UTM)"** en mode URL) avec un message rappelant qu'il faut d'abord ajouter l'Excel pour voir apparaître les suggestions. En mode "Wording + URL", les deux champs s'affichent l'un sous l'autre (les noms d'onglet/séquence ne sont pas forcément identiques des deux côtés, ex. `SEQ 1 NON LOYAL` vs `SEQ 1 NON FID`).

   Le menu déroulant **Wording** (`qa/excel_loader.list_wording_sheet_choices`) filtre les onglets qui ne doivent jamais être choisis directement soi-même : l'onglet UTM (dédié à la QA URL) et l'onglet `GENERIC` (toujours fusionné via la case dédiée, voir [Composants génériques](#composants-génériques)) n'apparaissent pas dans la liste. Le menu déroulant **URL** (`qa/excel_loader.list_url_sequence_choices`) applique le même filtrage aux sections de l'onglet UTM. Dans les deux menus, une paire de sœurs LOYAL/NON LOYAL (ou FID/NON FID) n'apparaît qu'**une seule fois**, avec un libellé explicite (ex. `SEQ 1 NON LOYAL (regroupé avec SEQ 1 LOYAL)`, `SEQ 1 NON FID (regroupé avec SEQ 1 FID)`) — puisque l'autre est de toute façon fusionné automatiquement dès qu'on teste le premier (voir [Onglet frère LOYAL / NON LOYAL](#onglet-frère-loyal--non-loyal)).
4. Choisir l'**onglet Excel** (mode Wording) ou la **séquence** (mode URL, voir [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url)) dans la liste — le menu affiche `— Sélectionnez —` par défaut (rien n'est présélectionné). En mode URL, si l'onglet UTM ne regroupe aucune séquence (onglet à plat, une seule séquence implicite), **aucun choix n'est nécessaire** : un message l'indique et le champ n'est pas requis.
5. Cliquer sur **🔍 Comparer**.
6. Consulter :
   - le taux de succès global et la répartition des types d'erreur,
   - le tableau détaillé (lignes vertes = OK, rouges = KO, diff mot-à-mot en gras rouge sur les KO),
   - télécharger le rapport Excel complet via le bouton **⬇️ Télécharger le rapport Excel**,
   - le détail de l'extraction dans l'expander **🐛 Logs de debug** (utile pour comprendre pourquoi un texte/une URL n'a pas été trouvé).
   - En mode "Wording + URL" : un **score combiné** s'affiche en tête, suivi du détail Wording puis du détail URL, chacun avec son propre tableau et bouton de téléchargement.
7. Le run est **archivé automatiquement** dans l'historique (pays/trigger détectés, une pop-up apparaît seulement si l'un des deux manque) — voir [Historique par pays / trigger / séquence](#historique-par-pays--trigger--séquence). En mode "Wording + URL", les deux tests sont archivés dans le même dossier de séquence et le rapport combiné est généré dans la foulée.

## Traiter plusieurs séquences en une fois

Une campagne a souvent plusieurs séquences (Seq1, Seq2, Seq3...), chacune avec son propre HTML mais partageant le même classeur Excel référentiel. Le mode multi-séquences évite de relancer l'app pour chacune, y compris en mode "Wording + URL" :

1. Cocher **📦 Traiter plusieurs séquences en une fois**.
2. Uploader le fichier Excel référentiel (unique) et **plusieurs fichiers HTML** (un par séquence).
3. Pour chaque fichier HTML, choisir dans la liste déroulante la séquence correspondante :
   - En mode Wording ou URL : un seul menu par fichier (l'**onglet** du classeur, ou la **séquence détectée dans l'onglet UTM**, voir [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url)).
   - En mode **Wording + URL** : deux menus par fichier (**"Onglet Wording"** et **"Séquence URL"**), car les deux noms ne sont pas forcément identiques (ex. `SEQ 1 NON LOYAL` vs `SEQ 1 NON FID`). Si l'onglet UTM n'a qu'une seule séquence, le second menu ne s'affiche pas et la séquence Wording est réutilisée automatiquement.

   Comme il s'agit d'un choix dans une liste (pas d'un mot-clé tapé), il n'y a pas de risque d'ambiguïté.
4. Cliquer sur **🔍 Comparer toutes les séquences**.
5. Consulter le récapitulatif par séquence (Total/OK/KO/Score, ou Score Wording + Score URL par fichier en mode combiné), puis le détail : un **expander par séquence** (`📁 <séquence> — X/Y OK (Z%)`), chacun avec son propre tableau — pas de mégatable mélangeant toutes les séquences, impossible de confondre un OK/KO d'une séquence avec celui d'une autre. Télécharger le(s) **rapport(s) Excel consolidé(s)** (`QA_MULTI_..._<horodatage>.xlsx`, voir [Rapport de sortie](#rapport-de-sortie)) — un par mode en "Wording + URL".

Si une séquence échoue (onglet/colonne introuvable), l'erreur est affichée pour ce fichier précis et les autres séquences continuent d'être traitées normalement. Chaque séquence est archivée dans l'historique sous son propre dossier (voir [Historique par pays / trigger / séquence](#historique-par-pays--trigger--séquence)) — pas de mélange entre séquences différentes d'un même run multi-séquences.

## Composants génériques

Certaines campagnes séparent dans l'Excel les wordings **spécifiques à la séquence** (ceux qui changent d'une séquence à l'autre) des composants **génériques**, communs à toutes les séquences d'une campagne (footer, mentions légales, bloc fidélité, bloc réassurance...), regroupés dans un onglet à part.

- Cocher **🧩 Les composants génériques sont dans un onglet séparé de l'Excel** (mode Wording uniquement, disponible en mode simple et en mode multi-séquences).
- L'onglet correspondant est retrouvé automatiquement (nom contenant `"GENERIC"`, configurable via `GENERIC_SHEET_KEYWORD`) et ses wordings sont **ajoutés** à ceux de la séquence testée avant le matching — aucune autre action requise.
- Structure attendue, identique à l'onglet de campagne à un détail près : la colonne wording de cet onglet est recherchée via `"TRANSLATION"` **ou** `"TEXT"` (le premier qui donne une correspondance unique), car un onglet générique peut porter un texte déjà localisé dans une colonne simplement nommée `"TEXT"` plutôt que `"TRANSLATION ..."`.
- Si l'onglet générique est introuvable ou que sa colonne wording est ambiguë, une erreur explicite est renvoyée (dans le diagnostic en mode simple, ou pour le fichier concerné en mode multi-séquences) plutôt que d'ignorer silencieusement les composants génériques.

## Onglet frère LOYAL / NON LOYAL

Certaines campagnes déclinent le même onglet/section de séquence en deux versions selon l'audience : `SEQ 1 NON LOYAL` / `SEQ 1 LOYAL` côté wording, `SEQ 1 FID` / `SEQ 1 NON FID` côté URL (sections empilées d'un même onglet UTM, voir [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url)). Dans la pratique, le HTML testé implémente souvent les **deux** wordings (et les liens qui vont avec) dans un seul et même template, via une condition AEM qui bascule de l'un à l'autre selon le statut du destinataire (voir "Blocs conditionnels AEM" plus bas) — tester contre un seul des deux ferait alors ressortir à tort le contenu de l'autre audience comme introuvable.

**Côté wording :**
- **Toujours actif, sans case à cocher** (contrairement aux composants génériques) : `qa/excel_loader.find_sibling_sequence_sheet` détecte automatiquement l'onglet "frère" à partir du nom de l'onglet résolu (même numéro de séquence, mot-clé fidélité opposé), et ses wordings sont chargés par `load_sibling_sequence_wordings`.
- Seuls les wordings **strictement différents** (texte normalisé non identique) de ceux déjà présents côté séquence sont fusionnés — la plupart des composants (header, carrousel produits...) ne changent pas selon l'audience. L'égalité doit être **exacte**, pas une similarité approchée : un pavé de mentions légales recopié avec une seule variation (ex. un nom de marque différent) reste un écart réel, signalé séparément pour chaque onglet plutôt que masqué comme un doublon.

**Côté URL, même principe (aussi toujours actif) :** `qa/excel_loader.find_sibling_url_section`/`load_sibling_url_section` détectent la section "sœur" au sein du **même** onglet UTM (même numéro de séquence, mot-clé fidélité opposé) et fusionnent ses URLs. Après fusion, `qa/engine.py` déduplique par **URL normalisée seule** (`qa/url_utils.normalize_url`, ordre/encodage des paramètres ignoré) — **peu importe le label** : une même destination listée à l'identique dans les deux sections (même sous des labels différents, ex. `HEADER` côté NON FID et `SOCIAL` côté FID) ne doit produire qu'**une seule** ligne de résultat, pas un OK/KO en double pour le même constat. Cette déduplication s'applique même sans onglet frère (une même section peut déjà lister deux fois la même URL sous deux labels) ; `run_url_qa` ne filtre jamais le matching par label (seulement l'affichage), donc dédoublonner par URL seule ne fait perdre aucune vérification réelle.

- Si le nom résolu (onglet ou section) n'a pas de mot-clé fidélité/numéro de séquence reconnaissable, si aucun autre onglet/section du classeur ne correspond, ou si **plusieurs** correspondent (ambigu), rien n'est fusionné — mieux vaut ne rien faire que fusionner à tort.
- Les logs de debug indiquent le nombre de wordings/URLs ignorés (déjà identiques côté séquence testée) et le nombre réellement fusionné, avec le nom de l'onglet/section frère détecté.
- Le menu déroulant de sélection (Wording **et** URL) ne propose qu'**un seul** des deux noms d'une paire frère, avec un libellé explicite (`qa/excel_loader.list_wording_sheet_choices` / `list_url_sequence_choices`, voir [Utilisation](#utilisation)) — l'autre étant fusionné automatiquement de toute façon.

## Format attendu du fichier Excel

- Une feuille par campagne, dont le **nom** contient le texte saisi dans le champ **"Onglet Excel (campagne)"** de l'app.
- **En-têtes sur la 2ᵉ ligne** de la feuille (la 1ʳᵉ ligne est ignorée).
- Colonnes attendues :

  | Colonne | Rôle |
  |---|---|
  | `COMPONENT NAME` | Nom du composant (reporté sur les lignes vides jusqu'au composant suivant) |
  | `ELEMENT` | Élément du composant (titre, CTA, paragraphe...) |
  | *(colonne contenant `TRANSLATION`)* | Wording de référence à comparer au HTML — recherché par mot-clé insensible à la casse (ex. `"TRANSLATION GERMAN + ..."`), pas par nom exact. Mot-clé configurable, voir [Configuration](#configuration). |
  | `TECHNICAL NAME` | Classe technique AEM (`audit-component-xxx`) utilisée pour matcher avec le HTML |

- Les placeholders entre crochets (ex. `[gift validity end date]`) sont automatiquement retirés avant comparaison.
- Certaines lignes sont ignorées systématiquement (`Subject` / `Header - Loyalty program name`, voir `EXCLUDED_QA_ROWS`), ainsi que tout élément nommé `LOGO` quel que soit son composant (voir `EXCLUDED_QA_ELEMENTS`) — son wording de référence est un texte alternatif d'image (`alt`), jamais extrait ni vérifié par l'outil.

En **mode URL**, l'onglet référentiel est différent :

- Son **nom** doit contenir `"UTM"` (insensible à la casse, peut contenir d'autres mots, ex. `"DS - Antichurn S2 UTM"`) — un seul onglet doit correspondre.
- Colonnes attendues (en-têtes sur la ligne où figure `<< TRACKED URL >>`, quelle que soit sa position) : une colonne exactement nommée `"URL"`, et la colonne `<< TRACKED URL >>` contenant les URLs trackées (avec paramètres UTM) à retrouver dans le HTML.
- Si l'onglet regroupe **plusieurs séquences** (ex. `SEQ 1 NON FID` et `SEQ 1 FID` empilées dans le même onglet), chaque séquence commence par une ligne "séparateur" ne contenant que son nom dans la colonne `"URL"` — c'est ce nom que le champ **"Séquence (onglet UTM)"** doit cibler. S'il n'y a pas de séparateur de ce type, tout l'onglet est utilisé sans filtrage.

## Comment fonctionne le matching

Pour chaque texte extrait du HTML, `qa/wording_qa.py` essaie, dans l'ordre, les stratégies définies dans `qa/matching.py` :

1. **Direct** (`find_best_match`) — comparaison 1 pour 1 avec les wordings Excel dont la classe technique est **connue et identique** à la `component_class` de l'élément HTML (`TECHNICAL NAME` ↔ classe CSS `audit-component-*` trouvée autour de l'élément HTML). Les deux classes doivent être connues : si l'une des deux (ou les deux) est inconnue, aucun candidat n'est retenu ici — `"None != None"` étant faux, sans cette vérification explicite deux classes inconnues des deux côtés seraient à tort traitées comme "la même classe".
2. **Agrégé** (`find_aggregated_match`) — regroupe 2 à 3 blocs HTML consécutifs (ex. titre + paragraphe) pour matcher une seule cellule Excel, utile quand le découpage HTML est plus fin que le découpage Excel.
3. **Split** (`find_split_match`) — accepte le cas où le texte HTML est une sous-phrase d'une cellule Excel (typiquement quand un placeholder Excel a été retiré côté HTML, ou qu'un fragment HTML couvre une bonne partie d'une cellule combinée). Deux garde-fous contre les faux positifs : le fragment doit couvrir au moins 40 % de la longueur de la cellule Excel (`min_ratio`), et si le HTML et la cellule ont chacun une classe technique connue, elles doivent correspondre (sinon un fragment court peut coïncider par hasard avec une cellule sans rapport, ex. un mot isolé qui apparaît dans un tout autre paragraphe). Si le HTML n'a **aucune** classe technique détectée, le match n'est plus accepté automatiquement en OK : voir "classe HTML introuvable" ci-dessous.
4. **Closest match** (`find_closest_match`) — en dernier recours, retient le wording Excel le plus proche par similarité (`difflib.SequenceMatcher`, seuil ≥ `SIM_CLOSEST_MATCH_MIN` = 55 %) :
   - classe technique **connue des deux côtés et différente** → KO `Wording in wrong component` (High),
   - classe HTML **introuvable** (`component_class` = `None`, voir ci-dessous) → KO `Component class not found in HTML` (Medium), même si le texte matche parfaitement,
   - sinon, similarité ≥ `SIM_CLOSEST_MATCH_ACCEPT` (85 %) → OK,
   - sinon → KO `Wording mismatch`.

### Classe HTML introuvable (`Component class not found in HTML`)

Un élément HTML n'a de classe technique connue que si `find_component_class` (dans `qa/html_extraction.py`) trouve un ancêtre `audit-component-*`, ou — pour un titre générique (`class="audit-generique-title"`) — si `find_generic_title_class` parvient à le résoudre via `title_to_component` (voir `build_title_to_component_map`, qui déduit le composant d'un titre générique de sa position dans la séquence DOM : un titre suivi d'un `audit-component-*` appartient à ce composant suivant, un titre suivi d'un autre titre appartient au composant précédent). Si ni l'un ni l'autre n'aboutit, `component_class` reste `None` : la colonne `HTML Class` du rapport affiche alors `none`.

Avant, un texte sans classe détectée pouvait quand même être validé OK par similarité de texte seule (Split, Closest match, branche conditionnelle partagée) — au risque de masquer un vrai défaut de balisage. Ces trois chemins renvoient désormais un KO dédié (`Component class not found in HTML`, criticité Medium) dès que la classe HTML est inconnue, plutôt qu'un OK reposant sur une hypothèse non vérifiable. Ce cas ne se produit **que** si le HTML n'a littéralement aucun marqueur exploitable à cet endroit (ni ancêtre `audit-component-*`, ni `audit-generique-title` résolvable) — un template qui ne balise jamais ses titres de section (juste des `<h1>`/`<h2>`/`<h3>` nus entre deux composants) déclenchera systématiquement ce KO pour ces titres, sans qu'il s'agisse d'un bug de l'outil.

Les étapes 1 et 2 ne verrouillent leur résultat que s'il est **OK** : un candidat de la bonne classe mais textuellement très différent (KO) n'empêche pas d'essayer l'étape suivante, qui peut trouver un meilleur match pour ce même fragment HTML. Le KO de l'étape 1/2 n'est utilisé que si aucune étape suivante ne trouve de OK — sauf si sa similarité est **inférieure à `SIM_CLOSEST_MATCH_MIN`** (55 %) : ce "meilleur effort" n'est alors que le candidat le moins mauvais parmi des non-matchs, pas un vrai wording légèrement incorrect. Plutôt que d'afficher un rapprochement arbitraire et trompeur (ex. un texte de section fidélité associé par erreur à un autre wording de la même classe, faute de mieux), la ligne est reportée honnêtement en KO `Unexpected wording (not in Excel)`, sans wording Excel associé.

La comparaison texte à texte elle-même (`compare()`) normalise les deux chaînes (`normalize()` dans `qa/text_utils.py` : retrait des tags AEM, placeholders, caractères invisibles) puis classe le résultat. Les noms d'`Error Type` et les seuils de similarité sont centralisés dans `qa/config.py` (constantes `KO_*` et `SIM_*`), pour qu'un même seuil ne soit jamais dupliqué à deux endroits :

| Cas | Statut | Error Type | Criticité |
|---|---|---|---|
| Chaînes vides des deux côtés | OK | — | — |
| Une seule chaîne vide | KO | `Text missing` | High |
| Égalité stricte après normalisation | OK | — | — |
| Égalité en ignorant la casse | KO | `Case mismatch` | Low |
| Similarité ≥ 95 % (`SIM_NEAR_EXACT`) | KO | `Minor typo` | Low |
| Similarité ≥ 80 % (`SIM_FUZZY`) | KO | `Fuzzy mismatch` | Medium |
| Égalité après normalisation des devises | OK | — | — |
| Même montant, présentation/devise différente | KO | `Currency format mismatch` | Medium |
| Sinon | KO | `Content mismatch` | High |

Chaque type de KO a un nom qui désigne un seul cas précis (avant, `Typo` recouvrait à la fois les quasi-exacts et les vagues ressemblances, et `Wording differs` recouvrait à la fois une vraie faute et un wording jamais retrouvé). La `Criticality` suit une échelle de gravité réelle (`CRITICALITY_BY_ERROR_TYPE` dans `qa/config.py`), pas le chemin de code qui a produit le KO.

### Blocs conditionnels AEM (`<% if %> ... <% } else { %> ... <% } %>`)

Certains wordings (typiquement la section loyalty) contiennent un bloc conditionnel qui affiche l'une ou l'autre de deux phrases selon une donnée destinataire (ex. singulier/pluriel sur un nombre de points). Le rendu final n'affiche jamais qu'**une seule branche à la fois**, mais le HTML source (et parfois la cellule Excel, si elle a été remplie en copiant le if/else brut du gabarit) contient les deux.

`qa.text_utils.expand_conditional_variants()` déplie chaque bloc conditionnel détecté en une variante de texte par branche (avec gestion des conditions successives/imbriquées dans un même fragment et des parenthèses imbriquées dans la condition, jusqu'à 8 combinaisons), au lieu de laisser `normalize()` concaténer les deux branches en une phrase incohérente. `compare()`, `find_split_match`, `find_closest_match` et `find_closest_html_match` essaient alors toutes les combinaisons de branches possibles côté HTML **et** côté Excel, et retiennent le meilleur résultat (un match sur n'importe laquelle des branches compte comme un match) — que l'Excel contienne la syntaxe conditionnelle brute ou une seule phrase représentative. Le texte affiché dans le rapport (`clean_display_text()`) ne montre jamais le code AEM brut : la branche la plus proche du wording comparé est choisie pour l'affichage, variables `<%= %>` retirées.

**Excel qui modélise les 2 branches en 2 lignes séparées** (courant pour faciliter la traduction, plutôt que de coller le if/else brut dans la cellule) : un seul fragment HTML "partagé" peut alors satisfaire les deux lignes Excel, mais un seul matching direct ne "consomme" que l'une d'elles. Pour la branche non consommée en premier, `run_wording_qa` cherche parmi les fragments HTML déjà exploités si l'un correspond, avec la même classe technique, quasi-parfaitement (`SIM_SHARED_BRANCH_EXACT`, volontairement beaucoup plus strict que le seuil de closest-match habituel) — si oui, la ligne est validée OK (`Match Type` = `Shared conditional branch`) plutôt que signalée à tort comme wording introuvable, **sauf si la classe HTML est introuvable** (voir [Classe HTML introuvable](#classe-html-introuvable-component-class-not-found-in-html)), auquel cas c'est un KO `Component class not found in HTML`. Le seuil est volontairement quasi-parfait : un match seulement "très bon" (proche de 95-98 %) reflète un vrai écart sur cette branche précise (ex. une ponctuation en trop), qui ne doit pas être masqué juste parce que la branche sœur a bien matché.

Chaque wording Excel non couvert par un match HTML (et non récupéré via le mécanisme ci-dessus) est reporté en fin de tableau : `Missing wording (not in HTML)` (Low, souvent un wording conditionnel/variant non attendu dans cette version) si rien de proche n'existe côté HTML, ou `Wording relocated (similar text found)` (Medium) si un texte HTML proche existe ailleurs sans que la classe technique corresponde ou sans atteindre le seuil quasi-parfait — ce cas mérite une vérification (doublon, texte mal placé) plutôt qu'une simple correction de faute.

Chaque ligne strictement identique (même statut, même paire HTML/Excel affichée) est dédupliquée en fin de pipeline — pas seulement les KO : une ligne déjà validée via Split ou Direct (qui ne "consomment" pas toujours la cellule Excel, voir `find_split_match`) peut aussi être retrouvée "non couverte" plus loin et repromue séparément, produisant deux fois la même ligne OK sans cette déduplication. Une promotion OK qui coïnciderait avec un KO déjà posé sur exactement le même texte n'a jamais lieu non plus (un vrai écart affectant les deux branches reste KO pour les deux, voir tests `TestConditionalBranchesDisplayAndMatching`).

Un dernier filtre (`_drop_ko_covered_by_ok`) supprime les KO dont le texte HTML est déjà entièrement contenu dans le texte HTML d'une autre ligne validée OK — cas fréquent quand un même contenu est extrait à la fois en un bloc complet (qui matche) et en fragments plus courts (qui, comparés seuls à leur propre wording Excel, ne matchent pas) : le fragment court est déjà couvert par le bloc validé, ce n'est pas un écart réel.

## Comment fonctionne la QA URL

L'onglet Excel de référence est celui dont le nom contient `"UTM"` (configurable via `URL_SHEET_KEYWORD`, voir [Configuration](#configuration)).

Cet onglet regroupe souvent plusieurs séquences empilées (une pour chaque variante de campagne : loyal/non-loyal, palier de fidélité...). `qa/excel_loader.load_excel_urls` repère les lignes "séparateur" (une seule cellule non vide, dans la colonne `"URL"`, contenant le nom de la séquence) pour découper l'onglet en sections, puis ne garde que les URLs de la section dont le nom correspond au texte saisi dans le champ **"Séquence (onglet UTM)"** (recherche insensible à la casse, comme pour la sélection d'onglet). Si aucun séparateur n'est trouvé, l'onglet est traité comme une liste plate et le mot-clé est ignoré.

Comme côté wording, la section "sœur" FID/NON FID (ou LOYAL/NON LOYAL) de la même séquence est fusionnée **automatiquement** (toujours actif, sans case à cocher) — voir [Onglet frère LOYAL / NON LOYAL](#onglet-frère-loyal--non-loyal).

Les URLs attendues de la séquence retenue sont lues dans la colonne `<< TRACKED URL >>` (`URL_COLUMN_NAME`). Tous les `href` du HTML sont extraits (`qa/html_extraction.extract_anchor_links`, qui ignore systématiquement les liens `mailto:` — une adresse de contact/support n'est jamais une URL marketing destinée à être trackée dans le référentiel), puis chaque URL Excel est recherchée parmi eux (`qa/url_qa.run_url_qa`) :

| Cas | Statut | Error Type | Match Type | Criticité |
|---|---|---|---|---|
| URL identique une fois les entités HTML décodées (`&amp;` → `&`) | OK | — | `Exact` | — |
| Même chemin, domaine listé dans `URL_IGNORE_QUERY_DOMAINS` (query ignorée) | OK | — | `Exact (paramètres ignorés)` | — |
| Même destination et mêmes paramètres UTM, mais ordre ou pourcent-encodage différent | KO | `URL parameter mismatch (order/encoding)` | `Functional match only` | Medium |
| Destination trouvée (peu importe le tier ci-dessus), mais classe technique **connue des deux côtés et différente** | KO | `URL in wrong component` | `Wrong component` | High |
| Aucune correspondance trouvée dans le HTML | KO | `URL not found` | `No match` | High |
| *(sens inverse)* Lien HTML présent mais ne correspondant (ni exactement ni fonctionnellement) à aucune URL Excel | KO | `Unexpected URL (not in Excel)` | `Unexpected` | Medium |

Le décodage des entités HTML est appliqué avant de juger un match "Exact" car AEM encode systématiquement les `href` ainsi (`&amp;` au lieu de `&`) — ce n'est pas une différence de contenu réelle. En revanche, un ordre de paramètres différent ou un encodage `%XX` différent reste considéré comme un écart (`Functional match only`), car l'URL n'est alors pas rendue à l'identique de ce qui a été validé.

**Classe technique** (`Expected Class` = le label de l'onglet UTM, `HTML Class` = la classe `audit-component-*` trouvée autour du lien) : comme le wording, deux vérifications s'appliquent dès qu'un href a été trouvé (peu importe le tier — `Exact`, `Exact (paramètres ignorés)` ou `Functional match only`) :
- **`KO_WRONG_COMPONENT`** (`URL in wrong component`, High) si les **deux** classes sont connues et **différentes** — l'URL pointe à la bonne destination mais vit dans le mauvais composant HTML ;
- **`KO_URL_CLASS_NOT_FOUND`** (`URL component class not found in HTML`, Medium) si **aucune classe `audit-component-*` n'a été détectée** autour du lien HTML — peu importe si `Expected Class` est lui-même connu ou non : on ne peut alors même pas vérifier si le composant est le bon, donc pas d'`OK` silencieux (même principe que `KO_COMPONENT_CLASS_NOT_FOUND` côté wording).

Ni l'une ni l'autre ne s'applique à un `No match` (aucun href trouvé du tout, rien à comparer) ni au sens inverse `Unexpected URL` (rien côté Excel à attribuer). `HTML Class` affiche `none` (jamais vide) quand aucune classe technique n'a pu être déterminée sur l'élément HTML — même convention que le wording. La raison exacte d'un `KO` (quel que soit le type ci-dessus, ou `URL not found`/`URL parameter mismatch`/`Unexpected URL`) est visible dans la colonne `Error Type` (`Raison` en français) du rapport — jusqu'ici calculée mais jamais affichée dans le rapport URL.

**Similarité caractère par caractère** (colonne `Similarity`, `qa.url_qa._char_similarity`) : un vrai pourcentage (`difflib.SequenceMatcher`, comme le wording) entre l'URL Excel attendue et l'URL HTML trouvée — 100 % pour un `Exact`, un pourcentage réel pour un `Functional match only` (reflète l'ampleur de l'écart, pas juste "trouvé/pas trouvé"), 0 % pour un `No match`/`Unexpected URL` (rien à comparer). Dans le rapport Excel, les différences entre `URL attendue (Excel)` et `URL trouvée (HTML)` d'une ligne `KO` sont surlignées en rouge gras **caractère par caractère** (`qa.report.build_rich_diff_char`), pas mot par mot comme le wording — une URL n'a pas d'espaces, un découpage par mots surlignerait toute l'URL au moindre écart. Même diff caractère par caractère dans l'aperçu Streamlit (`home.highlight_differences(..., char_level=True)`).

**Domaines à query ignorée** (`URL_IGNORE_QUERY_DOMAINS` dans `qa/config.py`, ex. `homeios.onelink.me` — lien court OneLink/deep-link vers l'app) : pour ces domaines précis, la query string (paramètres UTM/tracking) n'est **jamais** comparée, seule l'URL de base (domaine + chemin) compte — `qa.url_utils.is_query_ignored_domain`/`strip_query`. Le lien court **lui-même** contient déjà le tracking/attribution (encodé par le service dans le chemin, ex. le token après `/oUpm/...`) — une éventuelle query string ajoutée en plus est générée dynamiquement à l'envoi et jamais figée à l'identique dans le référentiel Excel ; la comparer produirait un KO "Functional match only" (voire "No match") systématique et sans valeur diagnostique. Un match sur le seul chemin compte comme un match plein (`OK`, `Exact (paramètres ignorés)`), pas comme un match dégradé — mais un `Exact` classique (chemin **et** query identiques) reste prioritaire s'il existe. Un chemin différent (au-delà de la query) reste normalement un `No match`.

Comme la query string peut différer intégralement entre les deux côtés dans ce cas, la colonne `Similarity` peut afficher un pourcentage bas malgré un `Status` `OK` — ce qui, sans explication, ressemble à tort à une anomalie. La colonne `Error Type`/`Raison` porte alors une note informative (`qa.config.INFO_URL_QUERY_IGNORED`) plutôt que de rester vide : ce n'est pas un vrai KO (la `Criticality` reste vide), juste une précision sur pourquoi ce pourcentage bas est normal ici. C'est la seule situation où `Error Type` est renseigné sur une ligne `OK` — partout ailleurs, il ne porte que la raison d'un `KO`.

**Blocs conditionnels AEM dans une URL** : comme le wording, une URL (côté Excel ou côté HTML) peut contenir un bloc `<% if %>...<% } else { %>...<% } %>` qui bascule d'une destination/d'un paramètre à l'autre selon l'audience — le rendu final n'affiche jamais qu'une seule branche à la fois. `run_url_qa` déplie chaque côté avec `qa.text_utils.expand_conditional_variants()` (même fonction que pour le wording, voir "Blocs conditionnels AEM" plus haut) avant de comparer : n'importe quelle combinaison de branches HTML/Excel qui matche (exactement ou fonctionnellement) compte comme un match, exactement comme `qa.matching.compare()`.

Pour l'affichage (colonnes `URL attendue (Excel)`/`URL trouvée (HTML)`) : si un href a été trouvé (`Exact` ou `Functional match only`), seule la branche qui a effectivement matché ce href est affichée (`clean_display_text()`, comme le wording) — pas d'ambiguïté à lever puisqu'on sait laquelle est en jeu. Dans les deux cas où rien n'a été rapproché, il n'y a au contraire aucun moyen de savoir laquelle des branches montrer : les **deux** sont alors affichées, séparées par ` ET ` (`qa.url_qa._display_all_variants`), plutôt que d'en montrer arbitrairement une seule et masquer l'autre. `ET`, pas `OU` : les deux destinations sont bien rendues alternativement selon l'audience, mais le référentiel Excel doit documenter les **deux** pour couvrir tous les cas — une seule ne suffit pas —
- côté Excel pour un `No match` (aucun href du HTML ne correspond à l'URL Excel attendue) ;
- côté HTML pour un `Unexpected URL (not in Excel)` (un href présent dans le HTML, jamais rapproché d'aucune URL Excel, porte lui-même un bloc conditionnel non résolu).

`run_url_qa` vérifie aussi le sens inverse : tout `href` du HTML qui n'a jamais servi de correspondance (exacte ou fonctionnelle) à une URL Excel ressort en KO `Unexpected URL (not in Excel)`, criticité Medium (délibérément plus basse que `URL not found` : un lien non tracké est souvent un lien légitime jamais destiné à être dans le référentiel — mentions légales, réseaux sociaux, désabonnement — pas nécessairement une erreur). Chaque valeur de `href` distincte n'est signalée **qu'une seule fois**, même répétée plusieurs fois dans le HTML (logo en header et en footer, par exemple).

La normalisation fonctionnelle (`qa/url_utils.normalize_url`) décode les entités HTML et le pourcent-encodage, puis retrie les paramètres de query pour comparer deux URLs indépendamment de l'ordre de leurs paramètres.

## Rapport de sortie

Le rapport Excel est généré dans `output/QA_<nom_feuille>_<horodatage>.xlsx` (dossier non versionné, créé automatiquement) et contient 3 onglets. Seuls les 2 rapports les plus récents sont conservés à cet emplacement (purge automatique, voir [Rotation des rapports](#rotation-des-rapports-qaarchivepy)) — le rapport archivé dans `output/<Pays>/<Trigger>/<Séquence>/` suit sa propre rotation, indépendante.

- **Summary** : nom de la campagne, date, total, nombre d'OK/KO, score global (coloré vert/orange/rouge selon le score).
- **Details** : toutes les lignes (KO et OK confondus), triées par ordre d'apparition dans le HTML (`sort_results_by_html_order` dans `qa/report.py`) — un composant qui a à la fois des OK et des KO reste groupé à sa vraie place dans la page, plutôt que d'éclater ses KO dans un bloc "tous les KO" séparé de ses OK. C'est le même tri qui alimente le tableau affiché dans l'app (`home.py`), pour que l'aperçu et le rapport téléchargé se correspondent toujours.
- **KO Only** : uniquement les écarts, triés par **criticité** (le plus grave en premier, pas l'ordre de la page — ce sheet sert au triage) puis par ordre d'apparition dans le HTML, avec le diff mot-à-mot en rouge gras entre les colonnes attendu/trouvé.

Les colonnes diffèrent selon le mode (`qa/report.py` : `BASE_COLUMNS` / `URL_COLUMNS`) :

| Mode | Colonnes |
|---|---|
| **Wording** | `Component Name`, `Element`, `Source Sheet`, `Expected Class`, `HTML Class`, `Excel Wording`, `HTML Wording`, `Status`, `Error Type`, `Similarity`, `Criticality`, `Match Type` |
| **URL** | `Section (FID/NON FID)`, `Classe attendue (Excel)`, `Classe trouvée (HTML)`, `URL attendue (Excel)`, `URL trouvée (HTML)`, `Status`, `Raison`, `Similarité (%)`, `Occurrences (HTML)` |

`Source Sheet` indique l'onglet Excel d'origine de la ligne (l'onglet de séquence testé, l'onglet frère LOYAL/NON LOYAL fusionné automatiquement, ou l'onglet `GENERIC`) — utile pour savoir si un écart vient du référentiel de la séquence elle-même ou d'un onglet fusionné. Visible dans l'Excel et dans le tableau Streamlit (mode Wording uniquement).

Côté URL, `Section (FID/NON FID)` joue le même rôle que `Source Sheet` : la section UTM d'origine de la ligne (la section testée, ou sa sœur FID/NON FID fusionnée automatiquement — voir [Onglet frère LOYAL / NON LOYAL](#onglet-frère-loyal--non-loyal)), vide si l'onglet UTM n'a pas de découpage en sections ou pour une ligne `Unexpected URL (not in Excel)` (rien côté Excel à attribuer).

`Occurrences (HTML)` compte le nombre de liens HTML dont la destination correspond à cette URL (selon les mêmes critères que le matching : exact, fonctionnel, ou domaine à query ignorée) — pas seulement le premier trouvé. Utile pour les liens censés se répéter plusieurs fois dans le HTML (ex. un même CTA affiché à plusieurs endroits) : il n'y a pas de nombre "attendu" côté référentiel Excel, mais le compte réel aide à vérifier qu'un lien censé se répéter l'est bien. Pour une ligne `Unexpected URL (not in Excel)`, compte les répétitions de ce lien précis lui-même.

En mode multi-séquences, `report.save_consolidated_report` génère un rapport unique `QA_MULTI_..._<horodatage>.xlsx` : les onglets `Details`/`KO Only` ajoutent une colonne `Sequence` (tri par séquence, puis même logique que ci-dessus au sein de chaque séquence), et le `Summary` ajoute un tableau récapitulatif Total/OK/KO/Score par séquence en plus du score global.

## Historique par pays / trigger / séquence

Chaque comparaison (mode simple ou multi-séquences) est **archivée automatiquement**, sans action de l'utilisateur, dans une arborescence `output/<Pays>/<Trigger>/<Séquence>/` navigable depuis une page dédiée.

### Détection automatique (`qa/classification.py`)

Le **pays** et le **trigger** sont déduits du **contenu** du classeur Excel — jamais du nom de fichier, trop peu fiable/cohérent d'une personne ou d'une campagne à l'autre :

- **Pays** : le **domaine** des URLs de l'onglet UTM a priorité (ex. `sephora.be` → `Belgium`, voir `DOMAIN_TO_COUNTRY`) ; le mot-clé de langue trouvé dans le nom de la colonne wording (ex. `"TRANSLATION FRENCH"` → `France`, voir `LANGUAGE_TO_COUNTRY`) ne sert qu'en repli, quand aucun domaine n'a pu être détecté (pas d'onglet UTM, ou domaine non reconnu). Cet ordre est volontaire : un mot-clé de langue comme `"FRENCH"` ou `"DUTCH"` désigne aussi bien la France que la Belgique (ou les Pays-Bas que la Belgique) — `LANGUAGE_TO_COUNTRY` retient la traduction littérale sans pouvoir trancher, alors que le domaine identifie le marché réel de la campagne.
- **Trigger** : valeur de la colonne `CAMPAIGN` de l'onglet UTM (ex. `revamp_inac` → `Revamp Inac`), recherché même en mode Wording (le classeur regroupe souvent les deux onglets).

Si l'un des deux n'a pas pu être détecté, une **pop-up** s'ouvre pour le compléter manuellement avant l'archivage (avec une option "Ne pas archiver"). La **séquence**, elle, n'est jamais redemandée : elle est toujours connue de manière fiable par ailleurs (onglet Excel résolu en mode Wording, section UTM résolue en mode URL).

### Wording et URL d'une même séquence, dans le même dossier

`qa/classification.normalize_sequence_name` réconcilie tous les vocabulaires possibles pour une même séquence numérotée en un seul nom de dossier canonique `"SEQ <n>"` :

- le vocabulaire diffère entre les deux onglets (l'onglet UTM parle de fidélité `FID` / `NON FID`, l'onglet wording de `LOYAL` / `NON LOYAL`) ;
- **LOYAL et NON LOYAL (ou FID et NON FID) sont eux-mêmes fusionnés entre eux** : ce sont les 2 wordings/audiences possibles d'un seul et même template HTML (voir [Onglet frère LOYAL / NON LOYAL](#onglet-frère-loyal--non-loyal), déjà fusionnés côté moteur wording avant le matching) — les archiver sous deux séquences distinctes dupliquerait à tort l'historique d'une seule et même séquence testée.

Ex. `"SEQ 1 NON FID"`, `"SEQ 1 NON LOYAL"`, `"SEQ 1 FID"`, `"SEQ 1 LOYAL"` → tous archivés sous `"SEQ 1"`. `resolve_sequence_alias` complète en recoupant avec les séquences déjà archivées pour ce couple pays/trigger quand l'onglet UTM ne précise pas la fidélité (ex. `"SEQ 2"` sans qualificatif) — utile surtout pour réconcilier une entrée déjà archivée sous un ancien nom qualifié (`"SEQ 2 LOYAL"`) avant cette fusion.

**Migration rétroactive automatique** (`qa/classification.migrate_legacy_loyalty_sequences`) : une séquence déjà archivée avant cette unification du nommage (donc encore sous un nom qualifié, ex. `"SEQ 2 LOYAL"`) est détectée et fusionnée vers son nom canonique **automatiquement**, sans action de l'utilisateur, à chaque ouverture de la page Historique (déplace les rapports Excel et réécrit l'historique, comme "Renommer / fusionner" — voir [Page Historique](#page-historique)). Sans effet et sans coût une fois toutes les séquences déjà sous forme canonique (vérifie juste qu'il n'y a rien à faire).

### Rotation des rapports (`qa/archive.py`)

Dans chaque dossier de séquence, les rapports sont préfixés par type (`QA_wording_...`, `QA_url_...`, `QA_combined_...`) et seuls les **2 plus récents de chaque type** (`KEEP_REPORTS`, voir [Configuration](#configuration)) sont conservés sur disque — les précédents sont supprimés automatiquement. L'historique des scores (`output/history.jsonl`) suit sa propre règle de rétention, voir [Rétention de l'historique](#rétention-de-lhistorique) ci-dessous.

**Rapports non classés, à la racine de `output/`** : chaque clic sur **Comparer** génère d'abord un rapport directement à la racine de `output/` (`qa/report.py::save_report`/`save_consolidated_report`), avant tout archivage éventuel dans un dossier Pays/Trigger/Séquence. La **même règle `KEEP_REPORTS`** s'applique à ces fichiers-là (`qa/archive.prune_root_reports`, appelée automatiquement juste après chaque génération) : seuls les 2 plus récents sont conservés à la racine, quel que soit leur statut d'archivage — sans cette purge, ils s'accumuleraient indéfiniment puisqu'un nouveau fichier est créé à chaque comparaison, archivée ou non. Cette purge ne touche jamais aux sous-dossiers Pays/Trigger/Séquence, déjà couverts par leur propre rotation ci-dessus.

### Rétention de l'historique (`qa/history.prune_history`)

À chaque run, l'historique classé (pays/trigger/séquence) est automatiquement purgé au-delà de `HISTORY_RETENTION_DAYS` (365 jours, voir `qa/config.py`), séquence par séquence, sans jamais perdre le point de comparaison le plus utile :

- **2 entrées ou plus dans les 365 derniers jours** : toutes les entrées récentes sont conservées, les plus anciennes sont supprimées.
- **exactement 1 entrée récente** : elle est conservée, ainsi que la dernière entrée juste avant elle (même si elle a plus d'un an) — pour garder un point de comparaison avant/après.
- **aucune entrée récente** (séquence pas testée depuis plus d'un an) : seules les **2 dernières entrées** sont conservées, quelle que soit leur date.

Les entrées non classées (runs sans archivage manuel, qui alimentent seulement le mini-graphique affiché juste après une comparaison) et les lignes corrompues ne sont jamais concernées par cette purge.

### Rapport combiné (`qa/combined_report.py`)

Dès que les rapports Wording **et** URL sont disponibles pour une séquence, un rapport combiné (`QA_combined_..._<horodatage>.xlsx`, onglets `Summary`/`Wording`/`URL`) est (re)généré automatiquement, avec son propre score suivi dans l'historique (`mode="combined"`).

### Page Historique

Accessible via le bouton **📈 Historique** en haut à droite de l'outil (page Streamlit séparée, `pages/1_Historique.py`) :

1. **Vue d'ensemble** en haut de page : un tableau récapitulatif de toutes les séquences archivées (Pays, Trigger, Séquence, date du dernier test, type, % OK, nombre de tests), avant même de naviguer par dossiers — utile pour repérer en un coup d'œil une séquence qui régresse sans avoir à la chercher dans l'arborescence.
2. Navigation par dossiers **Pays → Trigger → Séquence**.
3. Dans le dossier final : les rapports disponibles groupés par type (Wording / URL / Combiné), un graphique d'évolution du **% de OK** depuis le premier test (une courbe par type), et un panneau **"⚙️ Gérer cette séquence"** :
   - **Renommer / fusionner** : corrige une mauvaise détection automatique (pays/trigger) ou fusionne deux séquences classées séparément par erreur — déplace les rapports Excel et réécrit l'historique vers le nouveau (Pays, Trigger, Séquence) en un clic.
   - **Supprimer** : retire définitivement la séquence (rapports Excel **et** historique des scores) — protégé par une case à cocher de confirmation, le bouton restant désactivé tant qu'elle n'est pas cochée.

## Nettoyage automatique des fichiers temporaires

Les fichiers Excel/HTML uploadés (`temp/`) sont supprimés automatiquement dès qu'ils ont plus de 24h (`TEMP_MAX_AGE_HOURS`, voir `qa/cleanup.py` et [Configuration](#configuration)), sans action requise. Pour éviter de rescanner le dossier à chaque interaction (Streamlit réexécute tout le script à chaque clic), le nettoyage réel est limité à une fois par heure au maximum (`TEMP_CLEANUP_THROTTLE_SECONDS`) via un fichier marqueur (`temp/.last_cleanup`).

## Configuration

Les réglages globaux sont centralisés dans [`qa/config.py`](qa/config.py) :

| Constante | Rôle | Valeur par défaut |
|---|---|---|
| `WORDING_COLUMN_NAME` | Mot-clé (insensible à la casse) pour repérer la colonne wording de référence | `"TRANSLATION"` |
| `CAMPAIGN_KEYWORD` | Mot-clé par défaut pour l'exécution standalone (`python -m qa.engine`) | `"HugPlan S1 Bronze"` |
| `MIN_TEXT_LENGTH` | Longueur minimale d'un texte pour être pris en compte | `3` |
| `DEBUG_MODE` | Active les logs détaillés d'extraction dans la console — surchargeable sans toucher au code via la variable d'environnement `QA_DEBUG_MODE` (`0`/`false` pour désactiver) | `True` |
| `EXCLUDED_QA_ROWS` | Paires (Component, Element) Excel ignorées systématiquement | `subject/subject`, `header/loyalty program name` |
| `EXCLUDED_QA_ELEMENTS` | Éléments Excel ignorés quel que soit le composant | `logo` |
| `LEGAL_MENTION_CLASS_KEYWORDS` | Mots-clés identifiant une classe de mentions légales/footer, dont les liens individuels sont ignorés | `legal`, `footer` |
| `URL_SHEET_KEYWORD` | Mot-clé pour repérer l'onglet des URLs trackées (mode URL) | `"UTM"` |
| `URL_COLUMN_NAME` | Colonne des URLs de référence dans cet onglet | `"<< TRACKED URL >>"` |
| `URL_IGNORE_QUERY_DOMAINS` | Domaines pour lesquels la query string (UTM/tracking) n'est jamais comparée (mode URL) | `"homeios.onelink.me"` |
| `GENERIC_SHEET_KEYWORD` | Mot-clé pour repérer l'onglet des composants génériques | `"GENERIC"` |
| `OUTPUT_DIR` | Dossier de sortie des rapports | `"output"` |
| `TEMP_DIR` | Dossier des fichiers uploadés le temps d'une session | `"temp"` |
| `KEEP_REPORTS` | Nombre de rapports Excel les plus récents conservés par type dans un dossier de séquence | `2` |
| `TEMP_MAX_AGE_HOURS` | Âge au-delà duquel un fichier de `temp/` est supprimé automatiquement | `24` |
| `TEMP_CLEANUP_THROTTLE_SECONDS` | Fréquence maximale du scan réel de `temp/` (voir [Nettoyage automatique](#nettoyage-automatique-des-fichiers-temporaires)) | `3600` |
| `SCORE_COLOR_GOOD` / `SCORE_COLOR_WARN` | Seuils de couleur (vert/jaune/rouge) pour l'affichage d'un score dans les rapports Excel | `100` / `80` |
| `LANGUAGE_DETECTION_MIN_TEXT_LENGTH` | Longueur minimale d'un wording pour que la détection de langue (diagnostic Excel) soit fiable | `30` |
| `LANGUAGE_DETECTION_MIN_CLUSTER_SIZE` | Nombre minimal d'occurrences d'une langue minoritaire avant de la signaler | `2` |
| `LANGUAGE_TO_COUNTRY` | Mots-clés de langue (colonne wording) → pays, pour la détection automatique | `GERMAN`→Germany, `FRENCH`→France, `DUTCH`/`FLEMISH`→Belgium... |
| `DOMAIN_TO_COUNTRY` | Suffixes de domaine (URLs de l'onglet UTM) → pays, en repli si la langue n'a rien donné | `de`→Germany, `fr`→France, `be`→Belgium... |
| `UNCLASSIFIED_LABEL` | Nom de dossier utilisé si pays/trigger/séquence est vide | `"Non classé"` |

## Limitations connues / roadmap

- **Blocs conditionnels AEM multi-éléments** *(pas encore rencontré concrètement, laissé tel quel en attendant un cas réel)* : `expand_conditional_variants` résout un `<% if %>` contenu dans un seul fragment de texte (ex. une phrase dans un `<p>`). Un conditionnel qui englobe plusieurs balises entières (ex. `<% if(...) { %>` avant un titre, `<% } %>` après plusieurs paragraphes suivants) n'est pas interprété comme tel : chaque élément à l'intérieur reste extrait normalement, indépendamment de la condition qui les entoure.
- **Découpage en séquences de l'onglet UTM** *(pas encore rencontré concrètement, laissé tel quel en attendant un cas réel)* : ne gère qu'un seul niveau de séparateurs. Si un onglet imbrique des sous-catégories à l'intérieur d'une séquence (ex. des séparateurs répétés comme "FOOTER" dans chaque séquence), elles seront traitées comme des séquences distinctes plutôt que comme des sous-groupes.
- **Fusion de séquences (page Historique et migration automatique)** : déplace les rapports Excel et réécrit l'historique, mais ne fusionne pas le contenu des fichiers eux-mêmes — si les deux séquences dépassaient déjà `KEEP_REPORTS` fichiers après fusion, la rotation par type est réappliquée (les plus anciens sont supprimés, en gardant les plus récents toutes séquences d'origine confondues) sans tenir compte de leur pertinence relative.
- **Détection pays/trigger heuristique** : reste une proposition (toujours modifiable via la pop-up). Le domaine des URLs UTM a priorité sur la langue en cas de conflit (voir [Détection automatique](#détection-automatique-qaclassificationpy)), ce qui résout le cas le plus fréquent (FRANCE/Belgique, Pays-Bas/Belgique) — reste ambigu seulement si le domaine lui-même ne permet pas de trancher, ou si aucun onglet UTM n'est présent pour le déterminer.
