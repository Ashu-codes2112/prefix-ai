"use server";

import type { SyncRun } from "@prefix/shared";
import { refresh } from "next/cache";
import { redirect } from "next/navigation";

import { api, ApiError } from "@/lib/server/api";

import { type ActionResult, refused } from "../action-result";
import { performAction } from "../form-action";
import { parseServiceCodes } from "./mapping";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NOT_FOUND = refused("That record no longer exists.");

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function failure(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "Only an administrator can do this.";
    if (error.status < 500 && error.message) return error.message;
  }
  return "Something went wrong. Please try again.";
}

export async function configureFhir(form: FormData): Promise<ActionResult> {
  const baseUrl = text(form, "base_url");
  if (!/^https?:\/\/\S+$/.test(baseUrl)) {
    return refused("Enter the FHIR server's full address.");
  }
  const codes = parseServiceCodes(text(form, "service_codes"));
  if (typeof codes === "string") return refused(codes);
  const facility = text(form, "default_facility_id");
  if (facility && !UUID.test(facility)) return refused("Choose a facility.");
  const token = text(form, "token");
  const body = {
    base_url: baseUrl,
    enabled: form.get("enabled") === "on",
    // Left blank, the stored token stays; "clear_token" removes it.
    ...(form.get("clear_token") === "on"
      ? { token: "" }
      : token
        ? { token }
        : {}),
    mapping: {
      service_codes: codes,
      ...(facility ? { default_facility_id: facility } : {}),
    },
  };
  return performAction(
    "/integrations/fhir",
    // The API replaces the whole connection, so PUT.
    { method: "PUT", body },
    "FHIR connection saved",
  );
}

export async function syncNow(): Promise<ActionResult> {
  let run: SyncRun;
  try {
    run = await api<SyncRun>("/integrations/fhir/sync", { method: "POST" });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
    return { ok: false, message: failure(error) };
  }
  refresh();
  if (run.status === "FAILED") {
    return { ok: false, message: run.error ?? "The sync failed." };
  }
  const created = Object.values(run.counts).reduce((n, c) => n + c.created, 0);
  const updated = Object.values(run.counts).reduce((n, c) => n + c.updated, 0);
  const skipped = Object.values(run.counts).reduce((n, c) => n + c.skipped, 0);
  return {
    ok: true,
    message: `Synced: ${String(created)} new, ${String(updated)} updated, ${String(skipped)} skipped.`,
  };
}

export type WebhookResult =
  | { ok: true; secret: string; message: string }
  | { ok: false; message: string };

export async function addWebhook(form: FormData): Promise<WebhookResult> {
  const url = text(form, "url");
  if (!/^https?:\/\/\S+$/.test(url)) {
    return { ok: false, message: "Enter the receiver's full address." };
  }
  try {
    const created = await api<{ secret: string }>("/integrations/webhooks", {
      method: "POST",
      body: { url, description: text(form, "description") },
    });
    refresh();
    return {
      ok: true,
      secret: created.secret,
      message: "Webhook added. Copy its signing secret now.",
    };
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
    return { ok: false, message: failure(error) };
  }
}

export async function setWebhookEnabled(
  endpointId: string,
  enabled: boolean,
): Promise<ActionResult> {
  if (!UUID.test(endpointId)) return NOT_FOUND;
  try {
    await api(`/integrations/webhooks/${endpointId}`, {
      method: "PATCH",
      body: { enabled },
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
    return { ok: false, message: failure(error) };
  }
  refresh();
  return {
    ok: true,
    message: enabled ? "Webhook switched on" : "Webhook switched off",
  };
}
