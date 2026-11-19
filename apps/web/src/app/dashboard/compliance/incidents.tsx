"use client";

import {
  DATA_CATEGORIES,
  type Incident,
  type IncidentStep,
} from "@prefix/shared";
import { Download, ShieldAlert } from "lucide-react";

import { ActionButton, ActionForm } from "@/components/action-runner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import {
  addIncidentPatients,
  advanceIncident,
  createIncident,
} from "./actions";

const TIME = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "Asia/Kolkata",
});

const STEP_LABELS: Record<IncidentStep, string> = {
  contained: "Mark contained",
  authority_notified: "Authority told",
  patients_notified: "Patients told",
  closed: "Close incident",
};

/** Where the 72-hour clock stands, in words. */
export function deadline(
  incident: Incident,
  now = Date.now(),
): {
  text: string;
  tone: "ready" | "attention" | "urgent";
} {
  const by = new Date(incident.notify_by);
  if (incident.authority_notified_at) {
    const told = new Date(incident.authority_notified_at);
    return told <= by
      ? { text: `Authority told ${TIME.format(told)}, in time`, tone: "ready" }
      : { text: `Authority told ${TIME.format(told)}, late`, tone: "urgent" };
  }
  const hours = Math.floor((by.getTime() - now) / 3_600_000);
  if (hours < 0) {
    return {
      text: `Overdue: the authority had to be told by ${TIME.format(by)}`,
      tone: "urgent",
    };
  }
  return {
    text: `Tell the authority by ${TIME.format(by)} (${String(hours)} h left)`,
    tone: hours < 24 ? "urgent" : "attention",
  };
}

/** The steps that make sense next, in order. */
export function nextSteps(incident: Incident): IncidentStep[] {
  if (incident.status === "CLOSED") return [];
  const steps: IncidentStep[] = [];
  if (incident.status === "OPEN") steps.push("contained");
  if (!incident.authority_notified_at) steps.push("authority_notified");
  if (!incident.patients_notified_at) steps.push("patients_notified");
  steps.push("closed");
  return steps;
}

function reportHref(id: string): string {
  return `/dashboard/compliance/download?${new URLSearchParams({ kind: "incident", id }).toString()}`;
}

export function IncidentList({ incidents }: { incidents: Incident[] }) {
  if (incidents.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No incidents recorded.</p>
    );
  }
  return (
    <ul className="flex flex-col gap-3">
      {incidents.map((incident) => {
        const clock = deadline(incident);
        return (
          <li
            key={incident.id}
            className="flex flex-col gap-3 rounded-xl border p-4"
          >
            <div className="flex flex-wrap items-center gap-2">
              <ShieldAlert className="size-4 text-urgent" aria-hidden />
              <h3 className="font-medium">{incident.title}</h3>
              <Badge
                variant={
                  incident.severity === "LOW" || incident.severity === "MEDIUM"
                    ? "attention"
                    : "urgent"
                }
              >
                {incident.severity.toLowerCase()}
              </Badge>
              <Badge
                variant={incident.status === "CLOSED" ? "ready" : "default"}
              >
                {incident.status.toLowerCase()}
              </Badge>
            </div>
            <p className="text-sm">{incident.description}</p>
            <dl className="grid gap-1 text-sm sm:grid-cols-[10rem_1fr]">
              <dt className="text-muted-foreground">Discovered</dt>
              <dd>{TIME.format(new Date(incident.discovered_at))}</dd>
              <dt className="text-muted-foreground">Deadline</dt>
              <dd>
                <Badge variant={clock.tone}>{clock.text}</Badge>
              </dd>
              <dt className="text-muted-foreground">Affected patients</dt>
              <dd>{incident.affected_patients}</dd>
              <dt className="text-muted-foreground">Data involved</dt>
              <dd>
                {incident.data_categories.length > 0
                  ? incident.data_categories.join(", ")
                  : "Not yet known"}
              </dd>
            </dl>
            {incident.status !== "CLOSED" ? (
              <ActionForm
                action={(form) => addIncidentPatients(incident.id, form)}
                label={`Add affected patients to ${incident.title}`}
                className="flex flex-col gap-2"
              >
                <Label htmlFor={`mrns-${incident.id}`}>
                  Add affected patients by MRN (one per line)
                </Label>
                <Textarea id={`mrns-${incident.id}`} name="mrns" rows={2} />
                <Button
                  type="submit"
                  variant="outline"
                  size="sm"
                  className="self-start"
                >
                  Add patients
                </Button>
              </ActionForm>
            ) : null}
            <div className="flex flex-wrap gap-2">
              {nextSteps(incident).map((step) => (
                <ActionButton
                  key={step}
                  size="sm"
                  variant={step === "closed" ? "outline" : "soft"}
                  action={() => advanceIncident(incident.id, step)}
                >
                  {STEP_LABELS[step]}
                </ActionButton>
              ))}
              <a
                className="inline-flex h-8 pressable items-center gap-1.5 rounded-lg border px-3 text-sm font-medium hover:bg-muted"
                href={reportHref(incident.id)}
                download
              >
                <Download className="size-4" aria-hidden />
                Report for the authority
              </a>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function IncidentForm() {
  return (
    <ActionForm
      action={createIncident}
      label="Record an incident"
      className="flex flex-col gap-3 border-t pt-4"
    >
      <h3 className="font-medium">Record an incident</h3>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="incident-title">Title (no patient details)</Label>
        <Input
          id="incident-title"
          name="title"
          required
          minLength={3}
          maxLength={200}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="incident-description">What happened</Label>
        <Textarea
          id="incident-description"
          name="description"
          rows={3}
          required
        />
      </div>
      <div className="flex flex-wrap gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="incident-severity">Severity</Label>
          <select
            id="incident-severity"
            name="severity"
            defaultValue="MEDIUM"
            className="h-9 rounded-lg border bg-card px-2 text-sm"
          >
            <option value="LOW">Low</option>
            <option value="MEDIUM">Medium</option>
            <option value="HIGH">High</option>
            <option value="CRITICAL">Critical</option>
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="incident-discovered">Discovered (India time)</Label>
          <Input
            id="incident-discovered"
            name="discovered_at"
            type="datetime-local"
            required
          />
        </div>
      </div>
      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-sm font-medium">Data involved</legend>
        <div className="flex flex-wrap gap-3">
          {DATA_CATEGORIES.map((category) => (
            <label key={category} className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" name="data_categories" value={category} />
              {category}
            </label>
          ))}
        </div>
      </fieldset>
      <Button type="submit" className="self-start">
        Record incident
      </Button>
    </ActionForm>
  );
}
