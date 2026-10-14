"""
Ingesta: descarga las publicaciones de los portales y las guarda en la base de datos.

Uso: python -m app.ingest [--site motordil|v6|mercadolibre|all] [--pages N] [--provinces slug1,slug2] [--v6-limit N]
"""
import argparse
import logging
from typing import Any, Dict, Iterable, List, Tuple

from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.models import Car
from app.scraper import MOTORDIL_PROVINCE_SLUGS, make_client, scrape_mercadolibre, scrape_motordil, scrape_v6

logger = logging.getLogger(__name__)


def upsert_cars(db: Session, items: Iterable[Dict[str, Any]]) -> Tuple[int, int]:
    """Inserta las publicaciones nuevas y actualiza las ya conocidas (por `original_url`).

    Devuelve (insertadas, actualizadas).
    """
    inserted = updated = 0
    seen = set()
    for item in items:
        url = item.get("original_url")
        if not url or not item.get("title") or url in seen:
            continue
        seen.add(url)

        car = db.query(Car).filter(Car.original_url == url).first()
        if car is None:
            db.add(Car(**item))
            inserted += 1
        else:
            # Un dato que esta vez no se pudo leer no pisa al que ya teníamos
            for field, value in item.items():
                if value is not None:
                    setattr(car, field, value)
            updated += 1
    db.commit()
    return inserted, updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Descarga publicaciones y las guarda en cars.db")
    parser.add_argument("--site", choices=["motordil", "v6", "mercadolibre", "all"], default="all")
    parser.add_argument("--pages", type=int, default=1, help="páginas por provincia en motordil (24 autos c/u)")
    parser.add_argument(
        "--provinces",
        help=f"provincias de motordil separadas por coma (default: todas). Opciones: {', '.join(MOTORDIL_PROVINCE_SLUGS)}",
    )
    parser.add_argument("--v6-limit", type=int, help="máximo de publicaciones de v6 (default: todas)")
    parser.add_argument("--delay", type=float, default=1.0, help="segundos de espera entre requests")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    slugs = args.provinces.split(",") if args.provinces else None
    unknown = set(slugs or []) - set(MOTORDIL_PROVINCE_SLUGS)
    if unknown:
        parser.error(f"provincias desconocidas: {', '.join(sorted(unknown))}")

    items: List[Dict[str, Any]] = []
    with make_client() as client:
        if args.site in ("v6", "all"):
            items += scrape_v6(client, delay=args.delay, limit=args.v6_limit)
        if args.site in ("motordil", "all"):
            items += scrape_motordil(client, slugs, max_pages=args.pages, delay=args.delay)
        if args.site in ("mercadolibre", "all"):
            items += scrape_mercadolibre(client)

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        inserted, updated = upsert_cars(db, items)
    logger.info("Listo: %d nuevas, %d actualizadas", inserted, updated)


if __name__ == "__main__":
    main()
