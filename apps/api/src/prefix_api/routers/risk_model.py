"""Which cancellation risk model scores procedures, for the dashboard. No patient data."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from prefix_api.api.deps import StaffUser
from prefix_api.services.risk import RiskModel

router = APIRouter(prefix="/risk", tags=["risk"])


class RiskModelOut(BaseModel):
    name: Literal["v1", "v2"]
    version: str
    # What the dashboard shows: "v1 rules" or "v2 ML".
    label: str


@router.get("/model")
def risk_model(principal: StaffUser, request: Request) -> RiskModelOut:
    model: RiskModel = request.app.state.risk_model
    return RiskModelOut(name=model.name, version=model.version, label=model.label)
