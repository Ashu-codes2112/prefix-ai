# Prefix AI kaise kaam karta hai (Hinglish guide)

Yeh guide simple Hinglish mein har feature samjhati hai. Har feature ke saath:
- **Kya hai:** feature ek line mein;
- **Flow:** step by step kya hota hai;
- **Key files:** code kahan hai;
- **Teacher pooch sakte hain:** ek likely sawaal aur uska short jawab.

Paths repo ke root se hain. Gehri detail ke liye [ARCHITECTURE.md](ARCHITECTURE.md) dekhein.

## Problem kya hai?

Bahut saari surgery, colonoscopy aur scan last minute cancel ho jaate hain, kyunki patient ne prep sahi nahi kiya. Jaise:
- fasting ke time kuch kha liya;
- blood thinner band nahi kiya;
- bowel prep poora nahi kiya;
- ghar le jaane wala koi saath nahi tha.

Ek khaali OR slot hospital ko hazaaron rupaye ka padta hai. Prefix AI patient ko uska apna, doctor-approved prep plan sahi time par yaad dilata hai, check karta hai ki woh follow ho raha hai, aur coordinator ko pehle se batata hai ki kaun risk par hai.

## Sabse zaroori: 4 safety rules

| Rule | Matlab | Code mein kahan |
|---|---|---|
| **a** | AI kabhi apni taraf se medical instruction nahi banata. Sirf approved protocol ke steps. | `services/protocols.py`, `services/assistant/service.py` |
| **b** | Red flag (chest pain, bleeding, behoshi...) ya protocol ke bahar ka sawaal hamesha nurse ke paas. Yeh check LLM se **pehle** hota hai. | `domain/red_flags.py` |
| **c** | LLM ko naam, phone, email, MRN, record id kuch nahi bheja jaata. | `domain/deidentify.py` |
| **d** | Patient data ka har read/write audit log mein, kaun, kya, kab, kyun, kahan se. | `services/audit.py`, `db/models/audit.py` |

**Teacher pooch sakte hain:** "Agar Claude API down ho jaaye to red flag ka kya?"
**Jawab:** Red flag check deterministic hai (keyword aur pattern rules, English aur Hindi dono mein) aur LLM se pehle chalta hai. LLM down ho tab bhi escalation nurse tak jaata hai, aur patient ko 112 call karne ko kaha jaata hai.

---

## 1. Protocol Engine

**Kya hai:** Hospital ke clinician prep protocols likhte hain (steps, timing, conditions jaise "agar blood thinner leta hai"). Clinician approve kare tabhi woh patient ke liye use hote hain.

**Flow:**
1. Coordinator ya clinician draft banata hai.
2. Clinician approve karta hai. SAMPLE protocol ke liye "humne review kiya" confirm karna padta hai.
3. Approve hote hi steps lock ho jaate hain. Database trigger badlav rok deta hai, aur edit karne par nayi draft version banti hai.
4. Sirf APPROVED version se hi patient ka timeline banta hai.

**Key files:** `apps/api/src/prefix_api/services/protocols.py`, `domain/conditions.py` (safe JSON condition language, koi `eval` nahi), migration `0003` (triggers), `apps/web/src/app/dashboard/protocols/`.

**Teacher pooch sakte hain:** "Kya app khud protocol approve kar sakta hai?"
**Jawab:** Nahi. Approve sirf signed-in insaan (clinician/admin) kar sakta hai. Database bhi approver ke bina APPROVED status reject karta hai. Checklist [CLINICAL_REVIEW_CHECKLIST.md](CLINICAL_REVIEW_CHECKLIST.md) mein hai.

## 2. Smart Prep Timeline aur reminders

**Kya hai:** Protocol aur procedure ka time milakar har patient ke liye exact time wale steps banta hai, jaise "clopidogrel band karein: 5 din pehle" ya "sirf clear liquid: 24 ghante pehle".

**Flow:**
1. Appointment book hota hai.
2. `build_timeline` patient ki conditions (anticoagulant, insulin, GLP-1, eGFR...) ke hisaab se steps chunta hai.
3. Har step ka `send_at` time save hota hai.
4. Worker har minute due reminders bhejta hai: SMS, WhatsApp ya email. Patient ki language mein, cached translation ke saath.
5. Consent nahi hai to message skip hota hai.

**Key files:** `services/timeline.py`, `services/dispatch.py`, `services/messaging/providers.py` (Twilio SMS/WhatsApp), `apps/worker/src/prefix_worker/tasks.py`.

**Teacher pooch sakte hain:** "Ek reminder do baar to nahi chala jaayega?"
**Jawab:** Nahi. Dispatcher event ko pehle `SENDING` claim karta hai, aur database mein unique index hai: ek timeline event par ek hi outbound message.

## 3. Readiness check-ins

**Kya hai:** Zaroori steps par patient se poocha jaata hai, jaise "Prep solution shuru kiya?" ya "Escort arrange hua?". Jawab YES/NO/HELP (Hindi mein "haan", "nahi", "madad" bhi).

**Flow:**
1. Reminder ke saath check-in jaata hai.
2. Patient SMS, WhatsApp ya portal se jawab deta hai.
3. Inbound webhook Twilio signature verify karta hai, phir jawab ko pending check-in se jodta hai.
4. NO ya HELP par coordinator task aur staff notification banta hai. Free text kabhi guess nahi hota, insaan padhta hai.
5. "STOP" ya "बंद करो" bhejne par consent withdraw ho jaata hai.

**Key files:** `services/inbound.py`, `domain/replies.py`, `services/check_ins.py`, `routers/webhooks.py`.

**Teacher pooch sakte hain:** "Koi fake SMS webhook bhej de to?"
**Jawab:** Live mode mein har inbound request ka Twilio HMAC signature check hota hai. Galat signature wali request reject ho jaati hai.

## 4. AI Prep Assistant (Claude)

**Kya hai:** Patient apne prep ke baare mein sawaal pooche, to assistant sirf uske apne approved steps se jawab deta hai, English ya Hindi mein, aur batata hai kaunse step se jawab liya.

**Flow:**
1. Red-flag check (LLM se pehle). Red flag mile to fixed message ("112 call karein") aur nurse escalation.
2. De-identification: naam, phone, email, ids hata diye jaate hain.
3. Patient ke approved steps context mein jaate hain. Claude se structured JSON maanga jaata hai: covered, answer, cited_steps.
4. Checks: cite kiye steps wahi hone chahiye jo diye gaye, aur numbers bhi unhi steps se. Fail hua to nurse.
5. `ANTHROPIC_API_KEY` nahi hai to "nurse hand-off mode": har sawaal nurse ke inbox mein, aur staff app mein badge dikhta hai.

**Key files:** `services/assistant/service.py`, `services/assistant/llm.py`, `domain/deidentify.py`, `domain/red_flags.py`, `evals/run_assistant_eval.py` (`make eval` likhta hai `docs/eval_report.md`).

**Teacher pooch sakte hain:** "LLM hallucinate kare to?"
**Jawab:** Teen layer:
- Prompt mein sirf protocol hai.
- Jawab ko diye hue step cite karne padte hain.
- Code check karta hai ki jawab ke numbers unhi steps mein hain.

Koi check fail ho to jawab patient ko nahi jaata, nurse ko jaata hai.

## 5. Cancellation risk score (v1 rules, v2 ML)

**Kya hai:** Har upcoming procedure ka 0–100 score aur LOW/MEDIUM/HIGH level, saath mein "kyun", jaise "3 prep steps confirm nahi hue".

**Flow:**
1. Worker har 15 minute facts ikattha karta hai: unconfirmed steps, problem replies, escalations, conditions, procedure kitni der mein hai.
2. **v1 (default):** haath se likhe rules, har rule points jodta hai.
3. **v2 (`RISK_MODEL=v2`):** calibrated logistic regression. JSON model file se padhta hai (pickle nahi). Har feature ka contribution reason ke roop mein dikhta hai.
4. HIGH hone par coordinator ke liye ek task khulta hai. Score ke saath model version save hota hai.
5. Dashboard par "Model: v1 rules" ya "Model: v2 ML" badge.

**Key files:** `domain/risk.py`, `domain/risk_v2.py`, `services/risk.py`, `ml/synthetic.py`, `ml/train.py`, `risk_models/lr-v2.json`, [model_report.md](model_report.md).

**Teacher pooch sakte hain:** "v2 ka AUC 0.789 hai, to kya yeh real hospital mein bhi itna accurate hoga?"
**Jawab:** Nahi keh sakte. v2 synthetic data par train hua hai, aur report ki pehli line yahi kehti hai. Isi liye v1 default hai. Hospital ke apne outcomes par refit karke, held-out data par v1 se better nikle, tabhi switch karna chahiye.

## 6. Coordinator dashboard

**Kya hai:** Agle 72 ghante ke procedures, sabse kam ready pehle. Har row mein readiness ring, risk aur reasons.

**Flow:**
1. Row kholne par patient sheet khulti hai. Tabs: Overview, Prep timeline, Messages, Requests, Activity, Privacy.
2. One-click actions: call log, message, step confirm, reschedule, slot release, failed reminders resend.
3. Top bar ki bell patient ki har nayi cheez (problem, sawaal, reschedule) 15 second mein dikhati hai. Urgent wali red mein.

**Key files:** `services/dashboard.py`, `apps/web/src/app/dashboard/page.tsx`, `coordinator/patient-sheet.tsx`, `services/staff_notifications.py`.

**Teacher pooch sakte hain:** "Nurse aur coordinator ko alag permissions kaise milti hain?"
**Jawab:** `domain/permissions.py` mein role → permission table hai. Router pehle check karta hai, phir service dobara check karti hai (defence in depth). Web sirf wohi buttons dikhata hai jo role kar sakta hai.

## 7. Waitlist backfill

**Kya hai:** Koi slot release ho ya high risk ho, to waitlist mein se aise patient dhoondho jo time par prep kar sakein, aur unhe slot offer karo.

**Flow:**
1. Slot release hota hai.
2. Eligible candidates: same procedure, date window, kitne din pehle prep chahiye.
3. Ek waqt par ek offer, aur DB locking taaki double booking na ho.
4. Accept karne par naya appointment aur timeline banta hai.

**Key files:** `services/waitlist.py`, `apps/web/src/app/dashboard/waitlist/`.

**Teacher pooch sakte hain:** "Do coordinator ek saath same slot de dein to?"
**Jawab:** Partial unique index ek slot par ek hi accepted offer allow karta hai. Row lock (`FOR UPDATE`) race condition rokta hai.

## 8. Analytics / ROI

**Kya hai:** Cancellation rate pehle vs ab, bachaye gaye slots, theatre time, revenue recovered, aur step-wise compliance. CSV export bhi.

**Flow:**
1. Hospital ka baseline rate stored hai (Demo Hospital ka 14%).
2. Chosen date range mein procedures aur cancellations gine jaate hain.
3. Prep-related cancellations alag dikhte hain.

**Key files:** `services/analytics.py`, `apps/web/src/app/dashboard/analytics/`.

**Teacher pooch sakte hain:** "Revenue saved kaise nikalte ho?"
**Jawab:** Baseline rate se expected cancellations nikaalte hain, actual cancellations ghatate hain, aur fark ko procedure ki duration aur OR cost per minute se guna karte hain. Baseline set na ho to kuch "saved" claim nahi hota.

## 9. Integrations: CSV, FHIR R4, webhooks

**Kya hai:** Hospital ke EHR se patients, conditions aur appointments aate hain. Appointment status badalne par hospital ko signed webhook jaata hai.

**Flow (CSV):** File → dry run report → sab rows valid ho tabhi import. Error message mein value kabhi nahi, sirf line aur column.

**Flow (FHIR):**
1. Worker har 15 minute `_lastUpdated=gt<cursor>` se sirf badla hua data padhta hai.
2. Network errors, 429 aur 5xx par retry hota hai.
3. Resource se kya banta hai:
   - Patient → patient record;
   - MedicationStatement → anticoagulant, insulin, GLP-1 flags;
   - Observation → eGFR (sirf creatinine ho to CKD-EPI 2021 formula se);
   - AllergyIntolerance → contrast allergy;
   - Appointment → booking.
4. Flags sirf ON hote hain, OFF karna insaan ka faisla hai. EHR se consent nahi maana jaata.

**Flow (webhooks):** Appointment status badla → usi transaction mein outbox row → worker HMAC-SHA256 signature ke saath POST karta hai, fail hone par backoff se retry.

**Key files:** `domain/fhir.py`, `services/fhir/sync.py`, `services/fhir/client.py`, `services/webhooks.py`, `services/imports.py`, `scripts/fhir_mock_server.py`, [INTEGRATIONS.md](INTEGRATIONS.md). UI: Admin → Integrations.

**Teacher pooch sakte hain:** "Outbox pattern kyun? Seedha HTTP call kyun nahi?"
**Jawab:** Seedha call karein aur transaction rollback ho jaaye, to galat khabar chali jaayegi. Aur receiver down ho to change kho jaayega. Outbox row change ke saath hi commit hoti hai, to jo save hua wahi bheja jaata hai, aur baad mein retry ho sakta hai.

## 10. Compliance (HIPAA aur India DPDP Act)

**Kya hai:** Real hospital ke go-live ke liye zaroori kaam.

**Flow:**
- **Onboarding:** `uv run prefix-admin create-org` aur `create-first-admin`. Password hidden prompt par do baar maanga jaata hai, kabhi log nahi hota.
- **Consent:** har grant aur withdrawal `consent_record` mein, jo append-only hai (trigger se). Withdraw hone par koi message nahi jaata.
- **Export:** admin patient ka saara data JSON (poora) ya PDF (padhne ke liye) mein download karta hai. Audit hota hai.
- **Erasure:** admin ko tick karna padta hai aur "ERASE 01A1E942" type karna padta hai (double confirm). Naam, contact, free text hat jaate hain; ids, dates aur outcomes bina pehchaan ke rehte hain. Backups mein purani copy expire hone tak rehti hai (honest limit, [COMPLIANCE.md](COMPLIANCE.md) mein likha hai).
- **Retention:** hospital din set karta hai, pehle dry run dikhta hai, aur raat 02:15 par purge chalta hai. Upcoming care ya open incident wale patients bach jaate hain.
- **Breach:** incident record, affected patients by MRN, 72 ghante ka countdown, aur authority ke liye report (sirf counts, pehchaan nahi). Process [BREACH_RESPONSE.md](BREACH_RESPONSE.md) mein.

**Key files:** `services/consent.py`, `services/data_rights.py`, `services/export_pdf.py`, `services/retention.py`, `services/incidents.py`, `cli.py`. UI: patient sheet ka Privacy tab, aur Admin → Compliance.

**Teacher pooch sakte hain:** "Erase kiya patient FHIR sync se wapas to nahi aa jaayega?"
**Jawab:** Nahi. Erasure EHR id ki jagah uska keyed hash (HMAC) rakhta hai. Sync use pehchaan leta hai aur skip karta hai, aur asli id wapas nahi banti.

## 11. Patient portal

**Kya hai:** Mobile-first portal jahan patient apne steps dekhta hai, "Ho gaya" ya "Problem hai" dabata hai, sawaal poochta hai, aur reschedule maangta hai.

**Flow:**
1. Staff magic link bhejta hai. Token URL ke `#` fragment mein hota hai, isliye server logs mein nahi aata.
2. Link ek hi baar chalta hai, phir 12 ghante ka session milta hai.
3. Portal English aur Hindi mein hai, bade buttons ke saath, aur emergency number hamesha dikhta hai.

**Key files:** `services/patient_access.py`, `services/portal.py`, `apps/web/src/app/p/`.

**Teacher pooch sakte hain:** "Link leak ho jaaye to?"
**Jawab:** Link single-use hai aur jaldi expire hota hai. DB mein sirf token ka SHA-256 hash hai. Patient erase ho jaaye to purane sessions bhi turant band.

## 12. Security ki baaki baatein

- **Staff login:** argon2id password, 15 minute ka JWT, rotating refresh token. Purana token dobara use hua to poori family revoke.
- **Encryption:** naam, phone, email, MRN aur free text AES-256-GCM se encrypt. Search ke liye HMAC blind index.
- **Rate limits:** Redis mein sliding window. Redis down ho to process ke andar fallback.
- **CSP:** har page request par naya nonce. Bina nonce koi inline script nahi chalti.
- **Tenant isolation:** organization hamesha token se aati hai, body se kabhi nahi.

**Key files:** `core/security.py`, `core/crypto.py`, `core/rate_limit.py`, `apps/web/src/proxy.ts`, `apps/web/src/lib/csp.ts`.

**Teacher pooch sakte hain:** "Database chori ho jaaye to kya milega?"
**Jawab:** Personal fields encrypted ciphertext hain, aur keys environment ya secret manager mein hain, DB mein nahi. Keys ke bina naam ya phone nahi padh sakte.

## 13. Guest demo

**Kya hai:** "Try as Guest" se bina password Demo Hospital (synthetic patients) mein login. "View patient demo" se ussi hospital ka ek patient portal khulta hai.

**Flow:**
1. Guest patient bankar problem report karta hai, red-flag sawaal poochta hai aur reschedule maangta hai.
2. Staff dashboard ki bell, Inbox aur Requests mein yeh 15 second mein dikhta hai.
3. Guest request approve ya decline bhi kar sakta hai.
4. Demo Hospital kabhi asli message nahi bhejta, sirf console log.

**Key files:** `seed.py`, `services/demo.py`, `routers/demo.py`, `apps/web/e2e/demo.spec.ts`.

**Teacher pooch sakte hain:** "Demo mein kisi ka asli phone par SMS to nahi chala jaayega?"
**Jawab:** Nahi. Dispatch Demo Hospital ke liye hamesha console provider use karta hai. Ek hi exception hai: admin ka "Send test reminder to my phone", jo admin-only hai, ghante mein 3 baar tak, aur audited.

---

## Testing kaise hoti hai

- **pytest:** API aur worker. Har safety rule ke dedicated tests hain, jaise `test_safety_rules.py` aur `test_assistant.py`.
- **Vitest:** web components.
- **Playwright:** desktop aur mobile, axe accessibility scans ke saath, poora guest demo flow bhi.
- **CI (GitHub Actions):** ruff, mypy, ESLint, tsc, Prettier, saare tests, `docker compose build` aur smoke test. Green ho tabhi merge.

**Teacher pooch sakte hain:** "Kaise pata ki LLM ko PHI nahi ja raha?"
**Jawab:** Ek end-to-end test asli Claude client ko fake SDK par chalata hai. Sawaal mein naam, phone, email aur ids daalta hai, aur check karta hai ki SDK tak inme se kuch nahi pahuncha.
