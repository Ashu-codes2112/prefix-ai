# Clinical review checklist

Prefix AI only ever tells a patient what a clinician at their hospital approved. This checklist is for the clinician who approves a prep protocol, and for the clinical lead who signs off the hospital's go-live. Work through it for every protocol, every time a new version is drafted.

## The app never approves anything by itself

- A protocol is approved only when a signed-in person with the approve permission (a clinician or an admin) presses **Approve**. No import, sync, background job or AI step can approve one.
- The database refuses an approved protocol without the approving person and the time (`prep_protocol` check constraint). Once approved, its steps cannot change; an edit makes a new draft.
- The SAMPLE protocols that ship with Prefix AI are starting points, not advice. Approving one asks you to confirm that your team reviewed and adapted every step.
- The only automatic approvals are in the synthetic Demo Hospital's seed, which holds no real patients.
- The AI assistant rephrases and translates approved steps and answers questions only from them. If an answer cannot be grounded in a step, it is not sent. The question goes to a nurse instead.

## For each protocol

**Scope**
- [ ] The procedure type is right (for example colonoscopy, not upper endoscopy).
- [ ] The protocol matches your department's current written policy, and it names that policy's version.
- [ ] Anything another department owns is agreed with them, for example anaesthesia's fasting rules.

**Steps and timing**
- [ ] Every step is one clear action a patient can do at home.
- [ ] The timing of each step is right relative to the procedure time, for example "T-24h" or "T-5 days".
- [ ] Steps that would cancel the procedure if missed are marked **critical**, and steps the patient must confirm are marked as needing confirmation.
- [ ] Fasting, clear-fluid and bowel-prep windows match the policy to the hour.

**Conditions (patient-specific steps)**
- [ ] Anticoagulants and antiplatelets: who stops what, when, and who restarts them. Bridging is decided by the prescriber, not the app.
- [ ] Diabetes on insulin: dose changes, glucose checks, what to do when low.
- [ ] GLP-1 agonists: the hold period your anaesthesia team requires.
- [ ] Contrast allergy and eGFR thresholds, for imaging with contrast.
- [ ] Metal implants or devices, for MRI.
- [ ] Each condition is set on the patient record from a reliable source (the EHR or a clinician), not guessed.

**Wording and languages**
- [ ] Plain language at a reading age a patient can follow. No abbreviations without the words.
- [ ] A fluent clinician has read the Hindi version (and every other language offered). Machine translations are cached for review.
- [ ] Numbers (doses, millilitres, hours) are the same in every language.

**Escalation**
- [ ] The red-flag list (chest pain, breathing difficulty, severe bleeding, fainting or seizure, severe abdominal pain, allergic reaction, stroke signs) fits this procedure. Ask the product team to add any procedure-specific red flags before go-live.
- [ ] The emergency number shown to patients is right for your country (112 in India).
- [ ] A named nurse rota watches the Inbox during the hours reminders go out.

## Before go-live (clinical lead)

- [ ] Every protocol in use was approved by a named clinician through the steps above.
- [ ] Ten or more real patients' timelines were dry-run on paper and checked against the protocol.
- [ ] The nurse team has handled test escalations, including a red flag raised out of hours.
- [ ] The eval report (`docs/eval_report.md`) was reviewed. With the AI assistant switched on, every red-flag case escalates, and answers cite approved steps.
- [ ] Review dates are set, and each protocol is reviewed at least yearly or whenever the policy changes.

Record the reviewer, the date and the protocol version for each item in your hospital's governance system. Prefix AI's audit log records who approved each version and when.
