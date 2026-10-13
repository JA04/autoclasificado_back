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
    clean_number,
    clean_price,
    parse_motordil_listing,
    parse_motordil_page_count,
    parse_v6_detail_province,
    parse_v6_listing,
    scrape_motordil,
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
])
def test_clean_price(text, expected):
    assert clean_price(text) == expected


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
        "original_url": "https://v6.com.ar/auto/fiat-titano-ranch-4x4-2-2-at-4p-diesel-2026-PBfN31yK4Z",
        "source_site": "v6",
    }
    # 'Ciudad De Buenos Aires' es una provincia; 'Olivos' es una localidad y queda sin resolver
    assert cars[1]["province"] == "Capital Federal"
    assert cars[2]["title"] == "Ford Focus SE Plus 2.0 MT 5P"
    assert (cars[2]["price"], cars[2]["currency"]) == (11000, "USD")
    assert cars[2]["province"] is None


def test_parse_v6_detail_province():
    assert parse_v6_detail_province(fixture("v6_detail.html")) == "Buenos Aires"
    assert parse_v6_detail_province("<html><body>Vehículo no encontrado</body></html>") is None
    assert parse_v6_detail_province('<script type="application/ld+json">{no es json</script>') is None


def test_scrape_v6_fills_province_from_detail():
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url == V6_LISTING_URL:
            return httpx.Response(200, text=fixture("v6_listing.html"))
        if "ford-focus" in url:
            return httpx.Response(200, text=fixture("v6_detail.html"))
        return httpx.Response(404)

    cars = scrape_v6(mock_client(handler), delay=0)

    assert len(cars) == 9
    focus = next(car for car in cars if "ford-focus" in car["original_url"])
    assert focus["province"] == "Buenos Aires"
    # Solo se piden las publicaciones cuya provincia no salió del listado
    assert cars[0]["original_url"] not in requested
    assert sum(car["province"] is None for car in cars) == len(requested) - 2


def test_scrape_v6_returns_empty_when_listing_fails():
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
