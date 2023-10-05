from pydantic import BaseModel


class AccountInfo(BaseModel):
    type: str
    demo_expiration: int
    demo_left: int
    balance: float
    users: int
    price: float
    is_per_user: bool
    tariff: str
    link_for_pay: str
    demo_period: int
