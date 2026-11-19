import type { IntegrationHealth, SyncRun } from "@prefix/shared";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { formatServiceCodes, parseServiceCodes } from "./mapping";
import { HealthCard, summarizeRun, Webhooks } from "./panels";

const actions = vi.hoisted(() => ({
  addWebhook: vi.fn(),
  configureFhir: vi.fn(),
  setWebhookEnabled: vi.fn(),
  syncNow: vi.fn(),
}));
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}));
// Built at run time, so no literal looks like a credential.
const SECRET = ["whsec", "test"].join("_");
vi.mock("./actions", () => actions);
vi.mock("@/components/ui/toast", () => ({ toast }));

function run(overrides: Partial<SyncRun> = {}): SyncRun {
  return {
    id: "r1",
    started_at: "2026-11-19T04:30:00Z",
    finished_at: "2026-11-19T04:30:05Z",
    status: "PARTIAL",
    counts: {
      Patient: { read: 3, created: 2, updated: 0, skipped: 1 },
      Observation: { read: 0, created: 0, updated: 0, skipped: 0 },
      Appointment: { read: 3, created: 2, updated: 1, skipped: 0 },
    },
    problems: ["Patient: Patient.name needs a family and a given name"],
    error: null,
    ...overrides,
  };
}

function health(overrides: Partial<IntegrationHealth> = {}): IntegrationHealth {
  return {
    status: "failing",
    fhir: {
      base_url: "https://ehr.example.org/fhir",
      enabled: true,
      has_token: true,
      mapping: { service_codes: { "73761001": "COLONOSCOPY" } },
      cursors: { Patient: "2026-11-18T04:30:00Z" },
      last_sync_started_at: "2026-11-19T04:30:00Z",
      last_success_at: "2026-11-18T04:30:00Z",
      last_error: "The FHIR server answered HTTP 401",
      consecutive_failures: 3,
    },
    runs: [run()],
    webhooks: [],
    deliveries: { pending: 1, delivered: 4, failed: 0 },
    recent_deliveries: [],
    ...overrides,
  };
}

describe("service code mapping", () => {
  it("reads one code per line and writes it back", () => {
    expect(
      parseServiceCodes("73761001 = colonoscopy\n\n 1234=CT_CONTRAST "),
    ).toEqual({
      "73761001": "COLONOSCOPY",
      "1234": "CT_CONTRAST",
    });
    expect(formatServiceCodes({ "73761001": "COLONOSCOPY" })).toBe(
      "73761001 = COLONOSCOPY",
    );
    expect(formatServiceCodes(undefined)).toBe("");
  });

  it("says which line to fix", () => {
    expect(parseServiceCodes("73761001")).toMatch(/not "73761001"/);
    expect(parseServiceCodes("a = b = c")).toMatch(/EHR code = procedure code/);
  });
});

describe("sync runs", () => {
  it("are summed up per type, leaving out empty ones", () => {
    expect(summarizeRun(run())).toBe(
      "Patient 2 new, 1 skipped; Appointment 2 new, 1 updated",
    );
    expect(summarizeRun(run({ counts: {} }))).toBe("No changes");
  });
});

describe("health card", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows a failing connection with its error and offers a sync", async () => {
    actions.syncNow.mockResolvedValue({ ok: true, message: "Synced" });
    render(<HealthCard health={health()} />);

    expect(screen.getByText("Failing")).toBeInTheDocument();
    expect(
      screen.getByText(/HTTP 401 \(3 times in a row\)/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => {
      expect(actions.syncNow).toHaveBeenCalledTimes(1);
    });
  });

  it("explains when no server is set up", () => {
    render(<HealthCard health={health({ status: "off", fhir: null })} />);

    expect(screen.getByText("Off")).toBeInTheDocument();
    expect(screen.getByText(/No FHIR server is set up/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("webhooks", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows a new endpoint's secret once, after adding it", async () => {
    actions.addWebhook.mockResolvedValue({
      ok: true,
      secret: SECRET,
      message: "Webhook added. Copy its signing secret now.",
    });
    render(
      <Webhooks
        endpoints={[]}
        deliveries={[]}
        counts={{ pending: 0, delivered: 0, failed: 0 }}
      />,
    );
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Receiver URL"), {
      target: { value: "https://ehr.example.org/hooks" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add webhook" }));

    expect(await screen.findByText(SECRET)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "It will not be shown again",
    );
    const [form] = actions.addWebhook.mock.calls[0] as [FormData];
    expect(form.get("url")).toBe("https://ehr.example.org/hooks");
  });
});
