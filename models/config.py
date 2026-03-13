"""Models for the config service."""

from pydantic import BaseModel


class ConfigShowInput(BaseModel):
    pass


class ConfigShowResult(BaseModel):
    config: dict


class ConfigGetInput(BaseModel):
    key: str


class ConfigGetResult(BaseModel):
    key: str
    value: object


class ConfigSetInput(BaseModel):
    key: str
    value: str


class ConfigSetResult(BaseModel):
    key: str
    coerced_value: object
