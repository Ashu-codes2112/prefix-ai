"use server";

import { DATA_CATEGORIES, type IncidentStep } from "@prefix/shared";

import { type ActionResult, refused } from "../action-result";
import { performAction } from "../form-action";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LOCAL_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/;
const SEVERITIES = new Set(["LOW", "MEDIUM", "HIGH", "CRITICAL"]);
const STEPS: ReadonlySet<IncidentStep> = new Set([
  "contained",
  "authority_notified",
  "patients_notified",
  "closed",
]);
const STEP_DONE: Record<IncidentStep, string> = {
  contained: "Marked contained",
  authority_notified: "Recorded: the authority was told",
  patients_notified: "Recorded: patients were told",
  closed: "Incident closed",
};
const NOT_FOUND = refused("That record no longer exists.");
const MAX_MRNS = 1000;

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

/** Consent as the patient gave or withdrew it (in person, by phone, on a form). */
export async function recordConsent(
  patientId: string,
  granted: boolean,
): Promise<ActionResult> {
  if (!UUID.test(patientId)) return NOT_FOUND;
  return performAction(
    `/patients/${patientId}/consent`,
    { method: "POST", body: { granted } },
    granted
      ? "Consent recorded. Reminders will be sent."
      : "Consent withdrawn. No more messages will be sent.",
  );
}

/** Both confirmations travel to the API, which checks them again. */
export async function erasePatient(
  patientId: string,
  form: FormData,
): Promise<ActionResult> {
  if (!UUID.test(patientId)) return NOT_FOUND;
  const acknowledged = form.get("acknowledged") === "on";
  const phrase = text(form, "phrase");
  if (!acknowledged) return refused("Tick the box to confirm.");
  if (!phrase) return refused("Type the erasure phrase to confirm.");
  return performAction(
    `/patients/${patientId}/erasure`,
    { method: "POST", body: { acknowledged, phrase } },
    "The patient's personal data was erased.",
  );
}

export async function setRetention(form: FormData): Promise<ActionResult> {
  const keep = text(form, "keep");
  let days: number | null = null;
  if (keep !== "forever") {
    days = Number(text(form, "days"));
    if (!Number.isInteger(days) || days < 30 || days > 36500) {
      return refused("Choose between 30 and 36,500 days, or keep forever.");
    }
  }
  return performAction(
    "/compliance/retention",
    { method: "PUT", body: { retention_days: days } },
    days === null
      ? "Records are kept until someone erases them."
      : `Records will be erased ${days} days after a patient's last procedure.`,
  );
}

export async function createIncident(form: FormData): Promise<ActionResult> {
  const title = text(form, "title");
  const description = text(form, "description");
  const severity = text(form, "severity");
  const discovered = text(form, "discovered_at");
  const categories = form
    .getAll("data_categories")
    .filter(
      (value): value is string =>
        typeof value === "string" &&
        (DATA_CATEGORIES as readonly string[]).includes(value),
    );
  if (title.length < 3) return refused("Give the incident a short title.");
  if (!description) return refused("Describe what happened.");
  if (!SEVERITIES.has(severity)) return refused("Choose a severity.");
  if (!LOCAL_TIME.test(discovered)) {
    return refused("Enter when the incident was discovered.");
  }
  return performAction(
    "/compliance/incidents",
    {
      method: "POST",
      body: {
        title,
        description,
        severity,
        // Entered in India time, like the rest of the staff app.
        discovered_at: `${discovered}:00+05:30`,
        data_categories: categories,
      },
    },
    "Incident recorded. The 72-hour clock has started.",
  );
}

export async function addIncidentPatients(
  incidentId: string,
  form: FormData,
): Promise<ActionResult> {
  if (!UUID.test(incidentId)) return NOT_FOUND;
  const mrns = text(form, "mrns")
    .split(/[\s,;]+/)
    .map((mrn) => mrn.trim())
    .filter(Boolean);
  if (mrns.length === 0) return refused("List at least one MRN.");
  if (mrns.length > MAX_MRNS) {
    return refused(`Add at most ${MAX_MRNS} MRNs at a time.`);
  }
  return performAction(
    `/compliance/incidents/${incidentId}/patients`,
    { method: "POST", body: { mrns } },
    "Affected patients updated.",
  );
}

export async function advanceIncident(
  incidentId: string,
  step: IncidentStep,
): Promise<ActionResult> {
  if (!UUID.test(incidentId) || !STEPS.has(step)) return NOT_FOUND;
  return performAction(
    `/compliance/incidents/${incidentId}/steps`,
    { method: "POST", body: { step } },
    STEP_DONE[step],
  );
}
