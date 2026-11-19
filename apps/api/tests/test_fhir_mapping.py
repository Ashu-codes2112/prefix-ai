"""FHIR R4 resources into Prefix AI's terms: patients, condition flags, kidney results, bookings."""

import datetime as dt

import pytest

from prefix_api.domain.enums import AppointmentStatus, CancellationReason
from prefix_api.domain.fhir import (
    FhirMappingError,
    MedicationFlag,
    age_on,
    ckd_epi_2021,
    is_contrast_allergy,
    kidney_result,
    medication_flags,
    parse_appointment,
    parse_patient,
    reference_id,
)
from prefix_api.services.fhir.fixture_server import load_fixtures

NOW = dt.datetime(2026, 11, 19, 6, 0, tzinfo=dt.UTC)
FIXTURES = {(r["resourceType"], r["id"]): r for r in load_fixtures(NOW)}


def fixture(kind: str, fhir_id: str) -> dict[str, object]:
    return FIXTURES[(kind, fhir_id)]


def test_a_patient_with_mrn_language_and_contacts() -> None:
    record = parse_patient(fixture("Patient", "pat-1001"))

    assert (record.first_name, record.last_name) == ("Kavya", "Sharma")
    assert record.birth_date == dt.date(1968, 4, 12)
    assert (record.phone, record.email, record.mrn) == (
        "+919800001001",
        "kavya.sharma@example.org",
        "FH-1001",
    )
    assert record.language == "hi"
    assert record.gender == "female"


def test_given_names_are_joined_and_english_is_the_default() -> None:
    record = parse_patient(fixture("Patient", "pat-1002"))

    assert record.first_name == "Rohan K."
    assert record.language == "en"


def test_a_patient_without_a_given_name_is_refused_by_field_not_value() -> None:
    with pytest.raises(FhirMappingError, match=r"^Patient\.name needs a family and a given name$"):
        parse_patient(fixture("Patient", "pat-1003"))


@pytest.mark.parametrize(
    ("concept", "status", "flags"),
    [
        ({"text": "Apixaban 5 mg"}, "active", {MedicationFlag.ANTICOAGULANT}),
        ({"text": "Clopidogrel 75 mg"}, "active", {MedicationFlag.ANTICOAGULANT}),
        (
            {"coding": [{"system": "http://www.whocc.no/atc", "code": "B01AA03"}]},
            "active",
            {MedicationFlag.ANTICOAGULANT},
        ),
        ({"text": "Aspirin 75 mg"}, "active", set()),
        ({"text": "Insulin glargine"}, "intended", {MedicationFlag.INSULIN}),
        (
            {"coding": [{"system": "http://www.whocc.no/atc", "code": "A10BJ06"}]},
            "active",
            {MedicationFlag.GLP1},
        ),
        ({"text": "Tirzepatide weekly"}, "on-hold", {MedicationFlag.GLP1}),
        ({"text": "Warfarin 5 mg"}, "stopped", set()),
        ({"text": "Warfarin 5 mg"}, "completed", set()),
        ({"text": "Metformin"}, "active", set()),
    ],
)
def test_medications_become_condition_flags(
    concept: dict[str, object], status: str, flags: set[MedicationFlag]
) -> None:
    resource = {"status": status, "medicationCodeableConcept": concept}

    assert medication_flags(resource) == flags


def test_kidney_results_in_either_unit() -> None:
    egfr = kidney_result(fixture("Observation", "obs-1"))
    creatinine = kidney_result(fixture("Observation", "obs-2"))
    umol = kidney_result(
        {
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "14682-9"}]},
            "subject": {"reference": "Patient/p"},
            "valueQuantity": {"value": 88.42, "unit": "umol/L"},
        }
    )

    assert egfr is not None
    assert (egfr.patient_ref, egfr.egfr) == ("pat-1001", 52.0)
    assert creatinine is not None
    assert creatinine.creatinine_mg_dl == pytest.approx(1.4)
    assert umol is not None
    assert umol.creatinine_mg_dl == pytest.approx(1.0)


@pytest.mark.parametrize(
    "resource",
    [
        {"status": "preliminary"},
        {"status": "final", "code": {"coding": [{"system": "http://loinc.org", "code": "718-7"}]}},
        {
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "2160-0"}]},
            "subject": {"reference": "Patient/p"},
            "valueQuantity": {"value": 1.2, "unit": "furlongs"},
        },
    ],
    ids=["not final", "another test", "unknown unit"],
)
def test_other_observations_are_ignored_and_units_never_guessed(
    resource: dict[str, object],
) -> None:
    assert kidney_result(resource) is None


def test_ckd_epi_2021_matches_published_values() -> None:
    # Worked by hand from the published equation, e.g. a man of 70 with creatinine 1.4 mg/dL:
    # 142 x (1.4 / 0.9) ** -1.2 x 0.9938 ** 70 = 54.1.
    assert ckd_epi_2021(1.0, 50, "male") == pytest.approx(91.7, abs=0.1)
    assert ckd_epi_2021(1.0, 50, "female") == pytest.approx(68.6, abs=0.1)
    assert ckd_epi_2021(1.4, 70, "male") == pytest.approx(54.1, abs=0.1)
    assert ckd_epi_2021(0.6, 40, "female") == pytest.approx(116.0, abs=0.5)
    assert age_on(dt.date(1955, 11, 30), dt.date(2026, 11, 29)) == 70


def test_contrast_allergies_by_code_or_name_while_active() -> None:
    assert is_contrast_allergy(fixture("AllergyIntolerance", "alg-1"))
    assert not is_contrast_allergy(fixture("AllergyIntolerance", "alg-2"))
    assert is_contrast_allergy({"code": {"text": "Iodinated contrast - hives"}})
    assert not is_contrast_allergy(
        {"clinicalStatus": {"coding": [{"code": "resolved"}]}, "code": {"text": "contrast"}}
    )
    assert not is_contrast_allergy(
        {"verificationStatus": {"coding": [{"code": "refuted"}]}, "code": {"text": "contrast"}}
    )


def test_an_appointment_with_its_patient_location_and_service() -> None:
    record = parse_appointment(fixture("Appointment", "appt-1"))

    assert record.patient_ref == "pat-1001"
    assert record.location_ref == "loc-endo"
    assert record.service_codes == ("73761001",)
    assert record.status is AppointmentStatus.SCHEDULED
    assert record.duration_minutes == 45
    assert record.start is not None
    assert record.start > NOW


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        ("cancelled", "pat", CancellationReason.PATIENT_REQUESTED),
        ("cancelled", "prov", CancellationReason.FACILITY_OR_STAFF),
        ("cancelled", "xyz", CancellationReason.OTHER),
        ("noshow", None, CancellationReason.PATIENT_NO_SHOW),
    ],
)
def test_cancellations_keep_a_reason(
    status: str, reason: str | None, expected: CancellationReason
) -> None:
    resource = {
        "id": "a",
        "status": status,
        "participant": [{"actor": {"reference": "Patient/p"}}],
        "cancelationReason": {"coding": [{"code": reason}]} if reason else None,
    }

    record = parse_appointment(resource)

    assert record.status is AppointmentStatus.CANCELLED
    assert record.cancellation_reason is expected


def test_appointments_without_a_patient_or_a_known_status_are_refused() -> None:
    with pytest.raises(FhirMappingError, match="no Patient"):
        parse_appointment({"id": "a", "status": "booked", "participant": []})
    with pytest.raises(FhirMappingError, match="status"):
        parse_appointment({"id": "a", "status": "entered-in-error"})


def test_references_by_type() -> None:
    assert reference_id({"reference": "Patient/123"}, "Patient") == "123"
    assert reference_id({"reference": "https://ehr.example/fhir/Patient/9"}, "Patient") == "9"
    assert reference_id({"reference": "Location/1"}, "Patient") is None
    assert reference_id(None, "Patient") is None
