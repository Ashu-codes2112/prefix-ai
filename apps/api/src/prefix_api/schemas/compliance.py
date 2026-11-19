"""Consent, data rights, retention and incidents (go-live compliance)."""

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from prefix_api.db.models import ConsentRecord, SecurityIncident
from prefix_api.domain.enums import ConsentSource, IncidentSeverity, IncidentStatus
from prefix_api.services.data_rights import ErasureResult
from prefix_api.services.incidents import AddPatientsResult, IncidentView
from prefix_api.services.retention import RetentionReport


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granted: bool


class ConsentRecordOut(BaseModel):
    granted: bool
    source: ConsentSource
    recorded_at: dt.datetime
    recorded_by_staff: bool

    @classmethod
    def build(cls, record: ConsentRecord) -> ConsentRecordOut:
        return cls(
            granted=record.granted,
            source=record.source,
            recorded_at=record.recorded_at,
            recorded_by_staff=record.recorded_by_user_id is not None,
        )


class ConsentStatusOut(BaseModel):
    """The current answer and its history: enough for the patient sheet, no identifiers."""

    consent_to_message: bool
    consent_timestamp: dt.datetime | None
    erased: bool
    history: list[ConsentRecordOut]


class ErasureIn(BaseModel):
    """Both confirmations are required: the box, and the patient's own erasure phrase."""

    model_config = ConfigDict(extra="forbid")

    acknowledged: bool
    phrase: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]


class ErasurePhraseOut(BaseModel):
    phrase: str


class ErasureOut(BaseModel):
    patient_id: uuid.UUID
    erased_at: dt.datetime
    rows_changed: int

    @classmethod
    def build(cls, result: ErasureResult) -> ErasureOut:
        return cls(
            patient_id=result.patient_id,
            erased_at=result.erased_at,
            rows_changed=result.rows_changed,
        )


class RetentionPolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None keeps everything.
    retention_days: int | None


class RetentionReportOut(BaseModel):
    retention_days: int | None
    cutoff: dt.datetime | None
    dry_run: bool
    due: int
    held_for_incident: int
    held_for_upcoming_care: int
    erased: int

    @classmethod
    def build(cls, report: RetentionReport) -> RetentionReportOut:
        return cls(
            retention_days=report.retention_days,
            cutoff=report.cutoff,
            dry_run=report.dry_run,
            due=len(report.due),
            held_for_incident=report.held_for_incident,
            held_for_upcoming_care=report.held_for_upcoming_care,
            erased=report.erased,
        )


IncidentTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)
]
IncidentText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
]


class IncidentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: IncidentTitle
    description: IncidentText
    severity: IncidentSeverity
    discovered_at: AwareDatetime
    data_categories: list[str] = Field(default_factory=list, max_length=10)


class IncidentPatientsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mrns: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]] = Field(
        min_length=1, max_length=1000
    )


class IncidentPatientsOut(BaseModel):
    added: int
    already_listed: int
    not_found: int

    @classmethod
    def build(cls, result: AddPatientsResult) -> IncidentPatientsOut:
        return cls(
            added=result.added,
            already_listed=result.already_listed,
            not_found=result.not_found,
        )


class IncidentStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: Literal["contained", "authority_notified", "patients_notified", "closed"]


class IncidentOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    severity: IncidentSeverity
    status: IncidentStatus
    data_categories: list[str]
    discovered_at: dt.datetime
    notify_by: dt.datetime
    authority_notified_at: dt.datetime | None
    patients_notified_at: dt.datetime | None
    closed_at: dt.datetime | None
    affected_patients: int

    @classmethod
    def build(cls, view: IncidentView) -> IncidentOut:
        i: SecurityIncident = view.incident
        return cls(
            id=i.id,
            title=i.title,
            description=i.description,
            severity=i.severity,
            status=i.status,
            data_categories=i.data_categories,
            discovered_at=i.discovered_at,
            notify_by=i.notify_by,
            authority_notified_at=i.authority_notified_at,
            patients_notified_at=i.patients_notified_at,
            closed_at=i.closed_at,
            affected_patients=view.affected_patients,
        )
