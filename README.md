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
  - nettoyage des artefacts techniques : directives `<% %>` / `<%= %>`, variables `${...}`, placeholders `[xxx]`, caractères invisibles (LRM/RLM), entités HTML,
  - les liens (`<a>`) individuels à l'intérieur d'un composant de mentions légales/footer (classe contenant `"legal"` ou `"footer"`, voir `LEGAL_MENTION_CLASS_KEYWORDS`) sont ignorés : leur texte est déjà couvert par le paragraphe complet qui les englobe.
- **Matching tolérant** entre HTML et Excel, avec plusieurs stratégies en cascade (direct, agrégation de blocs, sous-phrase, meilleure correspondance approximative) et un traitement dédié aux devises (séparateurs `|`, `/`, `I`).
- **Dashboard Streamlit** : taux de succès global, répartition des erreurs par type (camemberts Plotly), tableau interactif (AgGrid) avec diff mot-à-mot en couleur entre le wording attendu et le wording trouvé.
- **Rapport Excel exportable** (`Summary` / `Details` / `KO Only`) avec mise en forme conditionnelle (couleurs par statut/criticité) et diff en rich text.
- **Mode URL** : vérification des liens (`href`) du HTML par rapport à la liste d'URLs trackées du référentiel Excel (correspondance exacte ou fonctionnelle sur les paramètres UTM) — voir [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url).
- **Diagnostic à l'upload** : dès que l'Excel est chargé, l'app affiche les onglets détectés, si l'onglet/la colonne attendue est trouvée, et — en mode Wording — le taux de remplissage de `TECHNICAL NAME`, pour repérer un référentiel incomplet avant même de lancer la comparaison.
- **Mode multi-séquences** : traiter plusieurs séquences d'une même campagne (plusieurs fichiers HTML + un seul Excel) en une fois, avec un rapport Excel consolidé et un récapitulatif par séquence — voir [Traiter plusieurs séquences en une fois](#traiter-plusieurs-séquences-en-une-fois).
- **Composants génériques** (mode Wording) : si le référentiel Wording d'une campagne ne couvre pas les composants communs à toutes les séquences (footer, mentions légales, bloc fidélité...), une case à cocher permet de les fusionner automatiquement depuis un onglet séparé — voir [Composants génériques](#composants-génériques).
- **Logs de debug dans l'UI** : le détail de l'extraction (auparavant visible seulement dans la console du serveur) est affiché dans un expander repliable après chaque comparaison, et a été réduit à l'essentiel (plus de trace ligne par ligne de chaque texte extrait).
- **Fichiers temporaires isolés par session** : chaque session de navigateur utilise ses propres fichiers dans `temp/`, deux analyses lancées en parallèle ne s'écrasent plus.

## Architecture du projet

```
app.py                     Interface Streamlit : upload, lancement de la QA, affichage, export
qa/
├── engine.py               Orchestrateur : run_qa_for_sequence() (1 sequence) et run_qa() (+ rapport)
├── config.py                Constantes de configuration (colonnes Excel, regex, seuils)
├── text_utils.py             Nettoyage et normalisation de texte (clean_text, normalize, normalize_currency)
├── html_extraction.py         Extraction du wording depuis le HTML / récupération AEM
├── excel_loader.py            Lecture de la feuille Excel référentielle
├── matching.py                 compare() + stratégies de matching HTML <-> Excel (mode Wording)
├── wording_qa.py                Pipeline de QA "wording" (run_wording_qa)
├── url_utils.py                  Extraction des href + normalisation fonctionnelle des URLs
├── url_qa.py                    Pipeline de QA "url" (run_url_qa)
└── report.py                   Génération des rapports Excel (save_report, save_consolidated_report)
temp/                       Fichiers uploadés temporairement, isolés par session (non versionné)
output/                     Rapports Excel générés (non versionné)
```

### Flux d'exécution (`run_qa`)

1. Si une URL AEM est fournie, le HTML est téléchargé (`fetch_html_from_aem`) et sauvegardé dans un fichier temporaire.
2. `run_qa_for_sequence` exécute l'extraction + le matching pour une sequence, selon le `mode` :
   - **`wording`** : `html_extraction.load_and_extract_html_texts` parse le HTML et retourne la liste des textes trouvés (`{text, tag, type, component_class}`) ; `excel_loader.load_excel_wordings` sélectionne l'onglet Excel correspondant au `keyword` de campagne et retourne la liste des wordings attendus (`{Component, Element, Wording, Technical, norm, curr}`). Si `include_generic=True`, `excel_loader.load_generic_wordings` charge en plus l'onglet des composants génériques et ses wordings sont ajoutés à la liste avant le matching (voir [Composants génériques](#composants-génériques)). `wording_qa.run_wording_qa` compare le tout au HTML.
   - **`url`** : `html_extraction.load_raw_html` retourne le HTML brut (reconstruit si nécessaire) ; `excel_loader.load_excel_urls` sélectionne l'onglet dont le nom contient `"UTM"` et retourne la liste des URLs trackées attendues ; `url_qa.run_url_qa` compare les deux.
3. `report.save_report` génère le rapport Excel dans `output/` (même format de sortie pour les deux modes).

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

## Utilisation

1. **Type de test** : `Wording` ou `URL`.
2. **Source du HTML** :
   - soit uploader un fichier `.html` (export "view-source" AEM ou HTML brut),
   - soit renseigner un **lien AEM** + **identifiant/mot de passe** pour le récupérer automatiquement.
3. Uploader le **fichier Excel référentiel** (`.xlsx`) : dès qu'il est chargé, le champ suivant devient un **menu déroulant** proposant directement les onglets du classeur (mode Wording) ou les séquences détectées dans l'onglet UTM (mode URL) — plus besoin de taper un mot-clé exact. Tant qu'aucun Excel n'est uploadé, un champ texte "Campaign keyword" fait office de repli.
4. Choisir l'**onglet Excel** (mode Wording) ou la **séquence** (mode URL, voir [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url)) dans la liste. En mode URL, si l'onglet UTM ne regroupe aucune séquence (onglet à plat), aucun choix n'est nécessaire.
5. Cliquer sur **🔍 Comparer**.
6. Consulter :
   - le taux de succès global et la répartition des types d'erreur,
   - le tableau détaillé (lignes vertes = OK, rouges = KO, diff mot-à-mot en gras rouge sur les KO),
   - télécharger le rapport Excel complet via le bouton **⬇️ Télécharger le rapport Excel**,
   - le détail de l'extraction dans l'expander **🐛 Logs de debug** (utile pour comprendre pourquoi un texte/une URL n'a pas été trouvé).

## Traiter plusieurs séquences en une fois

Une campagne a souvent plusieurs séquences (Seq1, Seq2, Seq3...), chacune avec son propre HTML mais partageant le même classeur Excel référentiel. Le mode multi-séquences évite de relancer l'app pour chacune :

1. Cocher **📦 Traiter plusieurs séquences en une fois**. Le champ Lien AEM disparaît : ce mode ne fonctionne qu'avec des fichiers HTML uploadés (pas de récupération AEM par URL).
2. Uploader le fichier Excel référentiel (unique) et **plusieurs fichiers HTML** (un par séquence).
3. Pour chaque fichier HTML, choisir dans la liste déroulante la séquence correspondante — la liste propose les **onglets** du classeur en mode Wording, ou les **séquences détectées dans l'onglet UTM** en mode URL (voir [Comment fonctionne la QA URL](#comment-fonctionne-la-qa-url)). Comme il s'agit d'un choix dans une liste (pas d'un mot-clé tapé), il n'y a pas de risque d'ambiguïté.
4. Cliquer sur **🔍 Comparer toutes les séquences**.
5. Consulter le récapitulatif par séquence (Total/OK/KO/Score), le tableau combiné (colonne `Sequence` ajoutée), et télécharger le **rapport Excel consolidé** unique (`QA_MULTI_..._<horodatage>.xlsx`, voir [Rapport de sortie](#rapport-de-sortie)).

Si une séquence échoue (onglet/colonne introuvable), l'erreur est affichée pour ce fichier précis et les autres séquences continuent d'être traitées normalement.

## Composants génériques

Certaines campagnes séparent dans l'Excel les wordings **spécifiques à la séquence** (ceux qui changent d'une séquence à l'autre) des composants **génériques**, communs à toutes les séquences d'une campagne (footer, mentions légales, bloc fidélité, bloc réassurance...), regroupés dans un onglet à part.

- Cocher **🧩 Les composants génériques sont dans un onglet séparé de l'Excel** (mode Wording uniquement, disponible en mode simple et en mode multi-séquences).
- L'onglet correspondant est retrouvé automatiquement (nom contenant `"GENERIC"`, configurable via `GENERIC_SHEET_KEYWORD`) et ses wordings sont **ajoutés** à ceux de la séquence testée avant le matching — aucune autre action requise.
- Structure attendue, identique à l'onglet de campagne à un détail près : la colonne wording de cet onglet est recherchée via `"TRANSLATION"` **ou** `"TEXT"` (le premier qui donne une correspondance unique), car un onglet générique peut porter un texte déjà localisé dans une colonne simplement nommée `"TEXT"` plutôt que `"TRANSLATION ..."`.
- Si l'onglet générique est introuvable ou que sa colonne wording est ambiguë, une erreur explicite est renvoyée (dans le diagnostic en mode simple, ou pour le fichier concerné en mode multi-séquences) plutôt que d'ignorer silencieusement les composants génériques.

## Format attendu du fichier Excel

- Une feuille par campagne, dont le **nom** contient le `Campaign keyword` saisi dans l'app.
- **En-têtes sur la 2ᵉ ligne** de la feuille (la 1ʳᵉ ligne est ignorée).
- Colonnes attendues :

  | Colonne | Rôle |
  |---|---|
  | `COMPONENT NAME` | Nom du composant (reporté sur les lignes vides jusqu'au composant suivant) |
  | `ELEMENT` | Élément du composant (titre, CTA, paragraphe...) |
  | *(colonne contenant `TRANSLATION`)* | Wording de référence à comparer au HTML — recherché par mot-clé insensible à la casse (ex. `"TRANSLATION GERMAN + ..."`), pas par nom exact. Mot-clé configurable, voir [Configuration](#configuration). |
  | `TECHNICAL NAME` | Classe technique AEM (`audit-component-xxx`) utilisée pour matcher avec le HTML |

- Les placeholders entre crochets (ex. `[gift validity end date]`) sont automatiquement retirés avant comparaison.
- Certaines lignes sont ignorées systématiquement (`Subject` / `Header - Loyalty program name`, voir `EXCLUDED_QA_ROWS`).

En **mode URL**, l'onglet référentiel est différent :

- Son **nom** doit contenir `"UTM"` (insensible à la casse, peut contenir d'autres mots, ex. `"DS - Antichurn S2 UTM"`) — un seul onglet doit correspondre.
- Colonnes attendues (en-têtes sur la ligne où figure `<< TRACKED URL >>`, quelle que soit sa position) : une colonne exactement nommée `"URL"`, et la colonne `<< TRACKED URL >>` contenant les URLs trackées (avec paramètres UTM) à retrouver dans le HTML.
- Si l'onglet regroupe **plusieurs séquences** (ex. `SEQ 1 NON FID` et `SEQ 1 FID` empilées dans le même onglet), chaque séquence commence par une ligne "séparateur" ne contenant que son nom dans la colonne `"URL"` — c'est ce nom que le `Campaign keyword` doit cibler. S'il n'y a pas de séparateur de ce type, tout l'onglet est utilisé sans filtrage.

## Comment fonctionne le matching

Pour chaque texte extrait du HTML, `qa/wording_qa.py` essaie, dans l'ordre, les stratégies définies dans `qa/matching.py` :

1. **Direct** (`find_best_match`) — comparaison 1 pour 1 avec les wordings Excel partageant la même classe technique (`TECHNICAL NAME` ↔ classe CSS `audit-component-*` trouvée autour de l'élément HTML).
2. **Agrégé** (`find_aggregated_match`) — regroupe 2 à 3 blocs HTML consécutifs (ex. titre + paragraphe) pour matcher une seule cellule Excel, utile quand le découpage HTML est plus fin que le découpage Excel.
3. **Split** (`find_split_match`) — accepte le cas où le texte HTML est une sous-phrase d'une cellule Excel (typiquement quand un placeholder Excel a été retiré côté HTML, ou qu'un fragment HTML couvre une bonne partie d'une cellule combinée). Deux garde-fous contre les faux positifs : le fragment doit couvrir au moins 40 % de la longueur de la cellule Excel (`min_ratio`), et si le HTML et la cellule ont chacun une classe technique connue, elles doivent correspondre (sinon un fragment court peut coïncider par hasard avec une cellule sans rapport, ex. un mot isolé qui apparaît dans un tout autre paragraphe).
4. **Closest match** (`find_closest_match`) — en dernier recours, retient le wording Excel le plus proche par similarité (`difflib.SequenceMatcher`, seuil ≥ 55 %) :
   - classe technique différente → KO `Wording in wrong component`,
   - similarité ≥ 85 % → OK,
   - sinon → KO `Wording differs`.

Les étapes 1 et 2 ne verrouillent leur résultat que s'il est **OK** : un candidat de la bonne classe mais textuellement très différent (KO) n'empêche pas d'essayer l'étape suivante, qui peut trouver un meilleur match pour ce même fragment HTML. Le KO de l'étape 1/2 n'est utilisé que si aucune étape suivante ne trouve de OK.

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

L'onglet Excel de référence est celui dont le nom contient `"UTM"` (configurable via `URL_SHEET_KEYWORD`, voir [Configuration](#configuration)).

Cet onglet regroupe souvent plusieurs séquences empilées (une pour chaque variante de campagne : loyal/non-loyal, palier de fidélité...). `qa/excel_loader.load_excel_urls` repère les lignes "séparateur" (une seule cellule non vide, dans la colonne `"URL"`, contenant le nom de la séquence) pour découper l'onglet en sections, puis ne garde que les URLs de la section dont le nom correspond au `Campaign keyword` saisi (recherche insensible à la casse, comme pour la sélection d'onglet). Si aucun séparateur n'est trouvé, l'onglet est traité comme une liste plate et le keyword est ignoré.

Les URLs attendues de la séquence retenue sont lues dans la colonne `<< TRACKED URL >>` (`URL_COLUMN_NAME`). Tous les `href` du HTML sont extraits (`qa/url_utils.extract_hrefs`), puis chaque URL Excel est recherchée parmi eux (`qa/url_qa.run_url_qa`) :

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

En mode multi-séquences, `report.save_consolidated_report` génère un rapport unique `QA_MULTI_..._<horodatage>.xlsx` : les onglets `Details`/`KO Only` ajoutent une colonne `Sequence` (tri par séquence puis criticité), et le `Summary` ajoute un tableau récapitulatif Total/OK/KO/Score par séquence en plus du score global.

## Configuration

Les réglages globaux sont centralisés dans [`qa/config.py`](qa/config.py) :

| Constante | Rôle | Valeur par défaut |
|---|---|---|
| `WORDING_COLUMN_NAME` | Mot-clé (insensible à la casse) pour repérer la colonne wording de référence | `"TRANSLATION"` |
| `CAMPAIGN_KEYWORD` | Mot-clé par défaut pour l'exécution standalone (`python -m qa.engine`) | `"HugPlan S1 Bronze"` |
| `MIN_TEXT_LENGTH` | Longueur minimale d'un texte pour être pris en compte | `3` |
| `DEBUG_MODE` | Active les logs détaillés d'extraction dans la console | `True` |
| `EXCLUDED_QA_ROWS` | Paires (Component, Element) Excel ignorées systématiquement | `subject/subject`, `header/loyalty program name` |
| `URL_SHEET_KEYWORD` | Mot-clé pour repérer l'onglet des URLs trackées (mode URL) | `"UTM"` |
| `URL_COLUMN_NAME` | Colonne des URLs de référence dans cet onglet | `"<< TRACKED URL >>"` |
| `GENERIC_SHEET_KEYWORD` | Mot-clé pour repérer l'onglet des composants génériques | `"GENERIC"` |
| `OUTPUT_DIR` | Dossier de sortie des rapports | `"output"` |

## Limitations connues / roadmap

- **Pas de tests automatisés.**
- **Mode URL** : ne vérifie que la présence des URLs Excel dans le HTML (comme le script d'origine) — ne détecte pas les `href` présents dans le HTML mais absents du référentiel Excel.
- **Découpage en séquences de l'onglet UTM** : ne gère qu'un seul niveau de séparateurs. Si un onglet imbrique des sous-catégories à l'intérieur d'une séquence (ex. des séparateurs répétés comme "FOOTER" dans chaque séquence), elles seront traitées comme des séquences distinctes plutôt que comme des sous-groupes.
- **`temp/` n'est jamais nettoyé automatiquement** : les fichiers y sont désormais isolés par session (pas de risque d'écrasement), mais ils s'accumulent d'une session à l'autre.
