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
    """
    Nettoie une URL en supprimant uniquement la partie fragment (#...),
    tout en conservant les paramètres de requête (?page=..., ?category=...).

    Cette fonction est utilisée pour suivre correctement les pages de
    pagination et les variantes de produits.
    """
    clean, _ = urldefrag(url)
    return clean.strip()


def canonicalize_url(url: str) -> str:
    """
    Transforme une URL en une forme canonique afin d’éviter de crawler
    plusieurs fois la même page.

    Les actions effectuées sont :
    - suppression du fragment (#...)
    - conservation des paramètres de requête
    - tri des paramètres de requête pour avoir un ordre stable

    Deux URLs logiquement identiques auront ainsi la même représentation.
    """
    clean, _ = urldefrag(url)
    p = urlparse(clean)

    query_pairs = parse_qsl(p.query, keep_blank_values=True)
    query_pairs.sort()
    new_query = urlencode(query_pairs)

    return f"{p.scheme}://{p.netloc}{p.path}" + (f"?{new_query}" if new_query else "")


def get_domain(url: str) -> str:
    """
    Extrait le nom de domaine (netloc) d’une URL.

    Cette information est utilisée pour déterminer si un lien est interne
    ou externe au site de départ.
    """
    return urlparse(url).netloc


def is_internal(url: str, base_domain: str) -> bool:
    """
    Indique si une URL appartient au même domaine que l’URL de départ.

    Seuls les liens internes sont ajoutés à la file d’attente du crawler.
    """
    return urlparse(url).netloc == base_domain


def is_valid_href(href: str) -> bool:
    """
    Vérifie qu’un lien est exploitable par le crawler.

    Les liens vides ou utilisant des protocoles non HTTP
    (mailto, tel, javascript) sont ignorés.
    """
    if not href:
        return False
    h = href.strip().lower()
    return not h.startswith(("mailto:", "tel:", "javascript:"))

# ----------------------------
# robots.txt
# ----------------------------

def build_robot_parser(start_url: str) -> robotparser.RobotFileParser:
    """
    Initialise un RobotFileParser à partir de l’URL de départ.

    La fonction construit automatiquement l’URL du fichier robots.txt
    du site (ex : https://site.com/robots.txt) et le charge afin de
    connaître les règles d’exploration autorisées pour le crawler.
    """
    parsed = urlparse(start_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        # En cas d’erreur (robots.txt inaccessible), on continue le crawl
        pass
    return rp


def can_fetch(rp: robotparser.RobotFileParser, user_agent: str, url: str) -> bool:
    """
    Vérifie si le crawler est autorisé à accéder à une URL donnée
    selon les règles définies dans le fichier robots.txt.

    En cas de problème lors de la vérification, la fonction autorise
    l’accès par défaut afin de ne pas bloquer le TP.
    """
    try:
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True

# ----------------------------
# Politeness + HTTP
# ----------------------------

def polite_wait(last_request_time: float, delay_s: float) -> float:
    """
    Implémente une règle de politesse entre deux requêtes HTTP.

    La fonction s’assure qu’un délai minimal (delay_s) est respecté
    entre deux accès au serveur afin de ne pas le surcharger.
    Elle retourne le nouveau temps de la dernière requête effectuée.
    """
    now = time.time()
    elapsed = now - last_request_time
    if elapsed < delay_s:
        time.sleep(delay_s - elapsed)
    return time.time()


def fetch_html(
    url: str,
    user_agent: str,
    last_request_time: float,
    delay_s: float
) -> tuple[str | None, float]:
    """
    Télécharge le contenu HTML d’une page en respectant les règles de politesse.

    Étapes réalisées :
    - attente si nécessaire avant la requête (polite_wait)
    - envoi d’une requête HTTP avec un User-Agent explicite
    - vérification du type de contenu (text/html uniquement)
    - gestion des erreurs HTTP et réseau

    La fonction retourne le HTML de la page (ou None en cas d’échec)
    ainsi que le temps de la requête pour la prochaine itération.
    """
    last_request_time = polite_wait(last_request_time, delay_s)
    try:
        req = Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html"
            }
        )
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
    """
    Extrait le titre principal de la page.

    La fonction privilégie le contenu de la balise <h1>, qui correspond
    généralement au nom du produit sur les pages produit.
    En l’absence de <h1>, elle utilise le contenu de la balise <title>.
    """
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
    Extrait une description textuelle de la page.

    La fonction recherche le premier paragraphe (<p>) contenant un texte
    suffisamment long dans les balises <main>, <article> ou <body>.
    Les pages de listing peuvent ne pas contenir de description pertinente
    et retourner une chaîne vide.
    """
    container = soup.find("main") or soup.find("article") or soup.body
    if not container:
        return ""

    # Suppression des zones non pertinentes
    for tag in container.find_all(["nav", "header", "footer", "aside"]):
        tag.decompose()

    # Sélection du premier paragraphe significatif
    for p in container.find_all("p"):
        text = p.get_text(" ", strip=True)
        if text and len(text) >= 40:
            return text[:max_len]

    return ""


def extract_links(soup: BeautifulSoup, current_url: str) -> list[str]:
    """
    Extrait tous les liens présents dans le corps de la page.

    Les liens sont :
    - convertis en URLs absolues
    - nettoyés des fragments (#...)
    - conservés avec leurs paramètres de requête

    La fonction retourne la liste complète des liens découverts,
    internes et externes.
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
    """
    Extraction des caractéristiques du produit.

    Cette fonction est volontairement simplifiée dans le cadre du TP
    et retourne un dictionnaire vide pour toutes les pages.
    """
    return {}


def extract_product_reviews(soup: BeautifulSoup) -> list[dict]:
    """
    Extraction des avis clients.

    Les avis ne sont pas exploités dans ce TP, la fonction retourne donc
    systématiquement une liste vide.
    """
    return []


# ----------------------------
# Crawling logic
# ----------------------------

def compute_priority(url: str) -> int:
    """
    Attribue une priorité à une URL.

    Plus la valeur retournée est faible, plus l’URL est visitée tôt.
    Ici, on priorise les URLs contenant le token 'product', car ce sont
    généralement des pages produit (objectif du crawler).
    """
    return 0 if "product" in url.lower() else 10


def crawl(start_url: str, max_pages: int, output_path: str, delay_s: float = 0.5) -> None:
    """
    Lance le processus de crawling à partir d'une URL de départ.

    Fonctionnement global :
    - on initialise une file de priorité (heap) des URLs à visiter (frontier)
    - on respecte robots.txt (can_fetch)
    - on applique une règle de politesse (délai entre requêtes)
    - on extrait les informations utiles (titre, description, liens)
    - on écrit chaque page crawlée dans un fichier NDJSON (1 objet JSON par ligne)
    - on s’arrête après avoir visité max_pages pages

    Remarque :
    - seen et visited stockent des URLs canonisées afin d’éviter les doublons
      dus à l’ordre des paramètres dans la query (?page=...&category=...).
    """
    user_agent = "TP1-WebCrawler/1.0 (educational)"

    start_url = normalize_url_keep_query(start_url)
    base_domain = get_domain(start_url)
    rp = build_robot_parser(start_url)

    # frontier contient des tuples (priorité, ordre d’insertion, url)
    frontier: list[tuple[int, int, str]] = []
    seen: set[str] = set()      # URLs déjà ajoutées à la file (forme canonique)
    visited: set[str] = set()   # URLs déjà visitées (forme canonique)

    # On ajoute l'URL de départ à la file
    order = 0
    heapq.heappush(frontier, (compute_priority(start_url), order, start_url))
    seen.add(canonicalize_url(start_url))
    order += 1

    # Dernière requête (pour appliquer la politesse)
    last_request_time = 0.0

    # Sortie au format NDJSON : 1 objet JSON par ligne
    with open(output_path, "w", encoding="utf-8") as f_out:
        written = 0

        # On continue tant qu'il reste des URLs et qu'on n'a pas atteint max_pages
        while frontier and written < max_pages:
            _prio, _ord, url = heapq.heappop(frontier)

            # Évite de revisiter la même page
            canon = canonicalize_url(url)
            if canon in visited:
                continue
            visited.add(canon)

            # Respect robots.txt
            if not can_fetch(rp, user_agent, url):
                continue

            # Téléchargement HTML (avec politesse)
            html, last_request_time = fetch_html(url, user_agent, last_request_time, delay_s)
            if html is None:
                continue

            soup = BeautifulSoup(html, "html.parser")

            # Extraction des champs demandés
            title = extract_title(soup)
            description = extract_description(soup)
            product_features = extract_product_features(soup)
            product_reviews = extract_product_reviews(soup)
            links = extract_links(soup, current_url=url)

            # Objet final sauvegardé pour cette page
            obj = {
                "url": url,
                "title": title,
                "description": description,
                "product_features": product_features,
                "links": links,
                "product_reviews": product_reviews
            }

            # Écriture d'une ligne JSON dans le fichier
            f_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
            written += 1

            # Ajout des nouveaux liens internes dans la file d'attente
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
    """
    Lit les arguments en ligne de commande.

    Format attendu :
    - argv[1] : URL de départ
    - argv[2] : nombre maximum de pages à visiter
    - argv[3] : (optionnel) nom du fichier de sortie, par défaut 'output.json'
    """
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
    """
    Point d’entrée du script.
    Vérifie les arguments puis lance le crawl.
    """
    try:
        start_url, max_pages, output_path = read_args(argv)
    except ValueError as err:
        print(err)
        return 1

    crawl(start_url=start_url, max_pages=max_pages, output_path=output_path, delay_s=0.5)
    return 0


if __name__ == "__main__":
    # Permet d’exécuter le script directement depuis la ligne de commande
    raise SystemExit(main(sys.argv))
