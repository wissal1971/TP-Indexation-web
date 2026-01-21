import os
import json
import math
import re
from dataclasses import dataclass
from typing import Dict, List, Set, Any, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit


# =========================================================
# IO
# =========================================================

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: str) -> List[dict]:
    docs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                docs.append(json.loads(line))
    return docs


def ensure_output_dir(base_dir: str) -> str:
    output_dir = os.path.join(base_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def save_results_to_json(output_path: str, results: dict) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


# =========================================================
# Tokenization
# =========================================================

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> str:
    return (text or "").lower()


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(normalize_text(text))


def remove_stopwords(tokens: List[str], stopwords: Set[str]) -> List[str]:
    return [t for t in tokens if t not in stopwords]


# =========================================================
# Stopwords
# =========================================================

def get_default_stopwords() -> Set[str]:
    # Minimal fallback stopwords list (OK for TP, replace by NLTK list if needed)
    return {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "was", "were", "be", "this", "that", "it", "as", "by"
    }


# =========================================================
# Synonyms (origin)
# =========================================================

def load_origin_synonyms(path: str) -> Dict[str, Set[str]]:
    raw = load_json(path)
    return {k.lower(): set(v.lower() for v in vals) for k, vals in raw.items()}


def expand_tokens_with_synonyms(tokens: List[str], synonyms: Dict[str, Set[str]]) -> List[str]:
    expanded = list(tokens)
    for t in tokens:
        if t in synonyms:
            for syn in synonyms[t]:
                expanded.extend(tokenize(syn))
    return expanded


# =========================================================
# URL canonicalization (dedup variants)
# =========================================================

def canonicalize_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


# =========================================================
# Doc store (KEY = URL)
# =========================================================

@dataclass
class DocRecord:
    doc_id: str
    url: str
    title: str
    description: str
    origin: str
    brand: str
    avg_rating: float
    review_count: int


def extract_origin(doc: dict) -> str:
    feats = doc.get("product_features") or {}
    norm = {str(k).lower(): v for k, v in feats.items()}
    return str(norm.get("made in") or norm.get("made_in") or norm.get("origin") or "")


def extract_brand(doc: dict) -> str:
    feats = doc.get("product_features") or {}
    norm = {str(k).lower(): v for k, v in feats.items()}
    return str(norm.get("brand") or "")


def compute_reviews(doc: dict) -> Tuple[float, int]:
    reviews = doc.get("product_reviews") or []
    if not reviews:
        return 0.0, 0
    ratings = [r.get("rating", 0) for r in reviews if isinstance(r, dict)]
    ratings = [x for x in ratings if isinstance(x, (int, float))]
    return (sum(ratings) / len(ratings), len(reviews)) if ratings else (0.0, len(reviews))


def build_doc_store(products: List[dict]) -> Dict[str, DocRecord]:
    """
    Build a doc store keyed by URL. Also tries to inherit origin/brand
    from the canonical URL (without query params) when missing.
    """
    store: Dict[str, DocRecord] = {}

    # 1) First pass: create records
    for d in products:
        url = d.get("url", "")
        if not url:
            continue

        avg_rating, review_count = compute_reviews(d)
        store[url] = DocRecord(
            doc_id=url,
            url=url,
            title=d.get("title", ""),
            description=d.get("description", "") or "",
            origin=extract_origin(d),
            brand=extract_brand(d),
            avg_rating=avg_rating,
            review_count=review_count,
        )

    # 2) Second pass: inherit missing origin/brand from canonical url
    for url, rec in list(store.items()):
        canon = canonicalize_url(url)
        if canon == url:
            continue
        parent = store.get(canon)
        if parent is None:
            continue

        if not rec.origin and parent.origin:
            rec.origin = parent.origin
        if not rec.brand and parent.brand:
            rec.brand = parent.brand

    return store


# =========================================================
# Index helpers
# =========================================================

def get_postings(index: dict, token: str) -> Dict[str, Any]:
    postings = index.get(token)
    if isinstance(postings, dict):
        return postings
    if isinstance(postings, list):
        return {str(p["doc_id"]): p for p in postings if isinstance(p, dict) and "doc_id" in p}
    return {}


def posting_tf(posting: Any) -> int:
    if posting is None:
        return 0
    if isinstance(posting, int):
        return posting
    if isinstance(posting, list):
        return len(posting)
    if isinstance(posting, dict):
        if "tf" in posting and isinstance(posting["tf"], int):
            return posting["tf"]
        if "pos" in posting and isinstance(posting["pos"], list):
            return len(posting["pos"])
    return 0


# =========================================================
# BM25 stats
# =========================================================

@dataclass
class CorpusStats:
    N: int
    avgdl: float
    dl: Dict[str, int]


def build_stats(doc_store: Dict[str, DocRecord], field: str) -> CorpusStats:
    dl = {k: len(tokenize(getattr(v, field))) for k, v in doc_store.items()}
    avgdl = sum(dl.values()) / len(dl) if dl else 0.0
    return CorpusStats(len(dl), avgdl, dl)


# =========================================================
# Filtering
# =========================================================

def filter_any(tokens: List[str], indexes: List[dict]) -> Set[str]:
    return {doc_id for t in tokens for idx in indexes for doc_id in get_postings(idx, t).keys()}


def filter_all(tokens: List[str], indexes: List[dict]) -> Set[str]:
    if not tokens:
        return set()
    sets = [{doc_id for idx in indexes for doc_id in get_postings(idx, t).keys()} for t in tokens]
    sets.sort(key=len)
    res = sets[0]
    for s in sets[1:]:
        res &= s
        if not res:
            break
    return res


# =========================================================
# Scoring
# =========================================================

def bm25(tf: int, df: int, dl: int, avgdl: float, N: int, k1: float = 1.2, b: float = 0.75) -> float:
    if tf == 0 or df == 0 or dl == 0 or avgdl == 0 or N == 0:
        return 0.0
    idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
    return idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))


def score_bm25(doc_id: str, tokens: List[str], index: dict, stats: CorpusStats) -> float:
    total = 0.0
    dl = stats.dl.get(doc_id, 0)
    for t in tokens:
        postings = get_postings(index, t)
        df = len(postings)
        tf = posting_tf(postings.get(doc_id))
        total += bm25(tf, df, dl, stats.avgdl, stats.N)
    return total


# =========================================================
# Search
# =========================================================

def search(
    query: str,
    doc_store: Dict[str, DocRecord],
    stopwords: Set[str],
    synonyms: Dict[str, Set[str]],
    title_idx: dict,
    desc_idx: dict,
    origin_idx: dict,
    brand_idx: dict,
    top_k: int = 10
) -> dict:
    tokens = tokenize(query)
    tokens = expand_tokens_with_synonyms(tokens, synonyms)
    tokens_ns = remove_stopwords(tokens, stopwords)

    candidates = (
        filter_all(tokens_ns, [title_idx, desc_idx, origin_idx, brand_idx])
        or filter_any(tokens, [title_idx, desc_idx, origin_idx, brand_idx])
    )

    stats = {
        "title": build_stats(doc_store, "title"),
        "description": build_stats(doc_store, "description"),
        "origin": build_stats(doc_store, "origin"),
        "brand": build_stats(doc_store, "brand"),
    }

    scored: List[Tuple[str, float]] = []
    for doc_id in candidates:
        doc = doc_store.get(doc_id)
        if not doc:
            continue

        score = (
            2.2 * score_bm25(doc_id, tokens, title_idx, stats["title"])
            + 1.0 * score_bm25(doc_id, tokens, desc_idx, stats["description"])
            + 0.8 * score_bm25(doc_id, tokens, origin_idx, stats["origin"])
            + 0.9 * score_bm25(doc_id, tokens, brand_idx, stats["brand"])
        )

        # Exact match bonus in title
        if normalize_text(query) in normalize_text(doc.title):
            score += 1.2

        # Origin bonus if query token appears in origin field
        if any(t in tokenize(doc.origin) for t in tokens):
            score += 0.8

        # Reviews signals
        score += 0.7 * (doc.avg_rating / 5.0)
        score += 0.2 * math.log(1 + doc.review_count)

        scored.append((doc_id, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Deduplicate variants by canonical URL (remove query params)
    dedup: Dict[str, Tuple[str, float]] = {}
    for doc_id, score in scored:
        canon = canonicalize_url(doc_id)
        if canon not in dedup:
            dedup[canon] = (doc_id, score)

    results = []
    for doc_id, score in list(dedup.values())[:top_k]:
        d = doc_store[doc_id]
        results.append({
            "title": d.title,
            "url": d.url,
            "description": d.description,
            "score": score,
            "metadata": {
                "origin": d.origin,
                "brand": d.brand,
                "avg_rating": d.avg_rating,
                "review_count": d.review_count,
            },
        })

    return {
        "query": query,
        "total_documents": len(doc_store),
        "documents_filtered": len(candidates),
        "results_returned": len(results),
        "documents": results,
    }


# =========================================================
# Main
# =========================================================

def main():
    import sys

    if len(sys.argv) < 2:
        print('Usage: python search_engine.py "your query here"')
        raise SystemExit(1)

    query = sys.argv[1]

    base = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(base, "input")

    title_idx = load_json(os.path.join(input_dir, "title_index.json"))
    desc_idx = load_json(os.path.join(input_dir, "description_index.json"))
    origin_idx = load_json(os.path.join(input_dir, "origin_index.json"))
    brand_idx = load_json(os.path.join(input_dir, "brand_index.json"))
    synonyms = load_origin_synonyms(os.path.join(input_dir, "origin_synonyms.json"))

    products = load_jsonl(os.path.join(input_dir, "products.jsonl"))
    doc_store = build_doc_store(products)

    stopwords = get_default_stopwords()

    res = search(
        query=query,
        doc_store=doc_store,
        stopwords=stopwords,
        synonyms=synonyms,
        title_idx=title_idx,
        desc_idx=desc_idx,
        origin_idx=origin_idx,
        brand_idx=brand_idx,
        top_k=10
    )

    # Print to console
    print(json.dumps(res, indent=2, ensure_ascii=False))

    # Save to output file (TP requirement)
    output_dir = ensure_output_dir(base)
    output_path = os.path.join(output_dir, "search_results.json")
    save_results_to_json(output_path, res)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
