"use client";

import type { RetentionReport } from "@prefix/shared";
import { Archive } from "lucide-react";
import { useState } from "react";

import { ActionForm } from "@/components/action-runner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { setRetention } from "./actions";

const DAY = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "Asia/Kolkata",
});

/** Plain words for the dry run: what tonight's purge would do, and what it keeps. */
export function describeReport(report: RetentionReport): string {
  if (report.retention_days === null) {
    return "Records are kept until someone erases them. Nothing is purged.";
  }
  const cutoff = report.cutoff ? DAY.format(new Date(report.cutoff)) : "";
  const due = report.due === 1 ? "1 patient" : `${String(report.due)} patients`;
  const held: string[] = [];
  if (report.held_for_upcoming_care > 0) {
    held.push(
      `${String(report.held_for_upcoming_care)} with care still to come`,
    );
  }
  if (report.held_for_incident > 0) {
    held.push(`${String(report.held_for_incident)} held for an open incident`);
  }
  return (
    `Tonight's purge would erase ${due} whose last procedure was before ${cutoff}.` +
    (held.length > 0 ? ` Kept: ${held.join(", ")}.` : "")
  );
}

export function RetentionCard({ report }: { report: RetentionReport }) {
  const [keep, setKeep] = useState<"days" | "forever">(
    report.retention_days === null ? "forever" : "days",
  );
  return (
    <section
      aria-labelledby="retention-heading"
      className="flex flex-col gap-4 rounded-2xl border bg-card p-5 shadow-card"
    >
      <div className="flex items-center gap-2">
        <Archive className="size-4 text-muted-foreground" aria-hidden />
        <h2
          id="retention-heading"
          className="text-lg font-semibold tracking-tight"
        >
          Retention
        </h2>
      </div>
      <p className="text-sm" role="status">
        {describeReport(report)}
      </p>
      <ActionForm
        action={setRetention}
        resetOnSuccess={false}
        label="Retention policy"
        className="flex flex-col gap-3"
      >
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">
            Erase a patient&apos;s personal data
          </legend>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="keep"
              value="days"
              checked={keep === "days"}
              onChange={() => setKeep("days")}
            />
            this many days after their last procedure
          </label>
          <div className="flex items-center gap-2 pl-6">
            <Label htmlFor="retention-days" className="sr-only">
              Days to keep
            </Label>
            <Input
              id="retention-days"
              name="days"
              type="number"
              min={30}
              max={36500}
              defaultValue={report.retention_days ?? 2555}
              disabled={keep !== "days"}
              className="w-32"
            />
            <span className="text-sm text-muted-foreground">days</span>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="keep"
              value="forever"
              checked={keep === "forever"}
              onChange={() => setKeep("forever")}
            />
            never automatically (keep until someone erases them)
          </label>
        </fieldset>
        <p className="text-xs text-muted-foreground">
          Patients with an upcoming procedure, an active waitlist entry or an
          open incident are always kept. The purge runs every night at 02:15.
        </p>
        <Button type="submit" className="self-start">
          Save retention policy
        </Button>
      </ActionForm>
    </section>
  );
}
