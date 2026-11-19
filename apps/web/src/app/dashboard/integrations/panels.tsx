"use client";

import type {
  FacilityOption,
  IntegrationHealth,
  IntegrationStatus,
  SyncRun,
  WebhookDelivery,
  WebhookEndpoint,
} from "@prefix/shared";
import { Copy, PlugZap, RefreshCw, Webhook } from "lucide-react";
import { useState, useTransition } from "react";

import { ActionButton, ActionForm } from "@/components/action-runner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/toast";

import {
  addWebhook,
  configureFhir,
  setWebhookEnabled,
  syncNow,
} from "./actions";
import { formatServiceCodes } from "./mapping";

const TIME = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "Asia/Kolkata",
});

function when(iso: string | null): string {
  return iso ? TIME.format(new Date(iso)) : "never";
}

export const STATUS: Record<
  IntegrationStatus,
  { label: string; tone: "ready" | "attention" | "urgent" | "default" }
> = {
  healthy: { label: "Healthy", tone: "ready" },
  failing: { label: "Failing", tone: "urgent" },
  stale: { label: "No recent sync", tone: "attention" },
  never_run: { label: "Not synced yet", tone: "attention" },
  off: { label: "Off", tone: "default" },
};

/** "Patient 2 new, 1 updated; Appointment 1 skipped", leaving out empty types. */
export function summarizeRun(run: SyncRun): string {
  const parts = Object.entries(run.counts)
    .map(([type, c]) => {
      const bits = [
        c.created ? `${String(c.created)} new` : "",
        c.updated ? `${String(c.updated)} updated` : "",
        c.skipped ? `${String(c.skipped)} skipped` : "",
      ].filter(Boolean);
      return bits.length > 0 ? `${type} ${bits.join(", ")}` : "";
    })
    .filter(Boolean);
  return parts.length > 0 ? parts.join("; ") : "No changes";
}

export function HealthCard({ health }: { health: IntegrationHealth }) {
  const status = STATUS[health.status];
  const fhir = health.fhir;
  return (
    <section
      aria-labelledby="fhir-health-heading"
      className="flex flex-col gap-3 rounded-2xl border bg-card p-5 shadow-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <PlugZap className="size-4 text-muted-foreground" aria-hidden />
        <h2
          id="fhir-health-heading"
          className="text-lg font-semibold tracking-tight"
        >
          EHR sync (FHIR R4)
        </h2>
        <Badge variant={status.tone}>{status.label}</Badge>
      </div>
      {fhir ? (
        <dl className="grid gap-1 text-sm sm:grid-cols-[11rem_1fr]">
          <dt className="text-muted-foreground">Server</dt>
          <dd className="break-all">{fhir.base_url}</dd>
          <dt className="text-muted-foreground">Last successful sync</dt>
          <dd>{when(fhir.last_success_at)}</dd>
          <dt className="text-muted-foreground">Last attempt</dt>
          <dd>{when(fhir.last_sync_started_at)}</dd>
          {fhir.last_error ? (
            <>
              <dt className="text-muted-foreground">Last error</dt>
              <dd className="text-urgent-foreground">
                {fhir.last_error}
                {fhir.consecutive_failures > 1
                  ? ` (${String(fhir.consecutive_failures)} times in a row)`
                  : ""}
              </dd>
            </>
          ) : null}
          <dt className="text-muted-foreground">Read up to</dt>
          <dd>
            {Object.keys(fhir.cursors).length > 0
              ? Object.entries(fhir.cursors)
                  .map(([type, at]) => `${type} ${when(at)}`)
                  .join(" · ")
              : "Nothing read yet"}
          </dd>
        </dl>
      ) : (
        <p className="text-sm text-muted-foreground">
          No FHIR server is set up. Patients and bookings come from the app and
          CSV imports until one is.
        </p>
      )}
      {fhir?.enabled ? (
        <ActionButton action={() => syncNow()} className="self-start">
          <RefreshCw aria-hidden />
          Sync now
        </ActionButton>
      ) : null}
      <p className="text-xs text-muted-foreground">
        The worker syncs every 15 minutes. Synced patients have not consented to
        messages until staff record it, and a sync only ever adds a condition;
        removing one is a person&apos;s decision.
      </p>
    </section>
  );
}

export function FhirForm({
  health,
  facilities,
}: {
  health: IntegrationHealth;
  facilities: FacilityOption[];
}) {
  const fhir = health.fhir;
  return (
    <ActionForm
      action={configureFhir}
      resetOnSuccess={false}
      label="FHIR connection"
      className="flex flex-col gap-3 rounded-2xl border bg-card p-5 shadow-card"
    >
      <h2 className="text-lg font-semibold tracking-tight">Set up</h2>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="fhir-base-url">FHIR base URL</Label>
        <Input
          id="fhir-base-url"
          name="base_url"
          type="url"
          required
          placeholder="https://ehr.example.org/fhir/r4"
          defaultValue={fhir?.base_url ?? ""}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="fhir-token">
          Bearer token{" "}
          {fhir?.has_token ? "(one is stored; leave empty to keep it)" : ""}
        </Label>
        <Input
          id="fhir-token"
          name="token"
          type="password"
          autoComplete="off"
        />
        {fhir?.has_token ? (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" name="clear_token" /> Remove the stored token
          </label>
        ) : null}
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="fhir-codes">
          Service codes (one per line: EHR code = procedure code)
        </Label>
        <Textarea
          id="fhir-codes"
          name="service_codes"
          rows={3}
          placeholder="73761001 = COLONOSCOPY"
          defaultValue={formatServiceCodes(fhir?.mapping.service_codes)}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="fhir-facility">Facility for synced bookings</Label>
        <select
          id="fhir-facility"
          name="default_facility_id"
          defaultValue={fhir?.mapping.default_facility_id ?? ""}
          className="h-9 rounded-lg border bg-card px-2 text-sm"
        >
          <option value="">Choose a facility</option>
          {facilities.map((facility) => (
            <option key={facility.id} value={facility.id}>
              {facility.name}
            </option>
          ))}
        </select>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          name="enabled"
          defaultChecked={fhir?.enabled ?? true}
        />
        Sync from this server
      </label>
      <Button type="submit" className="self-start">
        Save connection
      </Button>
    </ActionForm>
  );
}

const RUN_TONE: Record<SyncRun["status"], "ready" | "attention" | "urgent"> = {
  SUCCEEDED: "ready",
  PARTIAL: "attention",
  RUNNING: "attention",
  FAILED: "urgent",
};

export function RunList({ runs }: { runs: SyncRun[] }) {
  return (
    <section
      aria-labelledby="runs-heading"
      className="flex flex-col gap-3 rounded-2xl border bg-card p-5 shadow-card"
    >
      <h2 id="runs-heading" className="text-lg font-semibold tracking-tight">
        Recent syncs
      </h2>
      {runs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No syncs yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {runs.map((run) => (
            <li key={run.id} className="rounded-xl border p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={RUN_TONE[run.status]}>
                  {run.status.toLowerCase()}
                </Badge>
                <span className="text-muted-foreground">
                  {when(run.started_at)}
                </span>
              </div>
              <p className="mt-1">{run.error ?? summarizeRun(run)}</p>
              {run.problems.length > 0 ? (
                <ul className="mt-1 list-disc pl-5 text-muted-foreground">
                  {run.problems.map((problem) => (
                    <li key={problem}>{problem}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SecretOnce({ secret }: { secret: string }) {
  return (
    <div
      role="status"
      className="flex flex-col gap-2 rounded-xl border bg-attention-soft/50 p-3"
    >
      <p className="text-sm font-medium">
        Signing secret: copy it now. It will not be shown again.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="rounded bg-card px-2 py-1 font-mono text-xs break-all">
          {secret}
        </code>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => {
            void navigator.clipboard.writeText(secret).then(
              () => toast.success("Copied"),
              () => toast.error("Copy it by hand"),
            );
          }}
        >
          <Copy aria-hidden />
          Copy
        </Button>
      </div>
    </div>
  );
}

const DELIVERY_TONE: Record<
  WebhookDelivery["status"],
  "ready" | "attention" | "urgent"
> = {
  DELIVERED: "ready",
  PENDING: "attention",
  FAILED: "urgent",
};

export function Webhooks({
  endpoints,
  deliveries,
  counts,
}: {
  endpoints: WebhookEndpoint[];
  deliveries: WebhookDelivery[];
  counts: IntegrationHealth["deliveries"];
}) {
  const [secret, setSecret] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function submit(form: HTMLFormElement): void {
    const data = new FormData(form);
    startTransition(async () => {
      const result = await addWebhook(data);
      if (result.ok) {
        setSecret(result.secret);
        toast.success(result.message);
        form.reset();
      } else {
        toast.error(result.message);
      }
    });
  }

  return (
    <section
      aria-labelledby="webhooks-heading"
      className="flex flex-col gap-4 rounded-2xl border bg-card p-5 shadow-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Webhook className="size-4 text-muted-foreground" aria-hidden />
        <h2
          id="webhooks-heading"
          className="text-lg font-semibold tracking-tight"
        >
          Outbound webhooks
        </h2>
        <span className="text-sm text-muted-foreground">
          {counts.delivered} delivered · {counts.pending} waiting ·{" "}
          {counts.failed} failed
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        Every appointment status change is posted to each endpoint, signed with
        HMAC-SHA256 in the X-Prefix-Signature header. Payloads hold ids,
        statuses and times only.
      </p>
      {endpoints.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {endpoints.map((endpoint) => (
            <li
              key={endpoint.id}
              className="flex flex-wrap items-center gap-2 rounded-xl border p-3 text-sm"
            >
              <span className="flex-1 break-all">
                {endpoint.url}
                {endpoint.description ? (
                  <span className="text-muted-foreground">
                    {" "}
                    · {endpoint.description}
                  </span>
                ) : null}
              </span>
              <Badge variant={endpoint.enabled ? "ready" : "default"}>
                {endpoint.enabled ? "on" : "off"}
              </Badge>
              <ActionButton
                size="sm"
                variant="outline"
                action={() => setWebhookEnabled(endpoint.id, !endpoint.enabled)}
              >
                {endpoint.enabled ? "Switch off" : "Switch on"}
              </ActionButton>
            </li>
          ))}
        </ul>
      ) : null}
      {secret ? <SecretOnce secret={secret} /> : null}
      <form
        aria-label="Add a webhook"
        aria-busy={pending}
        className="flex flex-col gap-2 border-t pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          submit(event.currentTarget);
        }}
      >
        <fieldset disabled={pending} className="contents">
          <Label htmlFor="webhook-url">Receiver URL</Label>
          <Input
            id="webhook-url"
            name="url"
            type="url"
            required
            placeholder="https://ehr.example.org/hooks/prefix"
          />
          <Label htmlFor="webhook-description">What it is (optional)</Label>
          <Input id="webhook-description" name="description" maxLength={200} />
          <Button type="submit" variant="outline" className="self-start">
            Add webhook
          </Button>
        </fieldset>
      </form>
      {deliveries.length > 0 ? (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-muted-foreground">
            Recent deliveries
          </h3>
          <ul className="flex flex-col gap-1 text-sm">
            {deliveries.map((delivery) => (
              <li key={delivery.id} className="flex flex-wrap gap-2">
                <Badge variant={DELIVERY_TONE[delivery.status]}>
                  {delivery.status.toLowerCase()}
                </Badge>
                <span>
                  {delivery.appointment_status
                    ? `Appointment ${delivery.appointment_status.toLowerCase()}`
                    : delivery.event}
                </span>
                <span className="text-muted-foreground">
                  {when(delivery.created_at)}
                  {delivery.attempts > 1
                    ? ` · ${String(delivery.attempts)} attempts`
                    : ""}
                  {delivery.last_error ? ` · ${delivery.last_error}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
