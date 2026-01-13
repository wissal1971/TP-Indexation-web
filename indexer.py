import json
import re
import string
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from collections import defaultdict

# =========================
# Paths
# =========================
DATA_PATH = Path("data/products.jsonl")
OUT_DIR = Path("out_indexes")

# =========================
# Tokenization config
# =========================
STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "d", "et", "ou", "à", "a",
    "the", "a", "an", "and", "or", "to", "of", "in", "for", "with", "on", "at",
}
PUNCT_TABLE = str.maketrans("", "", string.punctuation)

# =========================
# URL parsing
# =========================
PRODUCT_ID_RE = re.compile(r"/product/(\d+)(?:/|$)")


# =========================
# IO
# =========================
def load_jsonl(path: Path):
    """Yield one dict per JSONL line."""
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON at line {i}: {e}") from e


def save_json(obj, path: Path):
    """Save JSON with UTF-8 and indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# =========================
# URL helpers
# =========================
def extract_product_id(url: str):
    """Extract product id from URL path if present, else None."""
    if not url:
        return None
    parsed = urlparse(url)
    m = re.search(r"^/product/(\d+)/?$", parsed.path)
    return m.group(1) if m else None



def extract_variant(url: str):
    """Extract ?variant=... if present, else None."""
    if not url:
        return None
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return qs.get("variant", [None])[0]


# =========================
# Text helpers
# =========================
def normalize_token(raw: str):
    tok = raw.lower().translate(PUNCT_TABLE)
    tok = tok.replace("’", "").replace("'", "")
    return tok


def tokenize(text: str):
    """Space tokenization + cleaning + stopwords removal."""
    if not text:
        return []
    out = []
    for raw in text.split():
        tok = normalize_token(raw)
        if tok and tok not in STOPWORDS:
            out.append(tok)
    return out


def tokenize_with_positions(text: str):
    """Return list of (token, position_index_in_field)."""
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
# Doc prep
# =========================
def deduplicate_by_url(docs):
    """Keep first occurrence per URL."""
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
    Store links and identifiers for each document URL.
    This is what you called "les liens" (field `links`).
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
# Index builders
# =========================
def build_inverted_index_urls(docs, field_name: str):
    """token -> sorted list of document URLs."""
    index = defaultdict(set)
    for d in docs:
        url = d["url"]
        text = d.get(field_name, "") or ""
        for tok in tokenize(text):
            index[tok].add(url)
    return {tok: sorted(list(urls)) for tok, urls in index.items()}


def build_positional_index_urls(docs, field_name: str):
    """token -> {url -> [positions]}"""
    index = defaultdict(lambda: defaultdict(list))
    for d in docs:
        url = d["url"]
        text = d.get(field_name, "") or ""
        for tok, pos in tokenize_with_positions(text):
            index[tok][url].append(pos)
    return {tok: dict(url_map) for tok, url_map in index.items()}


def build_reviews_stats_index(docs):
    """
    url -> {total_reviews, avg_rating, last_rating}
    (NOT inverted)
    """
    index = {}
    for d in docs:
        url = d["url"]
        reviews = d.get("product_reviews", []) or []

        total = len(reviews)
        if total == 0:
            index[url] = {"total_reviews": 0, "avg_rating": None, "last_rating": None}
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

        index[url] = {"total_reviews": total, "avg_rating": avg, "last_rating": last_rating}
    return index


def build_feature_inverted_index_urls(docs, feature_key: str):
    """
    Feature inverted index:
      token -> sorted list of canonical product URLs (NO variants)
    Only documents that have a product_id and NO variant.
    """
    index = defaultdict(set)
    for d in docs:
        if not d.get("product_id"):
            continue
        if d.get("variant") is not None:
            continue  # skip variants

        url = d["url"]
        features = d.get("product_features", {}) or {}
        value = features.get(feature_key)
        if value is None:
            continue

        for tok in tokenize(str(value)):
            index[tok].add(url)

    return {tok: sorted(list(urls)) for tok, urls in index.items()}


# =========================
# Main
# =========================
def main():
    raw_docs = list(load_jsonl(DATA_PATH))

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

    docs = deduplicate_by_url(docs)
    print(f"Total documents read: {len(docs)}")

    # Common documents section (contains the 'links' field)
    documents_section = build_documents_section(docs)

    # TITLE index: inverted + positional, doc_id = URL
    title_inverted = build_inverted_index_urls(docs, "title")
    title_positional = build_positional_index_urls(docs, "title")

    # DESCRIPTION index: inverted + positional, doc_id = URL
    desc_inverted = build_inverted_index_urls(docs, "description")
    desc_positional = build_positional_index_urls(docs, "description")

    # REVIEWS stats index (not inverted)
    reviews_stats = build_reviews_stats_index(docs)

    # FEATURES: brand and origin (in this dataset origin == "made in")
    brand_index = build_feature_inverted_index_urls(docs, "brand")
    origin_index = build_feature_inverted_index_urls(docs, "made in")

    # Save ONLY 5 files, each one includes documents + index
    save_json(title_inverted, OUT_DIR / "title_index.json")
    save_json(desc_inverted, OUT_DIR / "description_index.json")
    save_json(brand_index, OUT_DIR / "brand_index.json")
    save_json(origin_index, OUT_DIR / "origin_index.json")
    save_json(reviews_stats, OUT_DIR / "reviews_index.json")


    print("✅ Saved 5 files into:", OUT_DIR.resolve())
    print(" - title_index.json (includes links + inverted + positional)")
    print(" - description_index.json (includes links + inverted + positional)")
    print(" - brand_index.json (includes links + token->URLs)")
    print(" - origin_index.json (includes links + token->URLs)")
    print(" - reviews_index.json (includes links + stats per URL)")


if __name__ == "__main__":
    main()
