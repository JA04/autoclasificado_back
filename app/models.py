"""
Definición de modelos de datos usando SQLAlchemy ORM.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base


class Car(Base):
    __tablename__ = "cars"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=True)
    mileage = Column(Integer, nullable=True)        # Kilómetros en entero
    price = Column(Integer, nullable=True)          # Monto numérico
    currency = Column(String, default="USD")        # 'USD' o 'ARS'
    province = Column(String, nullable=True, index=True) # Provincia para filtros
    original_url = Column(String, unique=True, nullable=False)
    source_site = Column(String, nullable=False)    # 'motordil' o 'v6'
    created_at = Column(DateTime, default=datetime.utcnow)