"""Consent, patient data rights (export, erasure), retention and security incidents."""

import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status

from prefix_api.api.deps import DbSession, Now, require
from prefix_api.domain.permissions import Permission
from prefix_api.domain.principal import StaffPrincipal
from prefix_api.schemas.compliance import (
    ConsentIn,
    ConsentRecordOut,
    ConsentStatusOut,
    ErasureIn,
    ErasureOut,
    ErasurePhraseOut,
    IncidentIn,
    IncidentOut,
    IncidentPatientsIn,
    IncidentPatientsOut,
    IncidentStepIn,
    RetentionPolicyIn,
    RetentionReportOut,
)
from prefix_api.services.consent import ConsentService
from prefix_api.services.data_rights import DataRightsService, erasure_phrase
from prefix_api.services.export_pdf import render_patient_export
from prefix_api.services.incidents import IncidentService
from prefix_api.services.retention import RetentionService

router = APIRouter(tags=["compliance"])

Reader = Annotated[StaffPrincipal, Depends(require(Permission.PATIENT_READ))]
Writer = Annotated[StaffPrincipal, Depends(require(Permission.PATIENT_WRITE))]
Exporter = Annotated[StaffPrincipal, Depends(require(Permission.PATIENT_EXPORT))]
Eraser = Annotated[StaffPrincipal, Depends(require(Permission.PATIENT_ERASE))]
RetentionManager = Annotated[StaffPrincipal, Depends(require(Permission.RETENTION_MANAGE))]
IncidentManager = Annotated[StaffPrincipal, Depends(require(Permission.INCIDENT_MANAGE))]


# Consent ------------------------------------------------------------------------------------


@router.post("/patients/{patient_id}/consent", status_code=status.HTTP_201_CREATED)
def record_consent(
    patient_id: uuid.UUID, body: ConsentIn, principal: Writer, session: DbSession, now: Now
) -> ConsentRecordOut:
    record = ConsentService(session).record(principal, patient_id, granted=body.granted, now=now)
    return ConsentRecordOut.build(record)


@router.get("/patients/{patient_id}/consent")
def consent_status(
    patient_id: uuid.UUID, principal: Reader, session: DbSession
) -> ConsentStatusOut:
    patient, records = ConsentService(session).history(principal, patient_id)
    return ConsentStatusOut(
        consent_to_message=patient.consent_to_message,
        consent_timestamp=patient.consent_timestamp,
        erased=patient.erased_at is not None,
        history=[ConsentRecordOut.build(r) for r in records],
    )


# Data rights --------------------------------------------------------------------------------


@router.get("/patients/{patient_id}/export")
def export_patient(
    patient_id: uuid.UUID,
    principal: Exporter,
    session: DbSession,
    now: Now,
    fmt: Annotated[Literal["json", "pdf"], Query(alias="format")] = "json",
) -> Response:
    data = DataRightsService(session).export(principal, patient_id, now=now, fmt=fmt)
    stamp = now.strftime("%Y%m%d")
    name = f"patient-{patient_id.hex[:8]}-{stamp}"
    headers = {"Cache-Control": "no-store"}
    if fmt == "pdf":
        headers["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
        return Response(render_patient_export(data), media_type="application/pdf", headers=headers)
    headers["Content-Disposition"] = f'attachment; filename="{name}.json"'
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(body, media_type="application/json", headers=headers)


@router.get("/patients/{patient_id}/erasure")
def erasure_confirmation(patient_id: uuid.UUID, principal: Eraser) -> ErasurePhraseOut:
    """The phrase the admin must type. Derived from the id only: no patient data."""
    return ErasurePhraseOut(phrase=erasure_phrase(patient_id))


@router.post("/patients/{patient_id}/erasure")
def erase_patient(
    patient_id: uuid.UUID, body: ErasureIn, principal: Eraser, session: DbSession, now: Now
) -> ErasureOut:
    result = DataRightsService(session).erase(
        principal, patient_id, acknowledged=body.acknowledged, phrase=body.phrase, now=now
    )
    return ErasureOut.build(result)


# Retention ----------------------------------------------------------------------------------


@router.get("/compliance/retention")
def retention_report(
    principal: RetentionManager, session: DbSession, now: Now
) -> RetentionReportOut:
    """A dry run: what tonight's purge would erase, in counts. Changes nothing."""
    return RetentionReportOut.build(RetentionService(session).report(principal, now=now))


@router.put("/compliance/retention")
def set_retention(
    body: RetentionPolicyIn, principal: RetentionManager, session: DbSession, now: Now
) -> RetentionReportOut:
    service = RetentionService(session)
    service.set_policy(principal, body.retention_days)
    return RetentionReportOut.build(service.report(principal, now=now))


# Incidents ----------------------------------------------------------------------------------


@router.get("/compliance/incidents")
def list_incidents(principal: IncidentManager, session: DbSession) -> list[IncidentOut]:
    return [IncidentOut.build(v) for v in IncidentService(session).list_incidents(principal)]


@router.post("/compliance/incidents", status_code=status.HTTP_201_CREATED)
def create_incident(
    body: IncidentIn, principal: IncidentManager, session: DbSession, now: Now
) -> IncidentOut:
    view = IncidentService(session).create(
        principal,
        title=body.title,
        description=body.description,
        severity=body.severity,
        discovered_at=body.discovered_at,
        data_categories=body.data_categories,
        now=now,
    )
    return IncidentOut.build(view)


@router.get("/compliance/incidents/{incident_id}")
def get_incident(
    incident_id: uuid.UUID, principal: IncidentManager, session: DbSession
) -> IncidentOut:
    return IncidentOut.build(IncidentService(session).get(principal, incident_id))


@router.post("/compliance/incidents/{incident_id}/patients")
def add_incident_patients(
    incident_id: uuid.UUID,
    body: IncidentPatientsIn,
    principal: IncidentManager,
    session: DbSession,
    now: Now,
) -> IncidentPatientsOut:
    result = IncidentService(session).add_patients_by_mrn(
        principal, incident_id, body.mrns, now=now
    )
    return IncidentPatientsOut.build(result)


@router.post("/compliance/incidents/{incident_id}/steps")
def advance_incident(
    incident_id: uuid.UUID,
    body: IncidentStepIn,
    principal: IncidentManager,
    session: DbSession,
    now: Now,
) -> IncidentOut:
    return IncidentOut.build(
        IncidentService(session).advance(principal, incident_id, body.step, now=now)
    )


@router.get("/compliance/incidents/{incident_id}/report")
def incident_report(
    incident_id: uuid.UUID, principal: IncidentManager, session: DbSession, now: Now
) -> Response:
    """The export for the authority: facts, timeline and counts, never identities."""
    data = IncidentService(session).authority_report(principal, incident_id, now=now)
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="incident-{incident_id.hex[:8]}.json"',
            "Cache-Control": "no-store",
        },
    )
