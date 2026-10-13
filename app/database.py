"""
Configuración de la conexión a la base de datos SQLite con SQLAlchemy.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Archivo de base de datos local SQLite
DATABASE_URL = "sqlite:///./cars.db"

# connect_args={"check_same_thread": False} es necesario para SQLite en FastAPI
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

# Sesión local para interactuar con la base de datos
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Clase base para definir nuestros modelos
Base = declarative_base()


def get_db():
    """Generador de sesiones de base de datos para inyectar en las rutas."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()