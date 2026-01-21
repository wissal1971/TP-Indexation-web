import json
import re
import string
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from collections import defaultdict

# =========================
# Chemins des fichiers
# =========================
# Fichier d'entrée contenant les produits au format JSON Lines
DATA_PATH = Path("data/products.jsonl")

# Dossier de sortie pour stocker les index générés
OUT_DIR = Path("out_indexes")

# =========================
# Configuration de la tokenisation
# =========================
# Liste de mots très fréquents (français + anglais) à exclure de l’index
# car ils n’apportent pas d’information discriminante
STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "d", "et", "ou", "à", "a",
    "the", "a", "an", "and", "or", "to", "of", "in", "for", "with", "on", "at",
}

# Table de traduction permettant de supprimer la ponctuation
PUNCT_TABLE = str.maketrans("", "", string.punctuation)

# =========================
# Parsing des URLs
# =========================
# Expression régulière pour extraire l’identifiant produit depuis une URL
PRODUCT_ID_RE = re.compile(r"/product/(\d+)(?:/|$)")

# =========================
# Fonctions d’entrée / sortie
# =========================
def load_jsonl(path: Path):
    """
    Charge un fichier JSONL (une ligne = un objet JSON).
    Chaque ligne est convertie en dictionnaire Python.
    """
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"JSON invalide à la ligne {i} : {e}") from e


def save_json(obj, path: Path):
    """
    Sauvegarde un objet Python au format JSON lisible
    (indentation + encodage UTF-8).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

# =========================
# Fonctions utilitaires sur les URLs
# =========================
def extract_product_id(url: str):
    """
    Extrait l’identifiant du produit à partir de l’URL.
    Retourne None si le format ne correspond pas.
    """
    if not url:
        return None
    parsed = urlparse(url)
    m = re.search(r"^/product/(\d+)/?$", parsed.path)
    return m.group(1) if m else None


def extract_variant(url: str):
    """
    Extrait la variante du produit depuis les paramètres de l’URL
    (exemple : ?variant=123).
    """
    if not url:
        return None
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return qs.get("variant", [None])[0]

# =========================
# Fonctions de traitement du texte
# =========================
def normalize_token(raw: str):
    """
    Normalise un mot :
    - passage en minuscules
    - suppression de la ponctuation
    - suppression des apostrophes
    """
    tok = raw.lower().translate(PUNCT_TABLE)
    tok = tok.replace("’", "").replace("'", "")
    return tok


def tokenize(text: str):
    """
    Découpe un texte en tokens simples :
    - séparation par espaces
    - normalisation
    - suppression des stopwords
    """
    if not text:
        return []
    out = []
    for raw in text.split():
        tok = normalize_token(raw)
        if tok and tok not in STOPWORDS:
            out.append(tok)
    return out


def tokenize_with_positions(text: str):
    """
    Identique à tokenize(), mais conserve la position de chaque token
    dans le champ texte (utile pour un index positionnel).
    """
    if not text:
        return []
    out = []
    pos = 0
    for raw in text.split():
        tok = normalize_token(raw)
        if tok and tok not in STOPWORDS:
            out.append((tok, pos))
        pos += 1
    return out

# =========================
# Préparation des documents
# =========================
def deduplicate_by_url(docs):
    """
    Supprime les doublons en conservant uniquement
    la première occurrence de chaque URL.
    """
    seen = set()
    out = []
    for d in docs:
        u = d.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(d)
    return out


def build_documents_section(docs):
    """
    Construit une section 'documents' qui regroupe
    les métadonnées principales par URL.
    """
    documents = {}
    for d in docs:
        url = d["url"]
        documents[url] = {
            "product_id": d.get("product_id"),
            "variant": d.get("variant"),
            "links": d.get("links", []) or [],
        }
    return documents

# =========================
# Construction des index
# =========================
def build_inverted_index_urls(docs, field_name: str):
    """
    Construit un index inversé simple :
    token -> liste triée des URLs contenant ce token.
    """
    index = defaultdict(set)
    for d in docs:
        url = d["url"]
        text = d.get(field_name, "") or ""
        for tok in tokenize(text):
            index[tok].add(url)
    return {tok: sorted(list(urls)) for tok, urls in index.items()}


def build_positional_index_urls(docs, field_name: str):
    """
    Construit un index positionnel :
    token -> { URL -> liste des positions }
    """
    index = defaultdict(lambda: defaultdict(list))
    for d in docs:
        url = d["url"]
        text = d.get(field_name, "") or ""
        for tok, pos in tokenize_with_positions(text):
            index[tok][url].append(pos)
    return {tok: dict(url_map) for tok, url_map in index.items()}


def build_reviews_stats_index(docs):
    """
    Construit un index non inversé pour les avis :
    URL -> statistiques agrégées (nombre, moyenne, dernier avis).
    """
    index = {}
    for d in docs:
        url = d["url"]
        reviews = d.get("product_reviews", []) or []

        total = len(reviews)
        if total == 0:
            index[url] = {
                "total_reviews": 0,
                "avg_rating": None,
                "last_rating": None
            }
            continue

        ratings = [
            r.get("rating") for r in reviews
            if isinstance(r.get("rating"), (int, float))
        ]
        avg = (sum(ratings) / len(ratings)) if ratings else None

        def review_date(r):
            return r.get("date") or ""

        sorted_reviews = sorted(reviews, key=review_date)
        last_rating = sorted_reviews[-1].get("rating")

        index[url] = {
            "total_reviews": total,
            "avg_rating": avg,
            "last_rating": last_rating
        }
    return index


def build_feature_inverted_index_urls(docs, feature_key: str):
    """
    Construit un index inversé pour une caractéristique produit
    (ex : marque ou pays d’origine).
    Seuls les produits canoniques (sans variante) sont pris en compte.
    """
    index = defaultdict(set)
    for d in docs:
        if not d.get("product_id"):
            continue
        if d.get("variant") is not None:
            continue

        url = d["url"]
        features = d.get("product_features", {}) or {}
        value = features.get(feature_key)
        if value is None:
            continue

        for tok in tokenize(str(value)):
            index[tok].add(url)

    return {tok: sorted(list(urls)) for tok, urls in index.items()}

# =========================
# Fonction principale
# =========================
def main():
    # Chargement des documents bruts
    raw_docs = list(load_jsonl(DATA_PATH))

    # Normalisation des champs utiles
    docs = []
    for r in raw_docs:
        url = r.get("url")
        if not url:
            continue
        docs.append({
            "url": url,
            "title": r.get("title", ""),
            "description": r.get("description", ""),
            "links": r.get("links", []) or [],
            "product_features": r.get("product_features", {}) or {},
            "product_reviews": r.get("product_reviews", []) or [],
            "product_id": extract_product_id(url),
            "variant": extract_variant(url),
        })

    # Suppression des doublons
    docs = deduplicate_by_url(docs)
    print(f"Nombre total de documents : {len(docs)}")

    # Construction des index
    title_inverted = build_inverted_index_urls(docs, "title")
    title_positional = build_positional_index_urls(docs, "title")

    desc_inverted = build_inverted_index_urls(docs, "description")
    desc_positional = build_positional_index_urls(docs, "description")

    reviews_stats = build_reviews_stats_index(docs)

    brand_index = build_feature_inverted_index_urls(docs, "brand")
    origin_index = build_feature_inverted_index_urls(docs, "made in")

    # Sauvegarde des résultats
    save_json(title_inverted, OUT_DIR / "title_index.json")
    save_json(desc_inverted, OUT_DIR / "description_index.json")
    save_json(brand_index, OUT_DIR / "brand_index.json")
    save_json(origin_index, OUT_DIR / "origin_index.json")
    save_json(reviews_stats, OUT_DIR / "reviews_index.json")


if __name__ == "__main__":
    main()
