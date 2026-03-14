"""Models for the doctor service."""

from typing import Literal

from pydantic import BaseModel


class DoctorInput(BaseModel):
    fix: bool = False


class CheckResultModel(BaseModel):
    name: str
    status: Literal["pass", "fail", "warn"]
    message: str
    detail: str = ""
    fixable: bool = False


class DoctorResult(BaseModel):
    checks: list[CheckResultModel]
    has_failures: bool
