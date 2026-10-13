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
V6_SITEMAP_URL = "https://v6.com.ar/sitemap.xml"

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
NEXT_PUSH_RE = re.compile(r"self\.__next_f\.push\((\[.*\])\)\s*;?\s*$", re.DOTALL)
SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
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
    amount = clean_number(text)
    if any(prefix in upper for prefix in ("USD", "U$S", "US$")):
        currency = "USD"
    elif "ARS" in upper or "$" in upper:
        currency = "ARS"
    else:
        currency = infer_currency(amount)
    return amount, currency


# Cuando una publicación no aclara la moneda: nadie vende un auto a menos de
# un millón de pesos ni a más de un millón de dólares.
ARS_MIN_AMOUNT = 1_000_000


def infer_currency(amount: Optional[int]) -> str:
    """Deduce la moneda por el monto: menos de 1.000.000 es USD; desde ahí, ARS."""
    if amount is not None and amount >= ARS_MIN_AMOUNT:
        return "ARS"
    return "USD"


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
def v6_canonical_url(url: str) -> str:
    """Lleva la URL de una publicación de v6 a una forma única: /auto/{id}.

    v6 identifica la publicación por el id del final ('fiat-titano-2026-PBfN31yK4Z') e
    ignora el resto, así que el mismo auto aparece con textos distintos según la página.
    """
    uid = url.rstrip("/").rsplit("/", 1)[-1].rsplit("-", 1)[-1]
    return f"{V6_BASE_URL}/auto/{uid}"


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

    return {
        "title": title,
        "year": year,
        "mileage": mileage,
        "price": price,
        "currency": currency,
        # El listado muestra la localidad; solo sirve si coincide con una provincia
        "province": find_province(location),
        "original_url": v6_canonical_url(link["href"]),
        "source_site": "v6",
    }


def parse_v6_listing(html: str) -> List[Dict[str, Any]]:
    """Parsea el listado de publicaciones de v6.com.ar/autos-usados."""
    soup = BeautifulSoup(html, "html.parser")
    items = (parse_v6_item(item) for item in soup.select("ul.seo-index-list li"))
    return [item for item in items if item is not None]


def parse_v6_sitemap(xml: str) -> List[str]:
    """Extrae del sitemap de v6 las URLs de publicaciones (/auto/...)."""
    urls = [url for url in SITEMAP_LOC_RE.findall(xml) if "/auto/" in url]
    return list(dict.fromkeys(urls))


def _v6_structured_car(soup: BeautifulSoup) -> Dict[str, Any]:
    """Datos schema.org (ld+json) de la publicación, o {} si no están."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Car":
            return data
    return {}


def _v6_vehicle(soup: BeautifulSoup) -> Dict[str, Any]:
    """Objeto `initialVehicle` que la página de v6 le pasa a su app, o {} si no está."""
    marker = '"initialVehicle":'
    for script in soup.find_all("script"):
        match = NEXT_PUSH_RE.match((script.string or "").strip())
        if not match:
            continue
        try:
            chunk = json.loads(match.group(1))[1]
            position = chunk.index(marker) + len(marker)
            vehicle, _ = json.JSONDecoder().raw_decode(chunk, position)
        except (ValueError, IndexError, TypeError, AttributeError):
            continue
        if isinstance(vehicle, dict):
            return vehicle
    return {}


def _dig(data: Any, *keys: str) -> Any:
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _as_int(value: Any) -> Optional[int]:
    return clean_number(str(value)) if value not in (None, "") else None


def parse_v6_detail(html: str, url: str) -> Optional[Dict[str, Any]]:
    """Parsea la página de una publicación de v6.com.ar.

    Lee los datos que la página le pasa a su app (`initialVehicle`) y, si faltan, los
    de schema.org. La moneda de schema.org no es confiable (siempre dice ARS): cuando
    la publicación no trae la suya, se deduce por el monto.
    """
    soup = BeautifulSoup(html, "html.parser")
    vehicle = _v6_vehicle(soup)
    structured = _v6_structured_car(soup)
    if vehicle.get("active") is False:
        return None

    name = " ".join(str(vehicle[key]) for key in ("brand", "model", "version") if vehicle.get(key))
    year = _as_int(vehicle.get("year"))
    if not name:
        year, name = split_year(str(structured.get("name") or ""))
    if not name:
        return None

    price = _as_int(vehicle.get("price"))
    if price is None:
        price = _as_int(_dig(structured, "offers", "price"))
    mileage = _as_int(vehicle.get("kilometers"))
    if mileage is None:
        mileage = _as_int(_dig(structured, "mileageFromOdometer", "value"))
    currency = vehicle.get("currency")
    if currency not in ("USD", "ARS"):
        currency = infer_currency(price)
    province = normalize_province(vehicle.get("province")) or normalize_province(
        _dig(structured, "availableAtOrFrom", "address", "addressRegion")
    )

    return {
        "title": " ".join(name.split()),
        "year": year or _as_int(structured.get("vehicleModelDate")),
        "mileage": mileage,
        "price": price,
        "currency": currency,
        "province": province,
        "original_url": v6_canonical_url(url),
        "source_site": "v6",
    }


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


def scrape_v6(client: httpx.Client, delay: float = 1.0, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Recorre las publicaciones del sitemap de v6.com.ar (hasta `limit`, si se indica).

    Si el sitemap no está disponible, usa el listado de /autos-usados, que trae menos.
    """
    sitemap = fetch_html(client, V6_SITEMAP_URL)
    urls = parse_v6_sitemap(sitemap) if sitemap else []
    if not urls:
        logger.warning("v6: sin sitemap, se usa el listado de /autos-usados")
        return scrape_v6_listing(client, delay)

    urls = urls[:limit] if limit is not None else urls
    items: List[Dict[str, Any]] = []
    for url in urls:
        html = fetch_html(client, url, delay)
        item = parse_v6_detail(html, url) if html is not None else None
        if item is not None:
            items.append(item)
    logger.info("v6: %d publicaciones leídas de %d del sitemap", len(items), len(urls))
    return items


def scrape_v6_listing(client: httpx.Client, delay: float = 1.0) -> List[Dict[str, Any]]:
    """Lee el listado de /autos-usados y completa la provincia desde cada publicación."""
    html = fetch_html(client, V6_LISTING_URL)
    if html is None:
        return []
    items = parse_v6_listing(html)
    logger.info("v6 listado: %d publicaciones", len(items))
    for item in items:
        if item["province"] is None:
            detail_html = fetch_html(client, item["original_url"], delay)
            detail = parse_v6_detail(detail_html, item["original_url"]) if detail_html is not None else None
            if detail is not None:
                item["province"] = detail["province"]
    return items
