"""Models for the doctor service."""

from pydantic import BaseModel


class DoctorInput(BaseModel):
    fix: bool = False


class CheckResultModel(BaseModel):
    name: str
    status: str
    message: str
    detail: str = ""
    fixable: bool = False


class DoctorResult(BaseModel):
    checks: list[CheckResultModel]
    has_failures: bool
