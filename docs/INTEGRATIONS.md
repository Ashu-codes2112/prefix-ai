# Integrations

Two ways in: CSV import, the quick start for a hospital with no interface to its scheduling system, and an incremental FHIR R4 sync from the hospital's EHR. Appointment changes go back out as signed webhooks.

## CSV import

Coordinators and admins import from **Import records** in the dashboard, or with `POST /imports/patients` and `POST /imports/appointments`.

- The first line is the header. A header matches a field when its name is the field name, ignoring case, spaces and hyphens. `mapping` in the request maps other header names to fields, for example `{"DOB": "date_of_birth"}`.
- Columns that match no field are ignored and listed in the report, so a typo shows up rather than disappearing.
- A file is capped at 1 MB and 500 rows. Split a larger file.
- `dry_run` defaults to `true`. A dry run checks every row and saves nothing. A real import (`dry_run: false`) saves only when every row is valid, so a file is never half imported.

### Patients

| Column | Required | Notes |
|---|---|---|
| `first_name`, `last_name` | yes | Up to 100 characters each. |
| `date_of_birth` | yes | `YYYY-MM-DD`. |
| `phone` | no | E.164, with country code, e.g. `+919812300001`. |
| `email` | no | |
| `mrn` | no | Must be unique in the hospital. |
| `preferred_language` | no | e.g. `en`, `hi`. |
| `preferred_channel` | no | `SMS`, `WHATSAPP`, `EMAIL` or `PORTAL`. |
| `consent_to_message` | no | `yes` or `no`. Defaults to `no`, so nothing is messaged without consent. |
| `on_anticoagulant`, `diabetic_on_insulin`, `on_glp1_agonist`, `has_contrast_allergy`, `has_metal_implant` | no | `yes` or `no`. |
| `egfr_value` | no | A number from 0 to 200. |

### Appointments

| Column | Required | Notes |
|---|---|---|
| `patient_mrn` or `patient_phone` | one of them | Must match exactly one existing patient. |
| `facility` | yes | The facility name, ignoring case. |
| `procedure_code` | yes | The procedure code, ignoring case, e.g. `COLONOSCOPY`. |
| `scheduled_at` | yes | ISO 8601 with its UTC offset, e.g. `2026-11-02T09:00:00+05:30`. Must be in the future. |
| `arrival_at` | no | Same format as `scheduled_at`. |
| `duration_minutes` | no | 5 to 1440. Defaults to the procedure's duration. |

An appointment is booked by the same service as a typed booking, so the procedure's approved protocol builds the prep timeline, and the usual reminders follow.

## Safety properties

- **Same rules as a typed entry.** Every row goes through the patient and appointment services. Consent, audit, protocol and timeline rules all apply.
- **No values in error messages.** A report says `line 3, column email, is not valid`. It never repeats what was typed, so a report or a screenshot of one does not spread patient data.
- **Audited.** Each created record is audited as usual. A committed import also writes one `import` audit row with the record count. Each existing patient matched to an appointment is audited as a read.
- **Permissions.** Patient imports need the patient write permission and appointment imports need the appointment write permission. Viewers are refused.
- **Consent is recorded as given.** The consent timestamp is the time of import. The source of the consent, such as a signed form, is not stored, so check it before importing (see `docs/COMPLIANCE.md`, Phase 12).

## FHIR R4 sync

An admin sets it up on **Admin → Integrations**:
- the server's base URL, and a bearer token if it needs one (stored encrypted, never shown again);
- which EHR service codes are which procedures, e.g. `73761001 = COLONOSCOPY`;
- the facility for synced bookings.

The worker syncs every 15 minutes (`prefix_worker.fhir.sync`), and **Sync now** runs one at once. The page shows:
- the connection's health: healthy, failing, no recent sync, not synced yet, or off;
- the last error, if there is one;
- how far each resource type has been read;
- the last ten runs, with counts and the reasons any records were skipped.

### What is read, and what it becomes

| FHIR resource | Read | Becomes |
|---|---|---|
| `Patient` | official name, `birthDate`, phone and e-mail from `telecom`, the `MR` identifier, `communication` language, `gender` | A patient matched by the EHR's id, then by MRN. Created or updated. **Gender is used only to compute eGFR and is never stored.** A synced patient has **not consented to messages** until staff record it: consent is never inferred from the EHR. |
| `MedicationStatement` (active, intended, on hold) | medication name or ATC code | `on_anticoagulant` (warfarin, acenocoumarol, DOACs, heparins, fondaparinux, and the antiplatelets clopidogrel, prasugrel and ticagrelor; not aspirin), `diabetic_on_insulin`, `on_glp1_agonist` (semaglutide, liraglutide, dulaglutide, exenatide, lixisenatide, tirzepatide). |
| `Observation` (final, amended, corrected) | LOINC eGFR codes, or serum creatinine in mg/dL or µmol/L | `egfr_value`. Creatinine alone gives eGFR by the race-free CKD-EPI 2021 equation, from the patient's age and sex. The newest result wins; an older one never replaces it. An unknown unit is never guessed. |
| `AllergyIntolerance` (active, not refuted) | SNOMED codes or names for iodinated or gadolinium contrast | `has_contrast_allergy`. |
| `Appointment` | status, `start`, duration, patient, `serviceType` code, Location | A booking through the same service as a coordinator's, so only an approved protocol can build the prep plan (safety rule a). EHR cancellations, no-shows, completions and new times follow. A booking already closed here is not reopened. |

**Conditions are only ever switched on.** A sync can set `on_anticoagulant` but never clears it, even when the EHR stops the medicine: removing a condition from a prep plan is a person's decision. The medication and allergy lists live in `apps/api/src/prefix_api/domain/fhir.py`. The hospital's clinicians must review them, as they review protocols (see `docs/CLINICAL_REVIEW_CHECKLIST.md`).

### How a sync behaves

- **Incremental.** Each resource type is searched with `_lastUpdated=gt<cursor>`, oldest first, following the Bundle's `next` links. A cursor moves only past a type that was read to the end, so a failed run starts again where it stopped.
- **Retries.** Network errors, 429 and 5xx are retried three times, with backoff and `Retry-After`. Other answers (for example 401) fail the run at once. Errors name the status code, never a response body.
- **One bad record does not stop the rest.** Each record is applied in its own savepoint. A skipped record is reported by reason and count, for example "Appointment: no procedure type is mapped for its service code (3)", never by value.
- **Erased patients stay erased.** Erasure keeps a keyed hash of the EHR's id instead of the id itself, so a later sync recognises the patient and skips them instead of bringing their data back.
- **Audited** as the system job `fhir_sync` (purpose `ehr_sync`), field names only.

### Trying it without an EHR

```bash
uv run python scripts/fhir_mock_server.py      # http://127.0.0.1:8090/fhir
```

It serves synthetic, recorded-style fixtures (`apps/api/src/prefix_api/fhir_fixtures/fixtures.json`). Point the Integrations page at `http://127.0.0.1:8090/fhir`, map `73761001 = COLONOSCOPY`, choose a facility and press **Sync now**. The tests run the same fixtures in process (`tests/test_fhir_sync.py`).

## Outbound webhooks

Every appointment status change is posted to each enabled endpoint. That includes scheduled to cancelled, at risk or completed, from any part of the app or from the FHIR sync.

- **Outbox.** The change and its delivery rows are saved in one transaction, so nothing is sent that was not saved, and nothing is lost while a receiver is down. The worker posts due deliveries every minute (`prefix_worker.webhooks.deliver`) and retries after 1 min, 5 min, 30 min, 2 h, 6 h and 12 h, then gives up.
- **Payload.** Ids, statuses and times only, never names, contact details or notes:

  ```json
  {"id": "<delivery id>", "type": "appointment.status_changed", "occurred_at": "2026-11-19T10:02:00+00:00",
   "data": {"appointment_id": "...", "external_id": "appt-1", "status": "CANCELLED",
            "previous_status": "SCHEDULED", "scheduled_at": "...", "cancellation_reason": "NO_ESCORT"}}
  ```
- **Headers.** `X-Prefix-Event`, and `X-Prefix-Delivery` (the same on every retry, so ignore repeats). `X-Prefix-Signature: t=<unix seconds>,v1=<hex>`, where `v1` is HMAC-SHA256 of `"<t>.<raw body>"` under the endpoint's secret. The secret is shown once, when the endpoint is added.
- **Verifying.** Recompute the HMAC over the raw body, compare in constant time, and reject a `t` more than five minutes old. `prefix_api.services.webhooks.verify_signature` does exactly this.
- The Demo Hospital never sends webhooks. In production, endpoints and the FHIR server must use https.
