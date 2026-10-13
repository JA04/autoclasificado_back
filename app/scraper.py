"""
Módulo de recolección y limpieza de datos (Scraping).

Las funciones `clean_*` y `parse_*` son puras: reciben texto o HTML y devuelven datos.
Las funciones `scrape_*` son las únicas que hacen requests.
"""
import json
import logging
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup, Tag

from app.provinces import find_province, normalize_province

logger = logging.getLogger(__name__)

MOTORDIL_BASE_URL = "https://www.motordil.com"
V6_BASE_URL = "https://v6.com.ar"
V6_LISTING_URL = "https://www.v6.com.ar/autos-usados"

# motordil.com/autos no lista autos: es un índice. Las publicaciones están en
# /autos/usados/{provincia}, paginadas con ?page=N.
MOTORDIL_PROVINCE_SLUGS = [
    "provincia-de-buenos-aires",
    "ciudad-autonoma-de-buenos-aires",
    "catamarca",
    "chaco",
    "chubut",
    "cordoba",
    "corrientes",
    "entre-rios",
    "formosa",
    "jujuy",
    "la-pampa",
    "la-rioja",
    "mendoza",
    "misiones",
    "neuquen",
    "rio-negro",
    "salta",
    "san-juan",
    "san-luis",
    "santa-cruz",
    "santa-fe",
    "santiago-del-estero",
    "tierra-del-fuego",
    "tucuman",
]

USER_AGENT = "autoclasificado/1.0 (agregador de clasificados de autos usados)"

PRICE_RE = re.compile(r"(?:USD|U\$S|US\$|ARS|\$)\s*[\d.,]+", re.IGNORECASE)
MILEAGE_RE = re.compile(r"([\d.,]+)\s*kms?\b", re.IGNORECASE)
LEADING_YEAR_RE = re.compile(r"^((?:19|20)\d{2})\b\s*(.*)$", re.DOTALL)
PAGE_COUNT_RE = re.compile(r"\d+\s+de\s+(\d+)")


# --- LIMPIEZA ---
def clean_number(text: Optional[str]) -> Optional[int]:
    """Extrae únicamente dígitos de una cadena de texto (e.g. '119.000 km' -> 119000)."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def clean_price(text: Optional[str]) -> Tuple[Optional[int], str]:
    """Detecta si el precio es USD o ARS y extrae el valor entero."""
    if not text:
        return None, "USD"
    upper = text.upper()
    if any(prefix in upper for prefix in ("USD", "U$S", "US$")):
        currency = "USD"
    elif "ARS" in upper or "$" in upper:
        currency = "ARS"
    else:
        currency = "USD"
    return clean_number(text), currency


def split_year(text: str) -> Tuple[Optional[int], str]:
    """Separa el año con el que empieza un título ('2014 Ford Focus' -> (2014, 'Ford Focus'))."""
    text = " ".join(text.split())
    match = LEADING_YEAR_RE.match(text)
    if not match:
        return None, text
    return int(match.group(1)), match.group(2)


def _text(element: Tag) -> str:
    return " ".join(element.get_text(" ", strip=True).split())


# --- MOTORDIL ---
def parse_motordil_card(card: Tag, province: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parsea el <a href="/auto/..."> de una publicación de motordil.com."""
    href = card.get("href")
    heading = card.find("h4")
    if not href or heading is None:
        return None
    year, title = split_year(_text(heading))

    # El subtítulo viene como '1.4 TSI • 8,000 kms • Manual' (la versión es opcional)
    mileage = None
    details = card.find("h5")
    if details is not None:
        parts = [part.strip() for part in _text(details).split("•")]
        for index, part in enumerate(parts):
            match = MILEAGE_RE.search(part)
            if match:
                mileage = clean_number(match.group(1))
                title = " ".join([title, *parts[:index]]).strip()
                break

    # Si el auto bajó de precio, el valor anterior aparece tachado: lo salteamos
    price, currency = None, "USD"
    for paragraph in card.find_all("p"):
        if "line-through" in (paragraph.get("class") or []):
            continue
        match = PRICE_RE.search(_text(paragraph))
        if match:
            price, currency = clean_price(match.group(0))
            break

    if not title:
        return None
    return {
        "title": title,
        "year": year,
        "mileage": mileage,
        "price": price,
        "currency": currency,
        "province": province,
        "original_url": f"{MOTORDIL_BASE_URL}{href}",
        "source_site": "motordil",
    }


def parse_motordil_listing(html: str, province: Optional[str]) -> List[Dict[str, Any]]:
    """Parsea una página de /autos/usados/{provincia} de motordil.com."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("a", href=re.compile(r"^/auto/"))
    items = (parse_motordil_card(card, province) for card in cards)
    return [item for item in items if item is not None]


def parse_motordil_page_count(html: str) -> int:
    """Lee el total de páginas del paginador ('1 de 8' -> 8). Si no hay paginador, 1."""
    soup = BeautifulSoup(html, "html.parser")
    for paragraph in soup.find_all("p"):
        match = PAGE_COUNT_RE.fullmatch(_text(paragraph))
        if match:
            return int(match.group(1))
    return 1


# --- V6 ---
def parse_v6_item(item: Tag) -> Optional[Dict[str, Any]]:
    """Parsea un <li> del listado de v6.com.ar.

    Formato: <a href="/auto/...">2014 Ford Focus SE</a>
             <span class="seo-index-meta">119.000 km · USD 11.000 · Olivos</span>
    """
    link = item.find("a", href=True)
    if link is None:
        return None
    year, title = split_year(_text(link))
    if not title:
        return None

    mileage, price, currency, location = None, None, "USD", None
    meta = item.find("span", class_="seo-index-meta")
    for part in (_text(meta).split("·") if meta is not None else []):
        part = part.strip()
        mileage_match = MILEAGE_RE.search(part)
        price_match = PRICE_RE.search(part)
        if mileage_match:
            mileage = clean_number(mileage_match.group(1))
        elif price_match:
            price, currency = clean_price(price_match.group(0))
        elif part:
            location = part

    href = link["href"]
    return {
        "title": title,
        "year": year,
        "mileage": mileage,
        "price": price,
        "currency": currency,
        # El listado muestra la localidad; solo sirve si coincide con una provincia
        "province": find_province(location),
        "original_url": f"{V6_BASE_URL}{href}" if href.startswith("/") else href,
        "source_site": "v6",
    }


def parse_v6_listing(html: str) -> List[Dict[str, Any]]:
    """Parsea el listado de publicaciones de v6.com.ar/autos-usados."""
    soup = BeautifulSoup(html, "html.parser")
    items = (parse_v6_item(item) for item in soup.select("ul.seo-index-list li"))
    return [item for item in items if item is not None]


def parse_v6_detail_province(html: str) -> Optional[str]:
    """Obtiene la provincia de la página de una publicación de v6 (datos schema.org)."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if data.get("@type") != "Car":
                continue
            region = data["availableAtOrFrom"]["address"]["addressRegion"]
        except (ValueError, AttributeError, KeyError, TypeError):
            continue
        return normalize_province(region)
    return None


# --- REQUESTS ---
def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    )


def fetch_html(client: httpx.Client, url: str, delay: float = 0.0) -> Optional[str]:
    """Descarga una página. Si falla, lo registra y devuelve None."""
    if delay:
        time.sleep(delay)
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as error:
        logger.warning("No se pudo descargar %s: %s", url, error)
        return None
    return response.text


def scrape_motordil(
    client: httpx.Client,
    province_slugs: Optional[Iterable[str]] = None,
    max_pages: int = 1,
    delay: float = 1.0,
) -> List[Dict[str, Any]]:
    """Recorre las páginas por provincia de motordil.com (hasta `max_pages` por provincia)."""
    items: List[Dict[str, Any]] = []
    for slug in province_slugs or MOTORDIL_PROVINCE_SLUGS:
        province = normalize_province(slug.replace("-", " "))
        url = f"{MOTORDIL_BASE_URL}/autos/usados/{slug}"
        total_pages = 1
        page = 1
        while page <= min(total_pages, max_pages):
            html = fetch_html(client, url if page == 1 else f"{url}?page={page}", delay)
            if html is None:
                break
            if page == 1:
                total_pages = parse_motordil_page_count(html)
            found = parse_motordil_listing(html, province)
            logger.info("motordil %s página %d/%d: %d publicaciones", slug, page, total_pages, len(found))
            items.extend(found)
            page += 1
    return items


def scrape_v6(client: httpx.Client, delay: float = 1.0) -> List[Dict[str, Any]]:
    """Lee el listado de v6.com.ar y completa la provincia desde cada publicación."""
    html = fetch_html(client, V6_LISTING_URL)
    if html is None:
        return []
    items = parse_v6_listing(html)
    logger.info("v6 listado: %d publicaciones", len(items))
    for item in items:
        if item["province"] is None:
            detail = fetch_html(client, item["original_url"], delay)
            if detail is not None:
                item["province"] = parse_v6_detail_province(detail)
    return items
