# Compliance

This is an engineering summary of how Prefix AI meets the HIPAA (US) and India DPDP Act 2023 duties it is built for, with the gaps stated. It is not legal advice. A hospital deploying Prefix AI still needs its own privacy officer, its own agreements with its vendors, and a review of this file against its own obligations.

## Where personal data lives

| Data | Where | Protected by |
|---|---|---|
| Names, date of birth, phone, email, MRN | `patient` table | AES-256-GCM per field, with a key ID so keys can rotate. Lookups use HMAC blind indexes, so phone, email and MRN can be matched without storing them in clear. |
| Free text from patients (check-in replies, escalation summaries, messages) | `check_in`, `escalation`, `message` tables | The same field encryption. |
| Prep plans and approved protocols | `prep_protocol`, `prep_step` | Not personal data. They are clinician content, so they are stored in clear. |
| Who did what, when | `audit_log` | Append-only: a database trigger rejects `UPDATE`, `DELETE` and `TRUNCATE`. |
| Consent history | `consent_record` | Append-only by trigger. A row goes only when its patient record is deleted outright (the demo reset). |
| Security incidents | `security_incident`, `security_incident_patient` | The description is field-encrypted. Affected patients are linked by id; their MRNs are not stored there. |

## Requirements

| Requirement | How it is met | Status |
|---|---|---|
| **Consent capture** | `consent_to_message` is required at registration, CSV import and update. Every grant is kept in `consent_record` with how it arrived (staff, file import), who recorded it and when. Nothing is messaged without consent. | Met. The signed form itself stays in the hospital's system. |
| **Consent withdrawal** | Staff withdraw it on the patient's Privacy tab, and a patient can reply STOP (or बंद करो) to any message. Each withdrawal is kept in the history and audited. Dispatch checks consent again before every send, so pending reminders are skipped. | Met. |
| **Purpose limitation** | Every audit row records a purpose (for example `prep_reminder`, `waitlist_backfill`, `bulk_import`). The AI assistant sees only the patient's own approved protocol. | Met. |
| **Data minimisation** | Prompts to the AI assistant carry no names, phone numbers, e-mails or record numbers. Console and log output masks recipients. Logs record request method, path, status and timing, never bodies or query strings. A test checks that a personal e-mail never reaches the logs. | Met. Request paths contain record IDs (UUIDs), which are not names or contact details but are still identifiers. |
| **Audit logs** | Every read and write of patient data is audited in the service layer, not in the routers. A backstop middleware flags a patient-data route that writes no audit row. | Met. Audit rows from a request that fails are rolled back with it, the same as the data change they describe. |
| **Access control** | Role-based permissions checked in the router and again in the service. Every query filters on the tenant. Tests cover cross-tenant access. Login attempts are limited per account (10 failed attempts in 15 minutes) and per client address. Export, erasure, retention and incidents are for admins only. | Met. Limits are shared through Redis across API processes. |
| **Encryption in transit** | The API and web app expect TLS at the edge. Production responses send `Strict-Transport-Security`. | Met when deployed behind TLS. TLS is the operator's to set up. |
| **Encryption at rest** | Personal fields are encrypted in the application. Database volumes should be encrypted by the operator's platform. | Field encryption met. Volume encryption is an operator requirement. |
| **Retention and erasure** | Each hospital sets `retention_days` on the Compliance page. Every night the worker erases patients whose last procedure is older than that, unless they have care still to come or are on an open incident. The page shows a dry run first. Admins can also erase one patient on request: a tick and the patient's own phrase confirm it. | Met for the live database. See "Erasure and backups" below. |
| **Breach notification support** | Admins record an incident, add affected patients by MRN, follow the 72-hour deadline, and download a report for the authority with facts, timeline and counts. The process is in [BREACH_RESPONSE.md](BREACH_RESPONSE.md). | Met. The hospital names the person who runs it. |
| **Subject access** | Admins download everything held about a patient from the Privacy tab, as JSON (complete) or PDF (readable). Each export is audited. | Met. The PDF shows Latin script only; text in Hindi points to the JSON file, which keeps every character. |
| **Security headers** | The API and web app send `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, a content security policy and, in production, HSTS. The web policy allows scripts only with a fresh nonce per request. | Met. Styles still allow inline values. |

## Erasure and backups

Erasure removes, from the live database:
- the patient's names, date of birth, phone, e-mail and MRN, and their lookup hashes;
- message bodies, check-in replies, assistant questions and answers, and escalation and notification summaries;
- cancellation notes and unused portal links.
- the EHR's own id for the patient, replaced by a keyed hash so that a FHIR sync recognises the patient and does not bring their data back.

Their portal sessions stop working at once. What stays has no identity: ids, dates, statuses and step outcomes, so analytics and the audit trail still add up. The audit log never held patient data, so it is kept whole as the record of what was done.

Values are encrypted with the organisation's key (AES-256-GCM). Overwriting them removes the live copy, but backups taken earlier still hold the old ciphertext, readable by whoever holds the key, until those backups expire. Keep backup retention short and written down (for example 35 days), and say so in the privacy notice.

Making backups unreadable too (crypto-shredding) needs a separate key for each patient, kept in a key-management service and destroyed on erasure. That is not built.

## Clinical safety

Protocols are only ever approved by a named person. See [CLINICAL_REVIEW_CHECKLIST.md](CLINICAL_REVIEW_CHECKLIST.md) for what a clinician checks before approving one, and what the clinical lead signs off before go-live.

## Vendors

Prefix AI sends personal data to a messaging provider (Twilio for SMS and WhatsApp, SMTP for e-mail) only when a reminder is due and the patient has consented. It sends the AI assistant's prompt to the Claude API with names, phone numbers, e-mail addresses and record numbers removed. A patient's own question is passed on as written, so the assistant's answers must still be reviewed and red flags are escalated before the model is called. Each vendor relationship needs its own agreement, including a business associate agreement with a HIPAA covered entity. The hospital signs those, not Prefix AI.

## Before go-live

1. Create the hospital and its first admin with `prefix-admin` (see `docs/DEPLOYMENT.md`).
2. Set the retention period on the Compliance page, after reading the dry run. Write down the backup retention period alongside it.
3. Name the person who runs [BREACH_RESPONSE.md](BREACH_RESPONSE.md), and rehearse it once.
4. Record how consent was obtained for every patient imported from a file.
5. Have the clinical lead work through [CLINICAL_REVIEW_CHECKLIST.md](CLINICAL_REVIEW_CHECKLIST.md).
6. Sign the vendor agreements listed above (business associate agreements in the US).
7. Turn off guest login (`ENABLE_GUEST_LOGIN=false`) and check the production settings in `docs/DEPLOYMENT.md`.
