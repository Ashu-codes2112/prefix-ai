"""Reading FHIR R4 resources into Prefix AI's terms. Pure functions: no database, no network.

Only what the prep plan needs is read. Patients keep their names, birth date, contact details,
MRN and language. Medications, kidney results and allergies become the condition flags that
protocol steps test (on_anticoagulant, diabetic_on_insulin, on_glp1_agonist,
has_contrast_allergy, egfr_value). Gender is read only to compute eGFR from creatinine, and is
never stored.

The medication and allergy lists below decide which patients get which protocol steps. They
are deliberately inclusive (a false "on a blood thinner" adds a check, a missed one can cancel
a procedure) and must be reviewed by the hospital's clinicians (docs/INTEGRATIONS.md).
"""

import datetime as dt
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from prefix_api.domain.enums import AppointmentStatus, CancellationReason

type Resource = dict[str, Any]


class MedicationFlag(StrEnum):
    ANTICOAGULANT = "on_anticoagulant"
    INSULIN = "diabetic_on_insulin"
    GLP1 = "on_glp1_agonist"


# Name fragments, matched against the coding display and the text (case-insensitive).
_ANTICOAGULANTS = (
    "warfarin",
    "acenocoumarol",
    "apixaban",
    "rivaroxaban",
    "dabigatran",
    "edoxaban",
    "heparin",
    "enoxaparin",
    "dalteparin",
    "fondaparinux",
    # Antiplatelets that protocols also ask to stop or review before procedures.
    "clopidogrel",
    "prasugrel",
    "ticagrelor",
)
_INSULINS = ("insulin",)
_GLP1 = (
    "semaglutide",
    "liraglutide",
    "dulaglutide",
    "exenatide",
    "lixisenatide",
    "tirzepatide",
)
# ATC code prefixes: B01A antithrombotics (aspirin, B01AC06, is left out on purpose),
# A10A insulins, A10BJ GLP-1 receptor agonists.
_ATC = {
    "B01AA": MedicationFlag.ANTICOAGULANT,
    "B01AB": MedicationFlag.ANTICOAGULANT,
    "B01AE": MedicationFlag.ANTICOAGULANT,
    "B01AF": MedicationFlag.ANTICOAGULANT,
    "B01AC04": MedicationFlag.ANTICOAGULANT,  # clopidogrel
    "B01AC22": MedicationFlag.ANTICOAGULANT,  # prasugrel
    "B01AC24": MedicationFlag.ANTICOAGULANT,  # ticagrelor
    "A10A": MedicationFlag.INSULIN,
    "A10BJ": MedicationFlag.GLP1,
}
_ATC_SYSTEM = "http://www.whocc.no/atc"
_ACTIVE_MEDICATION = frozenset({"active", "intended", "on-hold"})

LOINC = "http://loinc.org"
# eGFR in mL/min/1.73m2 (MDRD, CKD-EPI 2009 and 2021, with and without race terms).
EGFR_CODES = frozenset(
    {
        "33914-3",
        "48642-3",
        "48643-1",
        "50044-7",
        "50210-4",
        "62238-1",
        "69405-9",
        "88293-6",
        "88294-4",
        "98979-8",
    }
)
# Creatinine in serum or plasma: mg/dL and µmol/L.
CREATININE_CODES = frozenset({"2160-0", "38483-4", "14682-9"})
_UMOL_PER_MG_DL = 88.42

_CONTRAST_TERMS = (
    "contrast",
    "iodine",
    "iodinated",
    "iohexol",
    "iopamidol",
    "iodixanol",
    "gadolinium",
    "gadobutrol",
)
# SNOMED CT: allergy to contrast media, iodine, iodinated contrast, gadolinium.
_CONTRAST_SNOMED = frozenset({"293637006", "294137005", "418634005", "300916003", "703833003"})
_ACTIVE_ALLERGY = frozenset({"active"})


@dataclass(frozen=True, slots=True)
class PatientRecord:
    fhir_id: str
    first_name: str
    last_name: str
    birth_date: dt.date
    phone: str | None
    email: str | None
    mrn: str | None
    language: str
    # Only for computing eGFR from creatinine; never stored.
    gender: str | None


@dataclass(frozen=True, slots=True)
class KidneyResult:
    patient_ref: str
    effective: dt.datetime | None
    egfr: float | None
    creatinine_mg_dl: float | None


@dataclass(frozen=True, slots=True)
class AppointmentRecord:
    fhir_id: str
    patient_ref: str
    status: AppointmentStatus
    start: dt.datetime | None
    duration_minutes: int | None
    service_codes: tuple[str, ...]
    location_ref: str | None
    cancellation_reason: CancellationReason | None


class FhirMappingError(ValueError):
    """A resource lacks something Prefix AI needs. The message names the field, never a value."""


def last_updated(resource: Resource) -> str | None:
    value = resource.get("meta", {}).get("lastUpdated")
    return value if isinstance(value, str) else None


def reference_id(reference: object, resource_type: str) -> str | None:
    """'Patient/123' (or a full URL ending in it) -> '123'."""
    if not isinstance(reference, dict):
        return None
    ref = reference.get("reference")
    if not isinstance(ref, str):
        return None
    parts = ref.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-2] == resource_type:
        return parts[-1]
    return None


def _codings(concept: object) -> list[dict[str, Any]]:
    if not isinstance(concept, dict):
        return []
    return [c for c in concept.get("coding", []) if isinstance(c, dict)]


def _words(concept: object) -> str:
    if not isinstance(concept, dict):
        return ""
    parts = [str(concept.get("text", ""))]
    parts += [str(c.get("display", "")) for c in _codings(concept)]
    return " ".join(parts).lower()


def _datetime(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # FHIR instants carry a zone; a bare local time is too ambiguous to book
    return parsed


# Patient --------------------------------------------------------------------------------------


def parse_patient(resource: Resource) -> PatientRecord:
    fhir_id = resource.get("id")
    if not isinstance(fhir_id, str):
        raise FhirMappingError("Patient.id is missing")
    names = [n for n in resource.get("name", []) if isinstance(n, dict)]
    name = next((n for n in names if n.get("use") == "official"), names[0] if names else None)
    if name is None or not name.get("family") or not name.get("given"):
        raise FhirMappingError("Patient.name needs a family and a given name")
    try:
        birth = dt.date.fromisoformat(str(resource.get("birthDate", "")))
    except ValueError as exc:
        raise FhirMappingError("Patient.birthDate is missing or not a full date") from exc

    phone = email = None
    for telecom in resource.get("telecom", []):
        if not isinstance(telecom, dict) or telecom.get("use") == "old":
            continue
        value = telecom.get("value")
        if telecom.get("system") == "phone" and phone is None and isinstance(value, str):
            phone = value
        if telecom.get("system") == "email" and email is None and isinstance(value, str):
            email = value

    mrn = None
    for identifier in resource.get("identifier", []):
        codes = {c.get("code") for c in _codings(identifier.get("type"))}
        if "MR" in codes and isinstance(identifier.get("value"), str):
            mrn = identifier["value"]
            break

    language = "en"
    for communication in resource.get("communication", []):
        languages = [
            str(c.get("code", "")).lower() for c in _codings(communication.get("language"))
        ]
        if any(code.startswith("hi") for code in languages):
            language = "hi"
            break

    gender = resource.get("gender")
    return PatientRecord(
        fhir_id=fhir_id,
        first_name=" ".join(str(g) for g in name["given"]),
        last_name=str(name["family"]),
        birth_date=birth,
        phone=phone,
        email=email,
        mrn=mrn,
        language=language,
        gender=gender if gender in ("male", "female") else None,
    )


# Medications ----------------------------------------------------------------------------------


def medication_flags(resource: Resource) -> frozenset[MedicationFlag]:
    """The flags an active MedicationStatement sets. Stopped or completed ones set none."""
    if resource.get("status") not in _ACTIVE_MEDICATION:
        return frozenset()
    concept = resource.get("medicationCodeableConcept")
    words = _words(concept)
    flags: set[MedicationFlag] = set()
    if any(term in words for term in _ANTICOAGULANTS):
        flags.add(MedicationFlag.ANTICOAGULANT)
    if any(term in words for term in _INSULINS):
        flags.add(MedicationFlag.INSULIN)
    if any(term in words for term in _GLP1):
        flags.add(MedicationFlag.GLP1)
    for coding in _codings(concept):
        if coding.get("system") != _ATC_SYSTEM:
            continue
        code = str(coding.get("code", ""))
        flags.update(flag for prefix, flag in _ATC.items() if code.startswith(prefix))
    return frozenset(flags)


# Kidney function ------------------------------------------------------------------------------


def kidney_result(resource: Resource) -> KidneyResult | None:
    """An eGFR or creatinine result, or None for any other Observation."""
    if resource.get("status") not in ("final", "amended", "corrected"):
        return None
    codes = {str(c.get("code")) for c in _codings(resource.get("code")) if c.get("system") == LOINC}
    patient = reference_id(resource.get("subject"), "Patient")
    quantity = resource.get("valueQuantity")
    if patient is None or not isinstance(quantity, dict) or not codes:
        return None
    value = quantity.get("value")
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        return None
    effective = _datetime(resource.get("effectiveDateTime") or resource.get("issued"))
    if codes & EGFR_CODES:
        return KidneyResult(patient, effective, egfr=float(value), creatinine_mg_dl=None)
    if codes & CREATININE_CODES:
        unit = str(quantity.get("code") or quantity.get("unit") or "").lower()
        if "umol" in unit or "µmol" in unit or "14682-9" in codes:
            mg_dl = float(value) / _UMOL_PER_MG_DL
        elif "mg/dl" in unit:
            mg_dl = float(value)
        else:
            return None  # an unknown unit is never guessed
        return KidneyResult(patient, effective, egfr=None, creatinine_mg_dl=mg_dl)
    return None


def ckd_epi_2021(creatinine_mg_dl: float, age_years: int, gender: str) -> float:
    """eGFR (mL/min/1.73m2) by the race-free CKD-EPI 2021 creatinine equation."""
    female = gender == "female"
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    ratio = creatinine_mg_dl / kappa
    egfr = (
        142
        * math.pow(min(ratio, 1.0), alpha)
        * math.pow(max(ratio, 1.0), -1.200)
        * math.pow(0.9938, age_years)
        * (1.012 if female else 1.0)
    )
    return round(egfr, 1)


def age_on(birth: dt.date, day: dt.date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


# Allergies ------------------------------------------------------------------------------------


def is_contrast_allergy(resource: Resource) -> bool:
    statuses = {c.get("code") for c in _codings(resource.get("clinicalStatus"))}
    if statuses and not statuses & _ACTIVE_ALLERGY:
        return False
    verification = {c.get("code") for c in _codings(resource.get("verificationStatus"))}
    if "refuted" in verification or "entered-in-error" in verification:
        return False
    concept = resource.get("code")
    if any(c.get("code") in _CONTRAST_SNOMED for c in _codings(concept)):
        return True
    return any(term in _words(concept) for term in _CONTRAST_TERMS)


# Appointments ---------------------------------------------------------------------------------

_STATUS = {
    "proposed": AppointmentStatus.SCHEDULED,
    "pending": AppointmentStatus.SCHEDULED,
    "booked": AppointmentStatus.SCHEDULED,
    "arrived": AppointmentStatus.SCHEDULED,
    "checked-in": AppointmentStatus.SCHEDULED,
    "waitlist": AppointmentStatus.SCHEDULED,
    "fulfilled": AppointmentStatus.COMPLETED,
    "cancelled": AppointmentStatus.CANCELLED,
    "noshow": AppointmentStatus.CANCELLED,
}
_CANCEL_REASONS = {
    "pat": CancellationReason.PATIENT_REQUESTED,
    "pat-crs": CancellationReason.PATIENT_REQUESTED,
    "pat-cpp": CancellationReason.PATIENT_REQUESTED,
    "pat-dec": CancellationReason.CLINICAL_REASON,
    "pat-fb": CancellationReason.CLINICAL_REASON,
    "pat-lt": CancellationReason.PATIENT_REQUESTED,
    "prov": CancellationReason.FACILITY_OR_STAFF,
    "prov-pers": CancellationReason.FACILITY_OR_STAFF,
    "prov-dch": CancellationReason.FACILITY_OR_STAFF,
    "prov-dis": CancellationReason.FACILITY_OR_STAFF,
    "maint": CancellationReason.FACILITY_OR_STAFF,
    "meds-inc": CancellationReason.MEDICATION_NOT_STOPPED,
}


def parse_appointment(resource: Resource) -> AppointmentRecord:
    fhir_id = resource.get("id")
    if not isinstance(fhir_id, str):
        raise FhirMappingError("Appointment.id is missing")
    status = _STATUS.get(str(resource.get("status")))
    if status is None:
        raise FhirMappingError("Appointment.status is not one Prefix AI follows")
    patient = location = None
    for participant in resource.get("participant", []):
        actor = participant.get("actor") if isinstance(participant, dict) else None
        patient = patient or reference_id(actor, "Patient")
        location = location or reference_id(actor, "Location")
    if patient is None:
        raise FhirMappingError("Appointment.participant has no Patient")
    start = _datetime(resource.get("start"))
    end = _datetime(resource.get("end"))
    minutes = resource.get("minutesDuration")
    if not isinstance(minutes, int) and start and end:
        minutes = int((end - start).total_seconds() // 60)
    codes = tuple(
        str(c.get("code"))
        for concept in [*resource.get("serviceType", []), resource.get("appointmentType")]
        for c in _codings(concept)
        if c.get("code")
    )
    reason = None
    if status is AppointmentStatus.CANCELLED:
        if resource.get("status") == "noshow":
            reason = CancellationReason.PATIENT_NO_SHOW
        else:
            code = next(
                (str(c.get("code")) for c in _codings(resource.get("cancelationReason"))), ""
            )
            reason = _CANCEL_REASONS.get(code, CancellationReason.OTHER)
    return AppointmentRecord(
        fhir_id=fhir_id,
        patient_ref=patient,
        status=status,
        start=start,
        duration_minutes=minutes if isinstance(minutes, int) and minutes > 0 else None,
        service_codes=codes,
        location_ref=location,
        cancellation_reason=reason,
    )
