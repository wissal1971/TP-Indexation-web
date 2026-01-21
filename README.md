# TP2 – Indexation Web

## Objectif

L’objectif de ce projet est de construire différents **index à partir d’un jeu de données e-commerce**
afin de préparer la mise en place d’un moteur de recherche.

Les index sont construits à partir d’un fichier JSONL contenant des pages produits,
leurs descriptions, leurs caractéristiques et leurs avis.

---

## Données d’entrée

- Fichier : `data/products.jsonl`
- Format : JSONL (un document par ligne)

Champs principaux :
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

## Traitement des URLs

Le fichier JSONL est lu ligne par ligne.

Pour chaque URL :
- **product_id** : identifiant extrait depuis `/product/<id>`
- **variant** : valeur du paramètre `?variant=` s’il est présent

Les pages sans `product_id` sont conservées pour les index basés sur les URLs,
mais ignorées pour les index basés sur les caractéristiques produit.

---

## Prétraitement du texte

Les champs textuels (titre, description, features) sont traités de la manière suivante :

- tokenisation par espace
- passage en minuscules
- suppression de la ponctuation
- suppression d’une liste de stopwords (français et anglais)
- normalisation des apostrophes

---

## Index construits

Chaque index est sauvegardé dans un **fichier JSON distinct**,
dans le dossier `out_indexes/`.

### 1. Index du titre – `title_index.json`

- Type : **index inversé**
- Structure :

token -> [url1, url2, ...]

yaml
Copier le code

---

### 2. Index de la description – `description_index.json`

- Type : **index inversé**
- Structure identique à l’index du titre.

---

### 3. Index des avis – `reviews_index.json`

- Type : **index non inversé**
- Structure :

url -> {
total_reviews,
avg_rating,
last_rating
}

yaml
Copier le code

Cet index permet de prendre en compte la qualité des avis.

---

### 4. Index des features – marque – `brand_index.json`

- Type : **index inversé**
- Champ traité : `product_features["brand"]`

---

### 5. Index des features – origine – `origin_index.json`

- Type : **index inversé**
- Champ traité : `product_features["made in"]`

---

## Index positionnel (bonus)

Pour les champs **titre** et **description**, un index positionnel est également généré.

Structure générale :

token -> {
url -> [positions]
}

yaml
Copier le code

Ces index peuvent être utilisés pour des recherches exactes
ou des calculs de score plus avancés.

---

## Choix d’implémentation

- Un seul script Python : `indexer.py`
- Une fonction par tâche (lecture, prétraitement, indexation, sauvegarde)
- Utilisation exclusive de la bibliothèque standard Python
- Sauvegarde des index en JSON lisible (`indent=2`)

---

## Exécution

Depuis la racine du projet :

```bash
python indexer.py
Les fichiers générés sont disponibles dans :

pgsql
Copier le code
out_indexes/
├── title_index.json
├── description_index.json
├── brand_index.json
├── origin_index.json
└── reviews_index.json
Exemple d’utilisation
python
Copier le code
import json

with open("out_indexes/origin_index.json", encoding="utf-8") as f:
    origin_index = json.load(f)

print(origin_index.get("italy", []))
