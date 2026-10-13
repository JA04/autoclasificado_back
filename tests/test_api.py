"""
Pruebas de endpoints de FastAPI y filtros con Pytest.
"""
import pytest

from app.models import Car
from app.provinces import find_province, normalize_province


# --- TESTS DE NORMALIZACIÓN DE PROVINCIAS ---
@pytest.mark.parametrize("text, expected", [
    ("Córdoba", "Córdoba"),
    ("cordoba", "Córdoba"),
    ("CÓRDOBA", "Córdoba"),
    ("  Cba. ", "Córdoba"),
    ("Bs. As.", "Buenos Aires"),
    ("Bs.As.", "Buenos Aires"),
    ("buenos aires", "Buenos Aires"),
    ("CABA", "Capital Federal"),
    ("C.A.B.A.", "Capital Federal"),
    ("Ciudad Autónoma de Buenos Aires", "Capital Federal"),
    ("santa fe", "Santa Fe"),
    ("Neuquen", "Neuquén"),
    ("Entre Rios", "Entre Ríos"),
    ("Narnia", None),
    ("", None),
    (None, None),
])
def test_normalize_province(text, expected):
    assert normalize_province(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Ford Focus SE 2014 119.000 km USD 11.000 Cordoba", "Córdoba"),
    ("Fiat Palio - La Plata, Bs. As.", "Buenos Aires"),
    ("Toyota Hilux en Ciudad de Buenos Aires", "Capital Federal"),
    ("Renault Sandero, San Rafael (MENDOZA)", "Mendoza"),
    ("Volkswagen Gol Trend sin ubicación", None),
    (None, None),
])
def test_find_province(text, expected):
    assert find_province(text) == expected


# --- TESTS DE ENDPOINTS Y FILTRO DE PROVINCIA ---
def make_car(n, **overrides):
    fields = {
        "title": f"Auto {n}",
        "year": 2014,
        "mileage": 100000,
        "price": 10000,
        "currency": "USD",
        "province": "Buenos Aires",
        "original_url": f"https://v6.com.ar/autos/{n}",
        "source_site": "v6",
    }
    fields.update(overrides)
    return Car(**fields)


def test_get_cars_filtered_by_province(client, db_session):
    db_session.add_all([
        make_car(1, title="Ford Focus SE", province="Buenos Aires"),
        make_car(2, title="Fiat Palio Fire", province="Córdoba", source_site="motordil"),
        make_car(3, title="Fiat Cronos", province=None),
    ])
    db_session.commit()

    # 1. Consultar todos los autos
    response = client.get("/cars")
    assert response.status_code == 200
    assert len(response.json()) == 3

    # 2. Filtrar por provincia, con o sin acento y en cualquier capitalización
    for value in ["Córdoba", "Cordoba", "cordoba", "CÓRDOBA", "cba"]:
        response_cba = client.get("/cars", params={"province": value})
        assert response_cba.status_code == 200
        data = response_cba.json()
        assert [car["title"] for car in data] == ["Fiat Palio Fire"], value
        assert data[0]["province"] == "Córdoba"

    # 3. Las abreviaturas también llegan a su provincia
    response_ba = client.get("/cars", params={"province": "Bs. As."})
    assert [car["title"] for car in response_ba.json()] == ["Ford Focus SE"]


def test_get_cars_province_substring_and_no_match(client, db_session):
    db_session.add_all([
        make_car(1, province="Santa Fe"),
        make_car(2, province="Santa Cruz"),
        make_car(3, province="Mendoza"),
    ])
    db_session.commit()

    # Un texto que no es una provincia se sigue buscando como subcadena, sin distinguir mayúsculas
    response = client.get("/cars", params={"province": "santa"})
    assert sorted(car["province"] for car in response.json()) == ["Santa Cruz", "Santa Fe"]

    response_none = client.get("/cars", params={"province": "Narnia"})
    assert response_none.status_code == 200
    assert response_none.json() == []


def test_get_cars_limit(client, db_session):
    db_session.add_all([make_car(n) for n in range(60)])
    db_session.commit()

    assert len(client.get("/cars").json()) == 50
    assert len(client.get("/cars", params={"limit": 5}).json()) == 5
    assert len(client.get("/cars", params={"limit": 100}).json()) == 60


@pytest.mark.parametrize("limit", ["0", "-1", "101", "abc"])
def test_get_cars_invalid_limit_returns_422(client, limit):
    response = client.get("/cars", params={"limit": limit})
    assert response.status_code == 422


# --- PAGINADO ---
def test_get_cars_offset_and_total_count(client, db_session):
    db_session.add_all([make_car(n) for n in range(60)])
    db_session.commit()

    first = client.get("/cars", params={"limit": 25})
    second = client.get("/cars", params={"limit": 25, "offset": 25})
    last = client.get("/cars", params={"limit": 25, "offset": 50})
    beyond = client.get("/cars", params={"limit": 25, "offset": 500})

    assert [len(r.json()) for r in (first, second, last, beyond)] == [25, 25, 10, 0]
    assert all(r.headers["x-total-count"] == "60" for r in (first, second, last, beyond))

    # Las páginas no se pisan y entre todas cubren el total
    ids = [car["id"] for r in (first, second, last) for car in r.json()]
    assert len(set(ids)) == 60
    assert ids == sorted(ids)


def test_get_cars_total_count_respects_filters(client, db_session):
    db_session.add_all([make_car(n, province="Córdoba" if n < 7 else "Mendoza") for n in range(20)])
    db_session.commit()

    response = client.get("/cars", params={"province": "cordoba", "limit": 5})
    assert len(response.json()) == 5
    assert response.headers["x-total-count"] == "7"


def test_get_cars_invalid_offset_returns_422(client):
    assert client.get("/cars", params={"offset": -1}).status_code == 422
    assert client.get("/cars", params={"offset": "abc"}).status_code == 422


# --- BÚSQUEDA POR MODELO ---
def search_titles(client, **params):
    return [car["title"] for car in client.get("/cars", params=params).json()]


def test_get_cars_search_by_title(client, db_session):
    db_session.add_all([
        make_car(1, title="Ford Focus SE Plus 2.0", province="Buenos Aires"),
        make_car(2, title="Ford Fiesta Kinetic", province="Córdoba"),
        make_car(3, title="Fiat Cronos Drive 1.3", province="Córdoba", source_site="motordil"),
        make_car(4, title="Peugeot 208 Allure 100%", province="Mendoza"),
    ])
    db_session.commit()

    assert search_titles(client, q="ford") == ["Ford Focus SE Plus 2.0", "Ford Fiesta Kinetic"]
    assert search_titles(client, q="FOCUS") == ["Ford Focus SE Plus 2.0"]
    # Varias palabras: todas tienen que estar, en cualquier orden
    assert search_titles(client, q="focus ford") == ["Ford Focus SE Plus 2.0"]
    assert search_titles(client, q="ford cronos") == []
    assert search_titles(client, q="   ") == search_titles(client)

    # Solo mira el título: ni la provincia ni el portal cuentan
    assert search_titles(client, q="Córdoba") == []
    assert search_titles(client, q="motordil") == []
    assert search_titles(client, q="v6") == []

    # Se combina con el filtro de provincia
    assert search_titles(client, q="ford", province="cordoba") == ["Ford Fiesta Kinetic"]

    # Los comodines de LIKE se buscan como texto
    assert search_titles(client, q="100%") == ["Peugeot 208 Allure 100%"]
    assert search_titles(client, q="%") == ["Peugeot 208 Allure 100%"]
    assert search_titles(client, q="_") == []

    response = client.get("/cars", params={"q": "ford", "limit": 1})
    assert response.headers["x-total-count"] == "2"


# --- CORS ---
def test_cors_allows_frontend_origin(client):
    response = client.get("/cars", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_ignores_unknown_origin(client):
    response = client.get("/cars", headers={"Origin": "https://otro-sitio.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_exposes_total_count_header(client):
    response = client.get("/cars", headers={"Origin": "http://localhost:5173"})
    assert "x-total-count" in response.headers["access-control-expose-headers"].lower()
