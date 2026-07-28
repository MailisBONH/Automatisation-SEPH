# QA Email Checker

Outil interne de QA pour campagnes emailing : il compare le **wording réel d'un email HTML** (issu d'AEM / Adobe Campaign) avec le **référentiel Excel** validé par les équipes marketing, et remonte automatiquement les écarts (texte différent, faute, mauvais composant, wording manquant...).

Interface Streamlit + moteur de comparaison Python, avec export du résultat en rapport Excel formaté.

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Architecture du projet](#architecture-du-projet)
- [Installation](#installation)
- [Lancer l'application](#lancer-lapplication)
- [Utilisation](#utilisation)
- [Format attendu du fichier Excel](#format-attendu-du-fichier-excel)
- [Comment fonctionne le matching](#comment-fonctionne-le-matching)
- [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url)
- [Rapport de sortie](#rapport-de-sortie)
- [Configuration](#configuration)
- [Limitations connues / roadmap](#limitations-connues--roadmap)

## Fonctionnalités

- **Deux sources pour le HTML** : upload d'un fichier `.html`, ou récupération directe depuis une URL AEM (authentification Basic Auth).
- **Extraction automatique du wording** depuis le HTML :
  - lecture des blocs `class="text-contribution"` et `class="audit-generique-title"` (marqueurs AEM),
  - reconstruction du HTML réel si le fichier fourni est une vue "view-source" AEM,
  - nettoyage des artefacts techniques : directives `<% %>` / `<%= %>`, variables `${...}`, placeholders `[xxx]`, caractères invisibles (LRM/RLM), entités HTML.
- **Matching tolérant** entre HTML et Excel, avec plusieurs stratégies en cascade (direct, agrégation de blocs, sous-phrase, meilleure correspondance approximative) et un traitement dédié aux devises (séparateurs `|`, `/`, `I`).
- **Dashboard Streamlit** : taux de succès global, répartition des erreurs par type (camemberts Plotly), tableau interactif (AgGrid) avec diff mot-à-mot en couleur entre le wording attendu et le wording trouvé.
- **Rapport Excel exportable** (`Summary` / `Details` / `KO Only`) avec mise en forme conditionnelle (couleurs par statut/criticité) et diff en rich text.
- **Mode URL** : vérification des liens (`href`) du HTML par rapport à la liste d'URLs trackées du référentiel Excel (correspondance exacte ou fonctionnelle sur les paramètres UTM) — voir [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url).

## Architecture du projet

```
app.py                     Interface Streamlit : upload, lancement de la QA, affichage, export
qa/
├── engine.py               Orchestrateur : run_qa() enchaîne les étapes ci-dessous
├── config.py                Constantes de configuration (colonnes Excel, regex, seuils)
├── text_utils.py             Nettoyage et normalisation de texte (clean_text, normalize, normalize_currency)
├── html_extraction.py         Extraction du wording depuis le HTML / récupération AEM
├── excel_loader.py            Lecture de la feuille Excel référentielle
├── matching.py                 compare() + stratégies de matching HTML <-> Excel (mode Wording)
├── wording_qa.py                Pipeline de QA "wording" (run_wording_qa)
├── url_utils.py                  Extraction des href + normalisation fonctionnelle des URLs
├── url_qa.py                    Pipeline de QA "url" (run_url_qa)
└── report.py                   Génération du rapport Excel (Summary / Details / KO Only)
temp/                       Fichiers uploadés temporairement (non versionné)
output/                     Rapports Excel générés (non versionné)
```

### Flux d'exécution (`run_qa`)

1. Si une URL AEM est fournie, le HTML est téléchargé (`fetch_html_from_aem`) et sauvegardé dans `temp/aem_input.html`.
2. Selon le `mode` :
   - **`wording`** : `html_extraction.load_and_extract_html_texts` parse le HTML et retourne la liste des textes trouvés (`{text, tag, type, component_class}`) ; `excel_loader.load_excel_wordings` sélectionne l'onglet Excel correspondant au `keyword` de campagne et retourne la liste des wordings attendus (`{Component, Element, Wording, Technical, norm, curr}`) ; `wording_qa.run_wording_qa` compare les deux.
   - **`url`** : `html_extraction.load_raw_html` retourne le HTML brut (reconstruit si nécessaire) ; `excel_loader.load_excel_urls` sélectionne l'onglet dont le nom contient `"UTM"` et retourne la liste des URLs trackées attendues ; `url_qa.run_url_qa` compare les deux.
3. `report.save_report` génère le rapport Excel dans `output/` (même format de sortie pour les deux modes).

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

## Utilisation

1. **Type de test** : `Wording` ou `URL`.
2. **Source du HTML** :
   - soit uploader un fichier `.html` (export "view-source" AEM ou HTML brut),
   - soit renseigner un **lien AEM** + **identifiant/mot de passe** pour le récupérer automatiquement.
3. Uploader le **fichier Excel référentiel** (`.xlsx`).
4. En mode **Wording**, renseigner le **Campaign keyword** : mot-clé recherché (insensible à la casse) dans le nom des onglets du fichier Excel pour identifier la bonne feuille. Le mot-clé doit correspondre à un **seul** onglet, sinon une erreur explicite est renvoyée. En mode **URL**, ce champ n'apparaît pas : l'onglet est retrouvé automatiquement (nom contenant `"UTM"`).
5. Cliquer sur **🔍 Comparer**.
6. Consulter :
   - le taux de succès global et la répartition des types d'erreur,
   - le tableau détaillé (lignes vertes = OK, rouges = KO, diff mot-à-mot en gras rouge sur les KO),
   - télécharger le rapport Excel complet via le bouton **⬇️ Télécharger le rapport Excel**.

## Format attendu du fichier Excel

- Une feuille par campagne, dont le **nom** contient le `Campaign keyword` saisi dans l'app.
- **En-têtes sur la 2ᵉ ligne** de la feuille (la 1ʳᵉ ligne est ignorée).
- Colonnes attendues :

  | Colonne | Rôle |
  |---|---|
  | `COMPONENT NAME` | Nom du composant (reporté sur les lignes vides jusqu'au composant suivant) |
  | `ELEMENT` | Élément du composant (titre, CTA, paragraphe...) |
  | `TEXT EN` | Wording de référence (nom configurable, voir [Configuration](#configuration)) |
  | `TECHNICAL NAME` | Classe technique AEM (`audit-component-xxx`) utilisée pour matcher avec le HTML |

- Les placeholders entre crochets (ex. `[gift validity end date]`) sont automatiquement retirés avant comparaison.
- Certaines lignes sont ignorées systématiquement (`Subject` / `Header - Loyalty program name`, voir `EXCLUDED_QA_ROWS`).

En **mode URL**, l'onglet référentiel est différent :

- Son **nom** doit contenir `"UTM"` (insensible à la casse, peut contenir d'autres mots, ex. `"DS - Antichurn S2 UTM"`) — un seul onglet doit correspondre.
- **En-têtes sur la 1ʳᵉ ligne** (contrairement à l'onglet wording).
- Colonne attendue : `<< TRACKED URL >>`, contenant la liste des URLs trackées (avec paramètres UTM) à retrouver dans le HTML.

## Comment fonctionne le matching

Pour chaque texte extrait du HTML, `qa/wording_qa.py` essaie, dans l'ordre, les stratégies définies dans `qa/matching.py` :

1. **Direct** (`find_best_match`) — comparaison 1 pour 1 avec les wordings Excel partageant la même classe technique (`TECHNICAL NAME` ↔ classe CSS `audit-component-*` trouvée autour de l'élément HTML).
2. **Agrégé** (`find_aggregated_match`) — regroupe 2 à 3 blocs HTML consécutifs (ex. titre + paragraphe) pour matcher une seule cellule Excel, utile quand le découpage HTML est plus fin que le découpage Excel.
3. **Split** (`find_split_match`) — accepte le cas où le texte HTML est une sous-phrase d'une cellule Excel (typiquement quand un placeholder Excel a été retiré côté HTML).
4. **Closest match** (`find_closest_match`) — en dernier recours, retient le wording Excel le plus proche par similarité (`difflib.SequenceMatcher`, seuil ≥ 55 %) :
   - classe technique différente → KO `Wording in wrong component`,
   - similarité ≥ 85 % → OK,
   - sinon → KO `Wording differs`.

La comparaison texte à texte elle-même (`compare()`) normalise les deux chaînes (`normalize()` dans `qa/text_utils.py` : retrait des tags AEM, placeholders, caractères invisibles) puis classe le résultat :

| Cas | Statut | Error Type |
|---|---|---|
| Chaînes vides des deux côtés | OK | — |
| Une seule chaîne vide | KO | `Text differs` |
| Égalité stricte après normalisation | OK | — |
| Égalité en ignorant la casse | KO | `Case Error` |
| Similarité ≥ 95 % | KO | `Typo` (quasi-exact) |
| Similarité ≥ 80 % | KO | `Typo` (fuzzy) |
| Égalité après normalisation des devises | OK | — |
| Sinon | KO | `Text differs` |

Chaque wording Excel non couvert par un match HTML est reporté en fin de tableau avec le statut `Not in HTML` (ou `Wording differs` si un texte HTML proche existe), et chaque doublon de KO identique (même paire HTML/Excel) est dédupliqué.

## Comment fonctionne la QA URL

L'onglet Excel de référence est celui dont le nom contient `"UTM"` (configurable via `URL_SHEET_KEYWORD`, voir [Configuration](#configuration)) ; les URLs attendues sont lues dans sa colonne `<< TRACKED URL >>` (`URL_COLUMN_NAME`). Tous les `href` du HTML sont extraits (`qa/url_utils.extract_hrefs`), puis chaque URL Excel est recherchée parmi eux (`qa/url_qa.run_url_qa`) :

| Cas | Statut | Match Type | Criticité |
|---|---|---|---|
| URL identique une fois les entités HTML décodées (`&amp;` → `&`) | OK | `Exact` | — |
| Même destination et mêmes paramètres UTM, mais ordre ou pourcent-encodage différent | KO | `Functional match only` | Medium |
| Aucune correspondance trouvée dans le HTML | KO | `No match` | High |

Le décodage des entités HTML est appliqué avant de juger un match "Exact" car AEM encode systématiquement les `href` ainsi (`&amp;` au lieu de `&`) — ce n'est pas une différence de contenu réelle. En revanche, un ordre de paramètres différent ou un encodage `%XX` différent reste considéré comme un écart (`Functional match only`), car l'URL n'est alors pas rendue à l'identique de ce qui a été validé.

La normalisation fonctionnelle (`qa/url_utils.normalize_url`) décode les entités HTML et le pourcent-encodage, puis retrie les paramètres de query pour comparer deux URLs indépendamment de l'ordre de leurs paramètres.

## Rapport de sortie

Le rapport Excel est généré dans `output/QA_<nom_feuille>_<horodatage>.xlsx` (dossier non versionné, créé automatiquement) et contient 3 onglets :

- **Summary** : nom de la campagne, date, total, nombre d'OK/KO, score global (coloré vert/orange/rouge selon le score).
- **Details** : toutes les lignes, KO triés par criticité puis par composant en premier, OK ensuite.
- **KO Only** : uniquement les écarts, avec le diff mot-à-mot en rouge gras entre `Excel Wording` et `HTML Wording`.

Chaque ligne comporte : `Component Name`, `Element`, `Expected Class`, `HTML Class`, `Excel Wording`, `HTML Wording`, `Status`, `Error Type`, `Similarity`, `Criticality`, `Match Type`.

## Configuration

Les réglages globaux sont centralisés dans [`qa/config.py`](qa/config.py) :

| Constante | Rôle | Valeur par défaut |
|---|---|---|
| `WORDING_COLUMN_NAME` | Colonne Excel contenant le wording de référence | `"TEXT EN"` |
| `CAMPAIGN_KEYWORD` | Mot-clé par défaut pour l'exécution standalone (`python -m qa.engine`) | `"HugPlan S1 Bronze"` |
| `MIN_TEXT_LENGTH` | Longueur minimale d'un texte pour être pris en compte | `3` |
| `DEBUG_MODE` | Active les logs détaillés d'extraction dans la console | `True` |
| `EXCLUDED_QA_ROWS` | Paires (Component, Element) Excel ignorées systématiquement | `subject/subject`, `header/loyalty program name` |
| `URL_SHEET_KEYWORD` | Mot-clé pour repérer l'onglet des URLs trackées (mode URL) | `"UTM"` |
| `URL_COLUMN_NAME` | Colonne des URLs de référence dans cet onglet | `"<< TRACKED URL >>"` |
| `OUTPUT_DIR` | Dossier de sortie des rapports | `"output"` |

## Limitations connues / roadmap

- **Fichiers temporaires partagés** : les uploads sont toujours écrits dans `temp/input.html` / `temp/input.xlsx`, quel que soit l'utilisateur — deux analyses lancées en même temps peuvent se marcher sur les pieds.
- **Pas de tests automatisés.**
- **Mode URL** : ne vérifie que la présence des URLs Excel dans le HTML (comme le script d'origine) — ne détecte pas les `href` présents dans le HTML mais absents du référentiel Excel.
