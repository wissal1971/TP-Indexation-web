# TP1 – Indexation Web (Préparation d’un moteur de recherche)

## Objectif

L’objectif de ce projet est de construire différents **index à partir d’un jeu de données e-commerce**
afin de préparer la mise en place d’un moteur de recherche.

Les index sont construits à partir d’un fichier JSONL contenant des pages produits,
leurs descriptions, leurs caractéristiques et leurs avis.

---

## Données d’entrée

- Fichier : `data/products.jsonl`
- Format : JSONL (un document par ligne)
- Champs principaux :
  - `url`
  - `title`
  - `description`
  - `product_features`
  - `product_reviews`
  - `links`

Le jeu de données contient à la fois :
- des pages produit (`/product/<id>`)
- des pages non-produit (catalogue, pagination, etc.)

---

## Étape 1 – Lecture et traitement des URLs

Le fichier JSONL est parsé ligne par ligne.

Pour chaque URL :
- **product_id** : numéro extrait depuis le chemin `/product/<id>`
- **variant** : valeur du paramètre `?variant=` si présent

Les pages sans `product_id` sont conservées pour les index basés sur les URLs,
mais ignorées pour les index basés sur les caractéristiques produit.

---

## Prétraitement du texte

Les champs textuels (titre, description, features textuelles) sont traités de la manière suivante :

- Tokenization par **espace**
- Passage en **minuscules**
- Suppression de la **ponctuation**
- Suppression d’une liste de **stopwords** (français + anglais)
- Normalisation des apostrophes (`kids'` → `kids`)

---

## Index construits

Chaque index est sauvegardé dans un **fichier JSON distinct**.
Les fichiers contiennent **uniquement la partie utile de l’index** (sans métadonnées supplémentaires).

---

### 1. Index du titre – `title_index.json`

- Type : **index inversé**
- Structure :

```json
token -> [url1, url2, ...]
Chaque token du champ title est associé à la liste des URLs des documents
dans lesquels il apparaît.

2. Index de la description – description_index.json
Type : index inversé

Structure identique à l’index du titre :

json
Copier le code
token -> [url1, url2, ...]
3. Index des reviews – reviews_index.json
Type : index non inversé

Structure :

json
Copier le code
url -> {
  total_reviews,
  avg_rating,
  last_rating
}
Cet index ne contient aucune information textuelle.
Il est destiné à faire remonter les documents selon la qualité des avis.

4. Index des features – marque – brand_index.json
Type : index inversé

Champ traité : product_features["brand"]

Structure :

json
Copier le code
token -> [url1, url2, ...]
Les valeurs de marque sont traitées comme du texte.

5. Index des features – origine – origin_index.json
Type : index inversé

Champ traité : product_features["made in"]

Structure :

json
Copier le code
token -> [url1, url2, ...]
Dans ce jeu de données, l’origine du produit est indiquée via la clé "made in".

Index de position (bonus)
Pour les champs titre et description, un index positionnel est également généré
afin de stocker la position des tokens dans chaque document.

Structure générale :

json
Copier le code
token -> {
  url -> [positions]
}
Ces index peuvent être utilisés ultérieurement pour :

recherche exacte

proximité de termes

scoring plus avancé

Choix d’implémentation
Un seul script Python (indexer.py)

Une fonction = une action (lecture, tokenization, indexation, sauvegarde)

Aucun package externe (uniquement la bibliothèque standard Python)

Sauvegarde des index en JSON lisible (indent=2)

Les fichiers de sortie contiennent uniquement les structures d’index,
afin de rester clairs et compacts

Comment lancer le code
Depuis la racine du projet :

bash
Copier le code
python indexer.py
Les fichiers d’index sont générés dans le dossier :

text
Copier le code
out_indexes/
Fichiers de sortie
text
Copier le code
out_indexes/
├── title_index.json
├── description_index.json
├── brand_index.json
├── origin_index.json
└── reviews_index.json
Exemple d’utilisation
Chargement d’un index :

python
Copier le code
import json

with open("out_indexes/origin_index.json", encoding="utf-8") as f:
    origin_index = json.load(f)

print(origin_index["italy"])