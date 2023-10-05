from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl

# class Status(Enum):
    
#     2

class CostType(str, Enum):
    per_user = "per_user"
    per_account = "per_account"

class ShowIntegration(BaseModel):
    id: int
    status : bool
    name: str   
    image: str
    images: list[str]
    description_short: str
    description_long: str
    folder_name: str
    microservice_id: int
    owner: int
    is_public: int
    cost: int
    cost_type: CostType
    cost_percent: float
    payed_to: Optional[str] = ""
    is_payed: Optional[bool] = False
    trial: Optional[str] = ""

class Integration(BaseModel):
    status : bool
    name: str   
    description_short: str
    description_long: str
    folder_name: str
    microservice_id: int
    is_public: int
    cost: int
    cost_type: CostType
    cost_percent: float
    # payed_to: str
    # is_payed: bool
    # trial: str

    class Config:  
        use_enum_values = True


class CreateApp(Integration):
    scopes: str
    url: HttpUrl
    redirect_uri: Optional[HttpUrl] = None

class UpdateIntegration(BaseModel):
    status : Optional[bool] =  None
    name: Optional[str] =  None
    description_short: Optional[str] =  None
    description_long: Optional[str] =  None
    folder_name: Optional[str] =  None
    microservice_id: Optional[int] =  None
    is_public: Optional[int] = None
    cost: Optional[int] = None
    cost_type: Optional[CostType] = None
    cost_percent: Optional[float] = None
    payed_to: Optional[str] = None
    # is_payed: Optional[bool] =  None
    trial: Optional[str] =  None


class JwtScope(BaseModel):
    scope: str
    interaction: str


class TokenData(BaseModel):
    scopes: list[str]