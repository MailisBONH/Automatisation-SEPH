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
- **Mode URL** (à venir) : vérification des liens (`href`) du HTML par rapport au référentiel — le pipeline est déjà prêt à l'accueillir (voir [Limitations connues / roadmap](#limitations-connues--roadmap)).

## Architecture du projet

```
app.py                     Interface Streamlit : upload, lancement de la QA, affichage, export
qa/
├── engine.py               Orchestrateur : run_qa() enchaîne les étapes ci-dessous
├── config.py                Constantes de configuration (colonnes Excel, regex, seuils)
├── text_utils.py             Nettoyage et normalisation de texte (clean_text, normalize, normalize_currency)
├── html_extraction.py         Extraction du wording depuis le HTML / récupération AEM
├── excel_loader.py            Lecture de la feuille Excel référentielle
├── matching.py                 compare() + stratégies de matching HTML <-> Excel
├── wording_qa.py                Pipeline de QA "wording" (run_wording_qa)
├── url_qa.py                    Pipeline de QA "url" (run_url_qa — stub, à implémenter)
└── report.py                   Génération du rapport Excel (Summary / Details / KO Only)
temp/                       Fichiers uploadés temporairement (non versionné)
output/                     Rapports Excel générés (non versionné)
```

### Flux d'exécution (`run_qa`)

1. Si une URL AEM est fournie, le HTML est téléchargé (`fetch_html_from_aem`) et sauvegardé dans `temp/aem_input.html`.
2. `html_extraction.load_and_extract_html_texts` parse le HTML et retourne la liste des textes trouvés (`{text, tag, type, component_class}`).
3. `excel_loader.load_excel_wordings` sélectionne l'onglet Excel correspondant au mot-clé de campagne et retourne la liste des wordings attendus (`{Component, Element, Wording, Technical, norm, curr}`).
4. `wording_qa.run_wording_qa` (ou `url_qa.run_url_qa` en mode URL) compare les deux listes et produit une liste de résultats (`Status`, `Error Type`, `Similarity`, `Criticality`, ...).
5. `report.save_report` génère le rapport Excel dans `output/`.

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

1. **Type de test** : `Wording` (disponible) ou `URL` (pas encore implémenté).
2. **Source du HTML** :
   - soit uploader un fichier `.html` (export "view-source" AEM ou HTML brut),
   - soit renseigner un **lien AEM** + **identifiant/mot de passe** pour le récupérer automatiquement.
3. Uploader le **fichier Excel référentiel** (`.xlsx`).
4. Renseigner le **Campaign keyword** : mot-clé recherché (insensible à la casse) dans le nom des onglets du fichier Excel pour identifier la bonne feuille. Le mot-clé doit correspondre à un **seul** onglet, sinon une erreur explicite est renvoyée.
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
| `OUTPUT_DIR` | Dossier de sortie des rapports | `"output"` |

## Limitations connues / roadmap

- **Mode URL non implémenté** : `qa/url_qa.py` contient le stub `run_url_qa` (lève `NotImplementedError`). `run_qa(..., mode="url")` route déjà vers cette fonction dans `qa/engine.py` — il reste à ajouter l'extraction des `href` du HTML (probablement dans `html_extraction.py`) et la comparaison correspondante avec le référentiel Excel.
- **Fichiers temporaires partagés** : les uploads sont toujours écrits dans `temp/input.html` / `temp/input.xlsx`, quel que soit l'utilisateur — deux analyses lancées en même temps peuvent se marcher sur les pieds.
- **Pas de tests automatisés.**
- **Vérification TLS désactivée** sur les appels vers AEM (`verify=False` dans `fetch_html_from_aem`) — à activer si les certificats AEM sont valides côté réseau.
