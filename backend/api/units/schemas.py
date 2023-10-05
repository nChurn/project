from pydantic import BaseModel
from typing import Optional, List


class Unit(BaseModel):
    id: int
    code: int
    name: str
    convent_national_view: str
    convent_international_view: str
    symbol_national_view: str
    symbol_international_view: str

    class Config:
        orm_mode = True


class UnitList(BaseModel):
    __root__: Optional[List[Unit]]

    class Config:
        orm_mode = True
