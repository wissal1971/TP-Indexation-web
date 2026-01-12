#!/usr/bin/env python3
import sys
import json
import time
import heapq
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse, urljoin, urldefrag, parse_qsl, urlencode
from urllib import robotparser

from bs4 import BeautifulSoup


# ----------------------------
# URL helpers
# ----------------------------

def normalize_url_keep_query(url: str) -> str:
    """Remove fragments (#...) but keep query params (?page=..., ?category=...)."""
    clean, _ = urldefrag(url)
    return clean.strip()


def canonicalize_url(url: str) -> str:
    """
    Normalise une URL pour éviter les doublons:
    - supprime #fragment
    - garde query mais trie les paramètres
    """
    clean, _ = urldefrag(url)
    p = urlparse(clean)

    query_pairs = parse_qsl(p.query, keep_blank_values=True)
    query_pairs.sort()
    new_query = urlencode(query_pairs)

    return f"{p.scheme}://{p.netloc}{p.path}" + (f"?{new_query}" if new_query else "")


def get_domain(url: str) -> str:
    return urlparse(url).netloc


def is_internal(url: str, base_domain: str) -> bool:
    return urlparse(url).netloc == base_domain


def is_valid_href(href: str) -> bool:
    if not href:
        return False
    h = href.strip().lower()
    return not h.startswith(("mailto:", "tel:", "javascript:"))


# ----------------------------
# robots.txt
# ----------------------------

def build_robot_parser(start_url: str) -> robotparser.RobotFileParser:
    parsed = urlparse(start_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        pass
    return rp


def can_fetch(rp: robotparser.RobotFileParser, user_agent: str, url: str) -> bool:
    try:
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


# ----------------------------
# Politeness + HTTP
# ----------------------------

def polite_wait(last_request_time: float, delay_s: float) -> float:
    now = time.time()
    elapsed = now - last_request_time
    if elapsed < delay_s:
        time.sleep(delay_s - elapsed)
    return time.time()


def fetch_html(url: str, user_agent: str, last_request_time: float, delay_s: float) -> tuple[str | None, float]:
    last_request_time = polite_wait(last_request_time, delay_s)
    try:
        req = Request(url, headers={"User-Agent": user_agent, "Accept": "text/html"})
        with urlopen(req, timeout=12) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "text/html" not in ctype:
                return None, time.time()
            html = resp.read().decode("utf-8", errors="replace")
            return html, time.time()
    except HTTPError as e:
        print(f"[HTTP {e.code}] {url}")
    except URLError as e:
        print(f"[URL error] {url} -> {e.reason}")
    except Exception as e:
        print(f"[Unexpected] {url} -> {e}")
    return None, time.time()


# ----------------------------
# Parsing (output fields)
# ----------------------------

def extract_title(soup: BeautifulSoup) -> str:
    """Prefer H1 (product name), fallback to <title>."""
    h1 = soup.find("h1")
    if h1:
        txt = h1.get_text(" ", strip=True)
        if txt:
            return txt
    if soup.title:
        return soup.title.get_text(strip=True)
    return ""


def extract_description(soup: BeautifulSoup, max_len: int = 2000) -> str:
    """
    First meaningful <p> in main/article/body (pages listing may return "").
    """
    container = soup.find("main") or soup.find("article") or soup.body
    if not container:
        return ""

    for tag in container.find_all(["nav", "header", "footer", "aside"]):
        tag.decompose()

    for p in container.find_all("p"):
        text = p.get_text(" ", strip=True)
        if text and len(text) >= 40:
            return text[:max_len]

    return ""


def extract_links(soup: BeautifulSoup, current_url: str) -> list[str]:
    """
    links = list of absolute URLs found in body, internal + external.
    Keep query params, remove fragments.
    """
    body = soup.body
    if not body:
        return []

    urls: list[str] = []
    for a in body.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not is_valid_href(href):
            continue
        abs_url = normalize_url_keep_query(urljoin(current_url, href))
        if abs_url:
            urls.append(abs_url)
    return urls


def extract_product_features(soup: BeautifulSoup) -> dict:
    return {}


def extract_product_reviews(soup: BeautifulSoup) -> list[dict]:
    return []


# ----------------------------
# Crawling logic
# ----------------------------

def compute_priority(url: str) -> int:
    """Lower = higher priority. Prioritize URLs containing 'product'."""
    return 0 if "product" in url.lower() else 10


def crawl(start_url: str, max_pages: int, output_path: str, delay_s: float = 0.5) -> None:
    user_agent = "TP1-WebCrawler/1.0 (educational)"

    start_url = normalize_url_keep_query(start_url)
    base_domain = get_domain(start_url)
    rp = build_robot_parser(start_url)

    frontier: list[tuple[int, int, str]] = []
    seen: set[str] = set()      # canonical urls
    visited: set[str] = set()   # canonical urls

    order = 0
    heapq.heappush(frontier, (compute_priority(start_url), order, start_url))
    seen.add(canonicalize_url(start_url))
    order += 1

    last_request_time = 0.0

    with open(output_path, "w", encoding="utf-8") as f_out:
        written = 0

        while frontier and written < max_pages:
            _prio, _ord, url = heapq.heappop(frontier)

            canon = canonicalize_url(url)
            if canon in visited:
                continue
            visited.add(canon)

            if not can_fetch(rp, user_agent, url):
                continue

            html, last_request_time = fetch_html(url, user_agent, last_request_time, delay_s)
            if html is None:
                continue

            soup = BeautifulSoup(html, "html.parser")

            title = extract_title(soup)
            description = extract_description(soup)
            product_features = extract_product_features(soup)
            product_reviews = extract_product_reviews(soup)
            links = extract_links(soup, current_url=url)

            obj = {
                "url": url,
                "title": title,
                "description": description,
                "product_features": product_features,
                "links": links,
                "product_reviews": product_reviews
            }

            f_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
            written += 1

            # enqueue internal links only
            for link_url in links:
                if not is_internal(link_url, base_domain):
                    continue

                canon_link = canonicalize_url(link_url)
                if canon_link in seen:
                    continue

                seen.add(canon_link)
                heapq.heappush(frontier, (compute_priority(link_url), order, link_url))
                order += 1

    print(f"Done. Visited: {written} pages. Output: {output_path}")


# ----------------------------
# CLI
# ----------------------------

def read_args(argv: list[str]) -> tuple[str, int, str]:
    if len(argv) < 3:
        raise ValueError(
            "Usage: python crawler.py <start_url> <max_pages> [output.json]\n"
            "Example: python crawler.py https://web-scraping.dev/products 50 output.json"
        )

    start_url = argv[1]
    try:
        max_pages = int(argv[2])
    except ValueError as e:
        raise ValueError("max_pages must be an integer (example: 50)") from e

    output_path = argv[3] if len(argv) >= 4 else "output.json"
    return start_url, max_pages, output_path


def main(argv: list[str]) -> int:
    try:
        start_url, max_pages, output_path = read_args(argv)
    except ValueError as err:
        print(err)
        return 1

    crawl(start_url=start_url, max_pages=max_pages, output_path=output_path, delay_s=0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
