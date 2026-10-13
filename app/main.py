"""
Punto de entrada de la aplicación FastAPI.
"""
import os
from typing import List, Optional
from fastapi import FastAPI, Depends, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.database import engine, Base, get_db
from app.models import Car
from app.provinces import normalize_province
from app.schemas import CarResponse

# Creamos las tablas en SQLite al iniciar
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Clasificados Autos API", version="1.0.0")

# Orígenes que pueden llamar a la API desde el navegador (separados por coma).
# Por defecto, el servidor de desarrollo de Vite.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)


def escape_like(text: str) -> str:
    """Escapa los comodines de LIKE para buscar el texto de forma literal."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@app.get("/cars", response_model=List[CarResponse])
def get_cars(
    response: Response,
    province: Optional[str] = Query(None, description="Filtrar por provincia"),
    q: Optional[str] = Query(None, description="Buscar por nombre del modelo (título)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, description="Cantidad de resultados a saltear"),
    db: Session = Depends(get_db)
):
    """Retorna publicaciones de autos, con filtro por provincia, búsqueda por modelo y paginado.

    El total de resultados (sin paginar) se informa en el header X-Total-Count.
    """
    query = db.query(Car)
    if province:
        # 'cordoba' o 'Bs. As.' se buscan por su nombre canónico; el resto, tal cual
        term = normalize_province(province) or province
        query = query.filter(Car.province.ilike(f"%{term}%"))
    if q:
        # Cada palabra tiene que aparecer en el título, en cualquier orden
        for word in q.split():
            query = query.filter(Car.title.ilike(f"%{escape_like(word)}%", escape="\\"))

    response.headers["X-Total-Count"] = str(query.count())
    return query.order_by(Car.id).offset(offset).limit(limit).all()
