"""Whether the AI Prep Assistant answers, for the staff app. No patient data."""

from fastapi import APIRouter

from prefix_api.api.deps import AssistantLLMDep, StaffUser
from prefix_api.schemas.assistant import AssistantStatusOut

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.get("/status")
def assistant_status(principal: StaffUser, llm: AssistantLLMDep) -> AssistantStatusOut:
    if llm is None:
        return AssistantStatusOut(mode="nurse_handoff", model=None)
    return AssistantStatusOut(mode="live", model=llm.model)
