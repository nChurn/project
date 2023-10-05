from pydantic import BaseModel
from typing import Optional, List


class Item(BaseModel):
    document_sale: Optional[int]
    net_cost: float
    sum: float
    actual_revenue: float
    direct_costs: float
    indirect_costs: float
    gross_profit: float
    rentability: float
    sales_manager: int


class ViewInList(BaseModel):
    id: int
    organization: int
    period_start: int
    period_end: int
    updated_at: int
    created_at: int


class View(ViewInList):
    table: Optional[List[Item]]

    class Config:
        orm_mode = True


class ListView(BaseModel):
    __root__: Optional[List[ViewInList]]

    class Config:
        orm_mode = True
