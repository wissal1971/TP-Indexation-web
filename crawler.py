#!/usr/bin/env python3
import sys
import json
import time
import heapq
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse, urljoin, urldefrag
from urllib import robotparser

from bs4 import BeautifulSoup


# ----------------------------
# Helpers URL
# ----------------------------

def normalize_url(url: str) -> str:
    """Remove #fragments and strip."""
    clean, _ = urldefrag(url)
    return clean.strip()


def get_domain(url: str) -> str:
    return urlparse(url).netloc


def is_internal(url: str, base_domain: str) -> bool:
    return urlparse(url).netloc == base_domain


def is_valid_href(href: str) -> bool:
    if not href:
        return False
    href_l = href.strip().lower()
    if href_l.startswith(("mailto:", "tel:", "javascript:")):
        return False
    return True


# ----------------------------
# Robots.txt
# ----------------------------

def build_robot_parser(start_url: str) -> robotparser.RobotFileParser:
    parsed = urlparse(start_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        # If robots can't be read, we keep rp but we'll treat can_fetch safely
        pass
    return rp


def can_fetch(rp: robotparser.RobotFileParser, user_agent: str, url: str) -> bool:
    try:
        return rp.can_fetch(user_agent, url)
    except Exception:
        # If parser fails, be permissive for TP (or set False to be strict)
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
    """
    Fetch HTML with politeness.
    Returns (html_or_none, new_last_request_time).
    """
    last_request_time = polite_wait(last_request_time, delay_s)

    try:
        req = Request(url, headers={"User-Agent": user_agent, "Accept": "text/html"})
        with urlopen(req, timeout=12) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
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
# Parsing
# ----------------------------

def extract_title(soup: BeautifulSoup) -> str:
    if soup.title:
        return soup.title.get_text(strip=True)
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else ""


def extract_first_paragraph(soup: BeautifulSoup, max_len: int = 250) -> str:
    """
    Extract a short, relevant 'first paragraph':
    1) first meaningful <p> in main/article/body (after removing nav/header/footer/aside)
    2) fallback: first reasonable text chunk (card-like) not too long
    """
    container = soup.find("main") or soup.find("article") or soup.body
    if not container:
        return ""

    # remove noisy sections
    for tag in container.find_all(["nav", "header", "footer", "aside"]):
        tag.decompose()

    # 1) first significant <p>
    for p in container.find_all("p"):
        text = p.get_text(" ", strip=True)
        if text and len(text) >= 40:
            return text[:max_len]

    # 2) fallback: first chunk that looks like content (avoid menus)
    for tag in container.find_all(["article", "section", "div"], limit=200):
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        low = text.lower()
        if "login" in low and "password" in low:
            continue
        # avoid taking the whole page: we want something bounded
        if 80 <= len(text) <= 500:
            return text[:max_len]

    return ""


def extract_internal_links(soup: BeautifulSoup, current_url: str, base_domain: str) -> list[dict]:
    """
    Liens pertinents = liens vers les pages produit (/product/...)
    """
    body = soup.body
    if not body:
        return []

    seen_urls = set()
    links: list[dict] = []

    for a in body.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not is_valid_href(href):
            continue

        abs_url = normalize_url(urljoin(current_url, href))
        if not abs_url or not is_internal(abs_url, base_domain):
            continue

        # ✅ uniquement les pages produit
        path = urlparse(abs_url).path.lower()
        if not path.startswith("/product/"):
            continue

        # ✅ dédoublonnage
        if abs_url in seen_urls:
            continue
        seen_urls.add(abs_url)

        links.append({
            "url": abs_url,
            "anchor_text": a.get_text(" ", strip=True),
            "source_url": current_url
        })

    return links


# ----------------------------
# Priority crawling
# ----------------------------

def compute_priority(url: str) -> int:
    """
    Lower = higher priority.
    Prioritize URLs containing 'product'.
    """
    return 0 if "product" in url.lower() else 10


def crawl(start_url: str, max_pages: int, output_path: str, delay_s: float = 0.5) -> None:
    user_agent = "TP1-WebCrawler/1.0 (educational)"
    start_url = normalize_url(start_url)

    base_domain = get_domain(start_url)
    rp = build_robot_parser(start_url)

    # frontier items: (priority, insertion_order, url)
    frontier: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    visited: set[str] = set()

    insertion = 0
    heapq.heappush(frontier, (compute_priority(start_url), insertion, start_url))
    seen.add(start_url)
    insertion += 1

    results: list[dict] = []

    last_request_time = 0.0

    while frontier and len(visited) < max_pages:
        _prio, _order, url = heapq.heappop(frontier)
        if url in visited:
            continue
        visited.add(url)

        # robots check
        if not can_fetch(rp, user_agent, url):
            # skip politely if disallowed
            continue

        html, last_request_time = fetch_html(url, user_agent, last_request_time, delay_s)
        if html is None:
            continue

        soup = BeautifulSoup(html, "html.parser")

        title = extract_title(soup)
        first_paragraph = extract_first_paragraph(soup)
        links = extract_internal_links(soup, current_url=url, base_domain=base_domain)

        results.append({
            "url": url,
            "title": title,
            "first_paragraph": first_paragraph,
            "links": links
        })

        # enqueue new links
        for link in links:
            next_url = link["url"]
            if next_url in seen:
                continue
            seen.add(next_url)
            heapq.heappush(frontier, (compute_priority(next_url), insertion, next_url))
            insertion += 1

    # write JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Done. Visited: {len(results)} pages. Output: {output_path}")


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

    output_path = argv[3] if len(argv) >= 4 else "crawl_results.json"
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
