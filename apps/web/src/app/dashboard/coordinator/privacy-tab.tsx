"use client";

import type { ConsentRecord, ConsentStatus } from "@prefix/shared";
import { Download, FileText, ShieldOff, UserX } from "lucide-react";

import { ActionButton } from "@/components/action-runner";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { erasePatient, recordConsent } from "../compliance/actions";

const DAY = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "Asia/Kolkata",
});

const SOURCE: Record<ConsentRecord["source"], string> = {
  STAFF: "recorded by staff",
  IMPORT: "from a file import",
  SMS_KEYWORD: "patient replied STOP",
  ERASURE: "data erased",
};

/** What the privacy tab may show and do. Each is decided on the server. */
export interface Privacy {
  consent: ConsentStatus | null;
  /** Only for admins: the phrase that confirms an erasure. */
  erasurePhrase: string | null;
  canRecordConsent: boolean;
}

function download(patientId: string, format: "json" | "pdf"): string {
  const query = new URLSearchParams({ kind: "patient", id: patientId, format });
  return `/dashboard/compliance/download?${query.toString()}`;
}

export function PrivacyTab({
  patientId,
  privacy,
}: {
  patientId: string;
  privacy: Privacy;
}) {
  const { consent, erasurePhrase } = privacy;
  if (!consent) {
    return (
      <p className="p-5 text-sm text-muted-foreground">
        Your role cannot see this patient&apos;s consent record.
      </p>
    );
  }
  if (consent.erased) {
    return (
      <div className="flex flex-col gap-2 p-5">
        <p className="flex items-center gap-2 font-medium">
          <UserX className="size-4 text-muted-foreground" aria-hidden />
          This patient&apos;s personal data was erased
        </p>
        <p className="text-sm text-muted-foreground">
          Names, contact details, messages and free text are gone. Dates and
          outcomes stay, without identity, for the hospital&apos;s statistics
          and audit trail.
        </p>
      </div>
    );
  }

  const on = consent.consent_to_message;
  return (
    <div className="flex flex-col gap-6 p-5">
      <section
        aria-labelledby="consent-heading"
        className="flex flex-col gap-3"
      >
        <div className="flex flex-wrap items-center gap-2">
          <h3
            id="consent-heading"
            className="text-sm font-semibold text-muted-foreground"
          >
            Consent to messages
          </h3>
          <Badge variant={on ? "ready" : "attention"}>
            {on ? "Messages on" : "Messages off"}
          </Badge>
        </div>
        <p className="text-sm">
          {on
            ? "Prep reminders and check-ins go out by the patient's chosen channel."
            : "Nothing is sent to this patient until consent is recorded."}
          {consent.consent_timestamp
            ? ` Since ${DAY.format(new Date(consent.consent_timestamp))}.`
            : ""}
        </p>
        {privacy.canRecordConsent ? (
          on ? (
            <ActionButton
              variant="outline"
              className="self-start"
              action={() => recordConsent(patientId, false)}
              confirm={{
                title: "Withdraw consent?",
                description:
                  "Pending reminders are skipped from now on. Record consent again if the patient changes their mind.",
                confirmLabel: "Withdraw consent",
                tone: "danger",
              }}
            >
              <ShieldOff aria-hidden />
              Withdraw consent
            </ActionButton>
          ) : (
            <ActionButton
              className="self-start"
              action={() => recordConsent(patientId, true)}
              confirm={{
                title: "Record consent?",
                description:
                  "Only when the patient agreed to be messaged: in person, by phone or on a signed form.",
                confirmLabel: "Record consent",
              }}
            >
              Record consent
            </ActionButton>
          )
        ) : null}
        {consent.history.length > 0 ? (
          <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
            {consent.history.slice(0, 5).map((record) => (
              <li key={`${record.recorded_at}-${String(record.granted)}`}>
                {record.granted ? "Given" : "Withdrawn"},{" "}
                {SOURCE[record.source]},{" "}
                {DAY.format(new Date(record.recorded_at))}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {erasurePhrase ? (
        <section
          aria-labelledby="data-rights-heading"
          className="flex flex-col gap-3"
        >
          <h3
            id="data-rights-heading"
            className="text-sm font-semibold text-muted-foreground"
          >
            Data rights (administrators)
          </h3>
          <p className="text-sm">
            Give the patient a copy of everything held about them, or erase
            their personal data when they ask. Both are recorded in the audit
            log.
          </p>
          <div className="flex flex-wrap gap-2">
            <a
              className="inline-flex h-9 pressable items-center gap-1.5 rounded-lg border px-3 text-sm font-medium hover:bg-muted"
              href={download(patientId, "json")}
              download
            >
              <Download className="size-4" aria-hidden />
              Download data (JSON)
            </a>
            <a
              className="inline-flex h-9 pressable items-center gap-1.5 rounded-lg border px-3 text-sm font-medium hover:bg-muted"
              href={download(patientId, "pdf")}
              download
            >
              <FileText className="size-4" aria-hidden />
              Download data (PDF)
            </a>
          </div>
          <ActionButton
            variant="destructive"
            className="self-start"
            action={(form) => erasePatient(patientId, form)}
            confirm={{
              title: "Erase this patient's personal data?",
              description:
                "Names, date of birth, contact details, messages and free text are removed for good. Dates and outcomes stay without identity. This cannot be undone.",
              confirmLabel: "Erase personal data",
              tone: "danger",
              fields: (
                <>
                  <div className="flex items-start gap-2">
                    <Checkbox id="erase-ack" name="acknowledged" />
                    <Label htmlFor="erase-ack" className="text-sm font-normal">
                      I understand this cannot be undone, and the patient (or
                      the law) asked for it.
                    </Label>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="erase-phrase">
                      Type <span className="font-mono">{erasurePhrase}</span> to
                      confirm
                    </Label>
                    <Input
                      id="erase-phrase"
                      name="phrase"
                      autoComplete="off"
                      spellCheck={false}
                      required
                    />
                  </div>
                </>
              ),
            }}
          >
            <UserX aria-hidden />
            Erase personal data…
          </ActionButton>
        </section>
      ) : null}
    </div>
  );
}
