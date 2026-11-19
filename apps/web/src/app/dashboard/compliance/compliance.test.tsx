import type { Incident, RetentionReport } from "@prefix/shared";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { deadline, IncidentList, nextSteps } from "./incidents";
import { describeReport } from "./retention-card";

vi.mock("./actions", () => ({
  addIncidentPatients: vi.fn(),
  advanceIncident: vi.fn(),
  createIncident: vi.fn(),
  setRetention: vi.fn(),
}));

const DISCOVERED = Date.parse("2026-11-18T04:30:00Z");

function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: "01a1e942-d9c9-7370-8722-1df7766ae320",
    title: "Laptop lost",
    description: "A laptop with an export was lost on the train.",
    severity: "HIGH",
    status: "OPEN",
    data_categories: ["contact", "health"],
    discovered_at: new Date(DISCOVERED).toISOString(),
    notify_by: new Date(DISCOVERED + 72 * 3_600_000).toISOString(),
    authority_notified_at: null,
    patients_notified_at: null,
    closed_at: null,
    affected_patients: 12,
    ...overrides,
  };
}

function report(overrides: Partial<RetentionReport> = {}): RetentionReport {
  return {
    retention_days: 365,
    cutoff: "2025-11-19T00:00:00Z",
    dry_run: true,
    due: 3,
    held_for_incident: 1,
    held_for_upcoming_care: 2,
    erased: 0,
    ...overrides,
  };
}

describe("retention dry run", () => {
  it("says what tonight's purge would do and what it keeps", () => {
    expect(describeReport(report())).toBe(
      "Tonight's purge would erase 3 patients whose last procedure was before 19 Nov 2025. Kept: 2 with care still to come, 1 held for an open incident.",
    );
    expect(
      describeReport(
        report({ due: 1, held_for_incident: 0, held_for_upcoming_care: 0 }),
      ),
    ).toBe(
      "Tonight's purge would erase 1 patient whose last procedure was before 19 Nov 2025.",
    );
    expect(describeReport(report({ retention_days: null }))).toMatch(
      /kept until someone erases them/,
    );
  });
});

describe("incident deadline", () => {
  it("counts down the 72 hours and turns urgent in the last day", () => {
    const early = deadline(incident(), DISCOVERED + 3_600_000);
    expect(early.text).toMatch(/\(71 h left\)/);
    expect(early.tone).toBe("attention");

    expect(deadline(incident(), DISCOVERED + 60 * 3_600_000).tone).toBe(
      "urgent",
    );
    expect(deadline(incident(), DISCOVERED + 73 * 3_600_000).text).toMatch(
      /^Overdue/,
    );
  });

  it("records whether the authority was told in time", () => {
    const inTime = incident({
      authority_notified_at: new Date(
        DISCOVERED + 10 * 3_600_000,
      ).toISOString(),
    });
    const late = incident({
      authority_notified_at: new Date(
        DISCOVERED + 80 * 3_600_000,
      ).toISOString(),
    });

    expect(deadline(inTime)).toMatchObject({ tone: "ready" });
    expect(deadline(inTime).text).toMatch(/in time$/);
    expect(deadline(late)).toMatchObject({ tone: "urgent" });
  });

  it("offers the steps that are left, in order", () => {
    expect(nextSteps(incident())).toEqual([
      "contained",
      "authority_notified",
      "patients_notified",
      "closed",
    ]);
    expect(
      nextSteps(
        incident({
          status: "NOTIFIED",
          authority_notified_at: new Date(DISCOVERED).toISOString(),
        }),
      ),
    ).toEqual(["patients_notified", "closed"]);
    expect(nextSteps(incident({ status: "CLOSED" }))).toEqual([]);
  });
});

describe("incident list", () => {
  it("shows the facts, the counts and the report download, never patient details", () => {
    render(<IncidentList incidents={[incident()]} />);

    expect(
      screen.getByRole("heading", { name: "Laptop lost" }),
    ).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("contact, health")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Report for the authority" }),
    ).toHaveAttribute("href", expect.stringContaining("kind=incident"));
    expect(
      screen.getByRole("button", { name: "Mark contained" }),
    ).toBeInTheDocument();
  });

  it("says when there are none", () => {
    render(<IncidentList incidents={[]} />);

    expect(screen.getByText("No incidents recorded.")).toBeInTheDocument();
  });
});
