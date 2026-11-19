# Breach response

What to do when personal data in Prefix AI may have been exposed, lost or changed without authority. It supports the hospital's own incident process and does not replace it. It is not legal advice: the hospital's privacy officer decides what the law requires in each case.

## The deadlines

| Law | Who must be told | When |
|---|---|---|
| India DPDP Act 2023 and the DPDP Rules | The Data Protection Board of India and each affected person | Without delay. The Rules ask for a detailed report to the Board **within 72 hours** of becoming aware. |
| HIPAA Breach Notification Rule (US) | Affected individuals; HHS; the media when more than 500 residents of a state are affected | Without unreasonable delay, no later than **60 days** after discovery. Breaches of fewer than 500 people can be reported to HHS within 60 days after the end of the calendar year. A business associate tells the covered entity. |

Prefix AI tracks the strictest of these, **72 hours from discovery**, on every incident.

## In the app (admins)

Open **Compliance → Incidents**.

1. **Record the incident** as soon as you suspect one. Give it a short title without patient data (for example "Exported list e-mailed to the wrong address"), what happened, the severity, when it was discovered, and the kinds of data involved (identity, contact, health, messages, credentials). The 72-hour deadline starts at the discovery time you enter.
2. **Add affected patients** by MRN, one per line. The incident stores the link, not the MRN. Patients on an open incident are kept from the nightly retention purge, which acts as a legal hold.
3. **Contain it**, then mark it *contained*. For example: revoke a lost device's sessions, rotate keys (`PII_ENCRYPTION_KEYS`, `JWT_SECRET`), disable the account.
4. **Download the report for the authority.** It has the facts, the timeline, the deadline, whether it was met and the number of people affected. It never contains their identities.
5. **Mark the authority notified** when you have sent it. The time is recorded against the deadline.
6. **Tell the patients** through your usual channel, then mark *patients notified*. Use the patient export (admins, on the patient sheet) for each person's own copy if they ask for it.
7. **Close** the incident when the review is done.

Every step is audited: who did it and when.

## Finding out what happened

- `audit_log` records every read and write of patient data: who, what, the action, when, why and from where. Query it by `patient_id`, `actor_id` and `occurred_at`. It is append-only.
- Sign-ins, failed sign-ins and refresh-token reuse are audited too. Token reuse revokes the whole login family.
- The API log holds request method, path, status and timing, never bodies.

## What to tell the authority and the patients

Cover:
- what happened, and when it was discovered;
- what kinds of data were involved, and how many people;
- the likely consequences for them;
- what has been done and what they can do (for example, be wary of calls asking for their details);
- a contact person.

## After the incident

- Write down the cause and the fix, and update this file if the process changed.
- If keys were rotated, re-encrypting older values is a manual step today (see `docs/DEPLOYMENT.md`).
