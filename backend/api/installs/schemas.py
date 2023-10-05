from pydantic import BaseModel
from typing import Optional


class Install(BaseModel):
    id: int
    md5key: str


class InstallCreate(BaseModel):
    iosversion: str
    phone: str
    devicetoken: str


class InstallSettings(BaseModel):
    geolocation: Optional[str]
    push: Optional[bool]
    my_push: Optional[str]
    foreign_push: Optional[str]
    contacts: Optional[bool]
