"""
Pruebas del scraper y de la ingesta. Usan HTML real guardado en tests/fixtures/
(sin scripts ni imágenes) y nunca salen a la red.
"""
from pathlib import Path

import httpx
import pytest

from app.ingest import upsert_cars
from app.models import Car
from app.scraper import (
    V6_LISTING_URL,
    V6_SITEMAP_URL,
    clean_number,
    clean_price,
    infer_currency,
    parse_motordil_listing,
    parse_motordil_page_count,
    parse_v6_detail,
    parse_v6_listing,
    parse_v6_sitemap,
    scrape_motordil,
    v6_canonical_url,
    scrape_v6,
    split_year,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- LIMPIEZA ---
def test_clean_number():
    assert clean_number("119.000 km") == 119000
    assert clean_number("12,900 kms") == 12900
    assert clean_number("sin km") is None
    assert clean_number(None) is None


@pytest.mark.parametrize("text, expected", [
    ("USD 11.000", (11000, "USD")),
    ("usd 11.000", (11000, "USD")),
    ("U$S 11.000", (11000, "USD")),
    ("US$ 54,000", (54000, "USD")),
    ("ARS 35.000.000", (35000000, "ARS")),
    ("$ 35.000.000", (35000000, "ARS")),
    ("Consultar", (None, "USD")),
    (None, (None, "USD")),
    # Sin moneda explícita se deduce por el monto
    ("11.000", (11000, "USD")),
    ("35.000.000", (35000000, "ARS")),
])
def test_clean_price(text, expected):
    assert clean_price(text) == expected


@pytest.mark.parametrize("amount, expected", [
    (4300, "USD"),
    (999_999, "USD"),
    (1_000_000, "ARS"),
    (35_000_000, "ARS"),
    (None, "USD"),
])
def test_infer_currency(amount, expected):
    assert infer_currency(amount) == expected


@pytest.mark.parametrize("text, expected", [
    ("2014 Ford Focus SE", (2014, "Ford Focus SE")),
    ("2020  Peugeot 2008 Feline", (2020, "Peugeot 2008 Feline")),
    ("Peugeot 2008 Feline", (None, "Peugeot 2008 Feline")),
    ("Fiat 1500", (None, "Fiat 1500")),
])
def test_split_year(text, expected):
    assert split_year(text) == expected


# --- MOTORDIL ---
def test_parse_motordil_listing():
    cars = parse_motordil_listing(fixture("motordil_listing.html"), "Córdoba")

    assert len(cars) == 24
    assert len({car["original_url"] for car in cars}) == 24
    assert all(car["source_site"] == "motordil" and car["province"] == "Córdoba" for car in cars)
    assert all(car["year"] and car["mileage"] is not None for car in cars)

    # Publicación con rebaja: se toma el precio vigente (54.000), no el tachado (57.000)
    assert cars[0] == {
        "title": "BMW Serie 1",
        "year": 2024,
        "mileage": 12900,
        "price": 54000,
        "currency": "USD",
        "province": "Córdoba",
        "original_url": "https://www.motordil.com/auto/2024-bmw-serie-1-hgzh",
        "source_site": "motordil",
    }

    # Publicación con versión en el subtítulo y precio en pesos
    sandero = next(car for car in cars if car["original_url"].endswith("/2009-renault-sandero-ppyq"))
    assert sandero["title"] == "Renault Sandero 1.6 CONFORT"
    assert sandero["mileage"] == 208000
    assert (sandero["price"], sandero["currency"]) == (9900000, "ARS")


def test_parse_motordil_page_count():
    assert parse_motordil_page_count(fixture("motordil_listing.html")) == 8
    assert parse_motordil_page_count("<html><body><p>Sin resultados</p></body></html>") == 1


def test_scrape_motordil_walks_pages_per_province():
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=fixture("motordil_listing.html"))

    cars = scrape_motordil(mock_client(handler), ["cordoba", "provincia-de-buenos-aires"], max_pages=2, delay=0)

    assert requested == [
        "https://www.motordil.com/autos/usados/cordoba",
        "https://www.motordil.com/autos/usados/cordoba?page=2",
        "https://www.motordil.com/autos/usados/provincia-de-buenos-aires",
        "https://www.motordil.com/autos/usados/provincia-de-buenos-aires?page=2",
    ]
    assert len(cars) == 96
    assert {car["province"] for car in cars} == {"Córdoba", "Buenos Aires"}


def test_scrape_motordil_skips_province_that_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        if "cordoba" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, text=fixture("motordil_listing.html"))

    cars = scrape_motordil(mock_client(handler), ["cordoba", "mendoza"], delay=0)

    assert len(cars) == 24
    assert {car["province"] for car in cars} == {"Mendoza"}


# --- V6 ---
def test_parse_v6_listing():
    cars = parse_v6_listing(fixture("v6_listing.html"))

    assert len(cars) == 9
    assert all(car["source_site"] == "v6" for car in cars)
    assert cars[0] == {
        "title": "Fiat Titano Ranch 4x4 2.2 AT 4P Diesel",
        "year": 2026,
        "mileage": 11000,
        "price": 49500000,
        "currency": "ARS",
        "province": "Córdoba",
        "original_url": "https://v6.com.ar/auto/PBfN31yK4Z",
        "source_site": "v6",
    }
    # 'Ciudad De Buenos Aires' es una provincia; 'Olivos' es una localidad y queda sin resolver
    assert cars[1]["province"] == "Capital Federal"
    assert cars[2]["title"] == "Ford Focus SE Plus 2.0 MT 5P"
    assert (cars[2]["price"], cars[2]["currency"]) == (11000, "USD")
    assert cars[2]["province"] is None


@pytest.mark.parametrize("url", [
    "https://v6.com.ar/auto/fiat-titano-2026-PBfN31yK4Z",
    "https://www.v6.com.ar/auto/fiat-titano-ranch-4x4-2-2-at-4p-diesel-2026-PBfN31yK4Z/",
    "/auto/fiat-titano-ranch-4x4-2-2-at-4p-diesel-2026-PBfN31yK4Z",
    "https://v6.com.ar/auto/PBfN31yK4Z",
])
def test_v6_canonical_url(url):
    assert v6_canonical_url(url) == "https://v6.com.ar/auto/PBfN31yK4Z"


def test_parse_v6_sitemap():
    xml = """<urlset>
      <url><loc>https://v6.com.ar/autos-usados</loc></url>
      <url><loc>https://v6.com.ar/auto/fiat-titano-2026-PBfN31yK4Z</loc></url>
      <url><loc> https://v6.com.ar/auto/renault-sandero-2019-qOqNcbBjNw </loc></url>
      <url><loc>https://v6.com.ar/auto/fiat-titano-2026-PBfN31yK4Z</loc></url>
      <url><loc>https://v6.com.ar/autos/toyota</loc></url>
    </urlset>"""
    assert parse_v6_sitemap(xml) == [
        "https://v6.com.ar/auto/fiat-titano-2026-PBfN31yK4Z",
        "https://v6.com.ar/auto/renault-sandero-2019-qOqNcbBjNw",
    ]
    assert parse_v6_sitemap("") == []


def test_parse_v6_detail_with_currency():
    # La publicación declara USD aunque sus datos schema.org digan ARS
    assert parse_v6_detail(fixture("v6_detail_usd.html"), "https://v6.com.ar/auto/renault-sandero-2019-qOqNcbBjNw") == {
        "title": "Renault Sandero II RS 2.0 16v MT 5P",
        "year": 2019,
        "mileage": 58358,
        "price": 14500,
        "currency": "USD",
        "province": "Capital Federal",
        "original_url": "https://v6.com.ar/auto/qOqNcbBjNw",
        "source_site": "v6",
    }


def test_parse_v6_detail_infers_currency_when_missing():
    # Esta publicación no trae moneda: 35.000.000 se toma como pesos
    car = parse_v6_detail(fixture("v6_detail_ars.html"), "https://v6.com.ar/auto/toyota-corolla-2022-Pl5OiYf76B")
    assert car["title"] == "Toyota Corolla HEV SEG 1.8 eCVT 4P"
    assert (car["price"], car["currency"]) == (35000000, "ARS")
    assert (car["year"], car["mileage"], car["province"]) == (2022, 62500, "Buenos Aires")


def test_parse_v6_detail_falls_back_to_structured_data():
    # Fixture sin los datos de la app: quedan solo los de schema.org, que dicen
    # "ARS 11000". Por el monto se corrige a USD.
    assert parse_v6_detail(fixture("v6_detail.html"), "https://v6.com.ar/auto/ford-focus-2014-JWSQCI8RKw") == {
        "title": "Ford Focus SE Plus 2.0 MT 5P",
        "year": 2014,
        "mileage": 119000,
        "price": 11000,
        "currency": "USD",
        "province": "Buenos Aires",
        "original_url": "https://v6.com.ar/auto/JWSQCI8RKw",
        "source_site": "v6",
    }


def test_parse_v6_detail_without_data():
    assert parse_v6_detail("<html><body>Vehículo no encontrado</body></html>", "https://v6.com.ar/auto/x") is None
    assert parse_v6_detail('<script type="application/ld+json">{no es json</script>', "https://v6.com.ar/auto/x") is None


def test_scrape_v6_reads_every_sitemap_listing():
    sitemap = """<urlset>
      <url><loc>https://v6.com.ar/auto/renault-sandero-2019-qOqNcbBjNw</loc></url>
      <url><loc>https://v6.com.ar/auto/toyota-corolla-2022-Pl5OiYf76B</loc></url>
      <url><loc>https://v6.com.ar/auto/dado-de-baja-2010-XXXXXXXXXX</loc></url>
    </urlset>"""
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url == V6_SITEMAP_URL:
            return httpx.Response(200, text=sitemap)
        if "sandero" in url:
            return httpx.Response(200, text=fixture("v6_detail_usd.html"))
        if "corolla" in url:
            return httpx.Response(200, text=fixture("v6_detail_ars.html"))
        return httpx.Response(404)

    cars = scrape_v6(mock_client(handler), delay=0)

    assert [(car["title"], car["currency"]) for car in cars] == [
        ("Renault Sandero II RS 2.0 16v MT 5P", "USD"),
        ("Toyota Corolla HEV SEG 1.8 eCVT 4P", "ARS"),
    ]
    assert len(requested) == 4
    assert V6_LISTING_URL not in requested

    requested.clear()
    assert len(scrape_v6(mock_client(handler), delay=0, limit=1)) == 1
    assert len(requested) == 2


def test_scrape_v6_falls_back_to_listing_without_sitemap():
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url == V6_LISTING_URL:
            return httpx.Response(200, text=fixture("v6_listing.html"))
        if url.endswith("JWSQCI8RKw"):
            return httpx.Response(200, text=fixture("v6_detail.html"))
        return httpx.Response(404)

    cars = scrape_v6(mock_client(handler), delay=0)

    assert len(cars) == 9
    focus = next(car for car in cars if car["original_url"].endswith("JWSQCI8RKw"))
    assert focus["province"] == "Buenos Aires"
    # Solo se piden las publicaciones cuya provincia no salió del listado
    assert cars[0]["original_url"] not in requested
    assert requested[:2] == [V6_SITEMAP_URL, V6_LISTING_URL]


def test_scrape_v6_returns_empty_when_site_is_down():
    assert scrape_v6(mock_client(lambda request: httpx.Response(500)), delay=0) == []


# --- INGESTA ---
def test_upsert_cars_inserts_then_updates(db_session):
    cars = parse_v6_listing(fixture("v6_listing.html"))

    assert upsert_cars(db_session, cars) == (9, 0)
    assert db_session.query(Car).count() == 9

    # Segunda corrida: cambia un precio y la provincia de otra publicación no se pudo leer
    cars[0] = {**cars[0], "price": 48000000, "province": None}
    assert upsert_cars(db_session, cars) == (0, 9)
    assert db_session.query(Car).count() == 9

    titano = db_session.query(Car).filter(Car.original_url == cars[0]["original_url"]).one()
    assert titano.price == 48000000
    assert titano.province == "Córdoba"


def test_upsert_cars_skips_duplicates_and_incomplete_items(db_session):
    car = parse_v6_listing(fixture("v6_listing.html"))[0]
    items = [car, dict(car), {**car, "original_url": ""}, {**car, "original_url": "https://v6.com.ar/auto/x", "title": ""}]

    assert upsert_cars(db_session, items) == (1, 0)
