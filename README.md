# Autoclasificado — backend

API de un agregador de autos usados. Junta publicaciones de
[motordil.com](https://www.motordil.com), [v6.com.ar](https://www.v6.com.ar) y
[MercadoLibre](https://autos.mercadolibre.com.ar), las guarda en SQLite y las expone en un único endpoint, `GET /cars`.

Stack: FastAPI, SQLAlchemy 2, SQLite, httpx + BeautifulSoup, Pytest.

## Inicio rápido

Necesitás Python 3.10+ y `make`.

```bash
make install   # crea venv/ e instala requirements.txt
make scrape    # descarga publicaciones y las guarda en cars.db
make run       # levanta la API en http://localhost:8000
```

La documentación interactiva queda en <http://localhost:8000/docs>.

| Comando        | Qué hace                                                    |
| -------------- | ----------------------------------------------------------- |
| `make install` | Crea el entorno virtual e instala las dependencias          |
| `make run`     | Levanta la API con recarga automática (`PORT=8001` para cambiar el puerto) |
| `make scrape`  | Corre la ingesta; acepta opciones con `ARGS="..."`          |
| `make test`    | Corre los tests                                             |
| `make clean`   | Borra cachés (no toca `cars.db` ni `venv/`)                 |

## API

### `GET /cars`

| Parámetro  | Tipo   | Default | Descripción                                                |
| ---------- | ------ | ------- | ---------------------------------------------------------- |
| `province` | string | —       | Filtra por provincia, sin distinguir mayúsculas ni acentos |
| `q`        | string | —       | Busca por nombre del modelo (solo en el título)            |
| `limit`    | int    | 50      | Cantidad máxima de resultados (1 a 100)                    |
| `offset`   | int    | 0       | Cantidad de resultados a saltear, para paginar             |

La respuesta es la lista de publicaciones de esa página, ordenadas por `id`. El total
de resultados sin paginar viene en el header `X-Total-Count`. Un `limit` u `offset`
fuera de rango devuelve `422`.

```bash
curl "http://localhost:8000/cars?province=cordoba&limit=2"
```

```json
[
  {
    "title": "BMW Serie 1",
    "year": 2024,
    "mileage": 12900,
    "price": 54000,
    "currency": "USD",
    "province": "Córdoba",
    "original_url": "https://www.motordil.com/auto/2024-bmw-serie-1-hgzh",
    "source_site": "motordil",
    "id": 10,
    "created_at": "2026-10-13T08:40:12.123456"
  }
]
```

`year`, `mileage`, `price` y `province` pueden venir en `null`.

El filtro reconoce nombres completos y abreviaturas (`cordoba`, `CÓRDOBA`, `Cba.`,
`Bs. As.`, `CABA`) y los lleva al nombre canónico. Cualquier otro texto se busca
como subcadena (`santa` devuelve Santa Fe y Santa Cruz). Las 24 provincias y sus
alias están en `app/provinces.py`.

`q` busca cada palabra dentro del título, en cualquier orden y sin distinguir
mayúsculas: `q=focus ford` encuentra "Ford Focus SE Plus". No mira provincia ni portal,
y distingue acentos (`megane` no encuentra "Mégane").

```bash
curl -i "http://localhost:8000/cars?q=corolla&province=cordoba&limit=24&offset=24"
```

## Ingesta

```bash
make scrape                                        # todo, 1 página por provincia
make scrape ARGS="--pages 3"                       # hasta 3 páginas por provincia en motordil
make scrape ARGS="--site motordil --provinces cordoba,santa-fe"
make scrape ARGS="--site v6 --v6-limit 20"         # solo v6, primeras 20 publicaciones
make scrape ARGS="--site mercadolibre"             # solo MercadoLibre (1 request)
```

Se puede correr todas las veces que haga falta: las publicaciones se identifican por
`original_url`, así que las conocidas se actualizan y no se duplican. Entre requests
espera 1 segundo (`--delay`).

### De dónde sale cada dato

**motordil** — `motordil.com/autos` es un índice sin publicaciones, así que se recorre
`/autos/usados/{provincia}` para las 24 provincias (24 autos por página, `?page=N`).
La provincia sale de la URL. Si una publicación bajó de precio se guarda el precio
vigente, no el tachado.

**v6** — `v6.com.ar/autos-usados` solo trae una decena de publicaciones en el HTML,
así que la lista completa se saca de su `sitemap.xml` y se abre cada publicación
(unas 180, un request por cada una). Los datos salen de la información que la página
le pasa a su propia app y, si falta, de sus datos `schema.org`. Si el sitemap no
responde, se usa el listado de `/autos-usados`.

v6 identifica cada publicación por el id del final de la URL e ignora el resto, así
que se guarda siempre la forma `https://v6.com.ar/auto/{id}`.

**mercadolibre** — se lee solo la primera página de autos usados de todo el país: 48
publicaciones en un request. Su `robots.txt` no permite recorrer las páginas
siguientes, así que es una muestra de lo más reciente y no el catálogo (unas 90.000
publicaciones). Como el orden cambia, cada corrida suele traer autos nuevos. La
provincia sale de la zona (`Avellaneda - Bs.As. G.B.A. Sur` → Buenos Aires) y el año
se saca del título. Se guarda la forma `https://auto.mercadolibre.com.ar/MLA-{id}-_JM`,
sin el título ni los datos de seguimiento del link.

### Moneda

Cuando la publicación dice la moneda (`USD`, `U$S`, `ARS`, `$`), se respeta. Cuando no
la dice, se deduce por el monto: **menos de 1.000.000 es USD; desde ahí, ARS**. Pasa
en cerca de un tercio de las publicaciones de v6; sus datos `schema.org` no sirven
para esto porque declaran `ARS` siempre, incluso en autos publicados en dólares.

### Limitaciones conocidas

- **Una corrida completa tarda unos 6 minutos**: son ~180 requests a v6, 24 a
  motordil y 1 a MercadoLibre, con 1 segundo de espera entre cada uno.
- **motordil pesa ~2 MB por página.** Con `--pages 1` son 24 requests; Buenos Aires
  tiene más de 100 páginas, así que conviene subir `--pages` de a poco.
- Las publicaciones que desaparecen de los portales no se borran de `cars.db`.
- Los parsers dependen del HTML actual de cada sitio. Si un portal cambia su diseño,
  la ingesta va a traer 0 publicaciones de ese portal y hay que actualizar el parser.

## Tests

```bash
make test
make test ARGS="-k motordil -v"
```

No salen a la red: los parsers se prueban contra HTML real guardado en
`tests/fixtures/` (sin scripts ni imágenes) y los requests se simulan con
`httpx.MockTransport`. La base de datos es SQLite en memoria.

## Estructura

```text
autoclasificado_back/
├── app/
│   ├── main.py        # API: GET /cars
│   ├── models.py      # tabla cars (SQLAlchemy)
│   ├── schemas.py     # esquemas de respuesta (Pydantic)
│   ├── database.py    # conexión a cars.db
│   ├── provinces.py   # normalización de provincias
│   ├── scraper.py     # limpieza, parsers y descarga de cada portal
│   └── ingest.py      # comando de ingesta (upsert en la base)
├── tests/
│   ├── fixtures/      # HTML real de los portales
│   ├── conftest.py    # base en memoria y cliente de pruebas
│   ├── test_api.py
│   └── test_scraper.py
├── Makefile
├── pytest.ini
└── requirements.txt
```

## CORS

La API acepta requests del navegador desde `http://localhost:5173` y
`http://127.0.0.1:5173` (el servidor de desarrollo de Vite). Para permitir otros
orígenes, definí `CORS_ORIGINS` con una lista separada por comas:

```bash
CORS_ORIGINS="https://autoclasificado.example,http://localhost:5173" make run
```

## Pendiente

- La búsqueda por modelo distingue acentos.
