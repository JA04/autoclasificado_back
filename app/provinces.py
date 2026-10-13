"""
Normalización de nombres de provincias argentinas.
"""
import re
import unicodedata
from typing import Dict, List, Optional

# Nombre canónico -> variantes con las que suele aparecer en las publicaciones
PROVINCE_ALIASES: Dict[str, List[str]] = {
    "Buenos Aires": ["Bs As", "BsAs", "Pcia de Buenos Aires", "Provincia de Buenos Aires", "Gran Buenos Aires"],
    "Capital Federal": ["CABA", "Cap Fed", "Ciudad de Buenos Aires", "Ciudad Autónoma de Buenos Aires"],
    "Catamarca": [],
    "Chaco": [],
    "Chubut": [],
    "Córdoba": ["Cba"],
    "Corrientes": [],
    "Entre Ríos": [],
    "Formosa": [],
    "Jujuy": [],
    "La Pampa": [],
    "La Rioja": [],
    "Mendoza": ["Mza"],
    "Misiones": [],
    "Neuquén": [],
    "Río Negro": [],
    "Salta": [],
    "San Juan": [],
    "San Luis": [],
    "Santa Cruz": [],
    "Santa Fe": ["Sta Fe"],
    "Santiago del Estero": ["Sgo del Estero"],
    "Tierra del Fuego": [],
    "Tucumán": [],
}


def _key(text: str) -> str:
    """Lleva un texto a minúsculas, sin acentos ni puntuación ('Bs. As.' -> 'bs as')."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plain = plain.lower().replace(".", "")
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


_LOOKUP: Dict[str, str] = {
    _key(name): canonical
    for canonical, aliases in PROVINCE_ALIASES.items()
    for name in [canonical, *aliases]
}

# Las variantes más largas primero, para que 'Ciudad de Buenos Aires' le gane a 'Buenos Aires'
_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, _LOOKUP), key=len, reverse=True)) + r")\b"
)


def normalize_province(text: Optional[str]) -> Optional[str]:
    """Devuelve el nombre canónico de una provincia (e.g. 'cordoba' -> 'Córdoba') o None."""
    if not text:
        return None
    return _LOOKUP.get(_key(text))


def find_province(text: Optional[str]) -> Optional[str]:
    """Busca la primera provincia mencionada dentro de un texto libre."""
    if not text:
        return None
    match = _PATTERN.search(_key(text))
    return _LOOKUP[match.group(1)] if match else None
