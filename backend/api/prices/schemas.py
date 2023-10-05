from pydantic import BaseModel
from typing import Optional, List


class PriceCreate(BaseModel):
    price: float
    nomenclature: int
    price_type: Optional[int]
    date_from: Optional[int]
    date_to: Optional[int]

    class Config:
        orm_mode = True


class PriceCreateMass(BaseModel):
    __root__: List[PriceCreate]

    class Config:
        orm_mode = True


class PriceEdit(PriceCreate):
    price: Optional[float]
    nomenclature: Optional[int]


class PriceInList(BaseModel):
    id: int
    name: str
    type: Optional[str]
    description_short: Optional[str]
    description_long: Optional[str]
    code: Optional[int]
    unit: Optional[int]
    unit_name: Optional[str]
    category: Optional[int]
    category_name: Optional[str]
    manufacturer: Optional[int]
    manufacturer_name: Optional[str]
    price: float
    price_type: Optional[str]
    price_finishes: Optional[int]

    class Config:
        orm_mode = True


class Price(PriceInList):
    updated_at: int
    created_at: int


class PriceList(BaseModel):
    __root__: Optional[List[PriceInList]]

    class Config:
        orm_mode = True


class PricePure(BaseModel):
    id: int
    price_type: Optional[int]
    price: float
    nomenclature: Optional[int]
    date_from: Optional[int]
    date_to: Optional[int]
    updated_at: int
    created_at: int


class PriceListPure(BaseModel):
    __root__: Optional[List[PricePure]]

    class Config:
        orm_mode = True
