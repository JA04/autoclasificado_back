"""
Esquemas de validación y serialización de datos con Pydantic.
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class CarBase(BaseModel):
    title: str
    year: Optional[int] = None
    mileage: Optional[int] = None
    price: Optional[int] = None
    currency: Optional[str] = "USD"
    province: Optional[str] = None
    original_url: str
    source_site: str


class CarCreate(CarBase):
    pass


class CarResponse(CarBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)