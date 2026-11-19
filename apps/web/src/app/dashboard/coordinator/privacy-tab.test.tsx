import type { ConsentStatus } from "@prefix/shared";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PrivacyTab } from "./privacy-tab";

const actions = vi.hoisted(() => ({
  recordConsent: vi.fn(),
  erasePatient: vi.fn(),
}));
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}));

vi.mock("../compliance/actions", () => actions);
vi.mock("@/components/ui/toast", () => ({ toast }));

const PATIENT = "01a1e942-d9c9-7370-8722-1df7766ae320";

function consent(overrides: Partial<ConsentStatus> = {}): ConsentStatus {
  return {
    consent_to_message: true,
    consent_timestamp: "2026-11-01T04:30:00Z",
    erased: false,
    history: [
      {
        granted: true,
        source: "STAFF",
        recorded_at: "2026-11-01T04:30:00Z",
        recorded_by_staff: true,
      },
    ],
    ...overrides,
  };
}

describe("privacy tab", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows consent and its history, and lets staff withdraw it after asking", async () => {
    actions.recordConsent.mockResolvedValue({ ok: true, message: "Withdrawn" });
    render(
      <PrivacyTab
        patientId={PATIENT}
        privacy={{
          consent: consent(),
          erasurePhrase: null,
          canRecordConsent: true,
        }}
      />,
    );

    expect(screen.getByText("Messages on")).toBeInTheDocument();
    expect(screen.getByText(/Given, recorded by staff/)).toBeInTheDocument();
    expect(screen.queryByText(/Data rights/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Withdraw consent" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Withdraw consent?",
    });
    fireEvent.click(
      Array.from(dialog.querySelectorAll("button")).find(
        (b) => b.textContent === "Withdraw consent",
      ) as HTMLButtonElement,
    );

    await waitFor(() => {
      expect(actions.recordConsent).toHaveBeenCalledWith(PATIENT, false);
    });
  });

  it("offers only what the role may do", () => {
    render(
      <PrivacyTab
        patientId={PATIENT}
        privacy={{
          consent: consent({ consent_to_message: false, history: [] }),
          erasurePhrase: null,
          canRecordConsent: false,
        }}
      />,
    );

    expect(screen.getByText("Messages off")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("gives admins the exports and a double-confirmed erasure", async () => {
    actions.erasePatient.mockResolvedValue({ ok: true, message: "Erased" });
    render(
      <PrivacyTab
        patientId={PATIENT}
        privacy={{
          consent: consent(),
          erasurePhrase: "ERASE 01A1E942",
          canRecordConsent: true,
        }}
      />,
    );

    expect(
      screen.getByRole("link", { name: "Download data (JSON)" }),
    ).toHaveAttribute(
      "href",
      `/dashboard/compliance/download?kind=patient&id=${PATIENT}&format=json`,
    );
    expect(
      screen.getByRole("link", { name: "Download data (PDF)" }),
    ).toHaveAttribute("href", expect.stringContaining("format=pdf"));

    fireEvent.click(
      screen.getByRole("button", { name: "Erase personal data…" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("ERASE 01A1E942");
    fireEvent.change(screen.getByLabelText(/to confirm/), {
      target: { value: "ERASE 01A1E942" },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(
      Array.from(dialog.querySelectorAll("button")).find(
        (b) => b.textContent === "Erase personal data",
      ) as HTMLButtonElement,
    );

    await waitFor(() => {
      expect(actions.erasePatient).toHaveBeenCalledTimes(1);
    });
    const [id, form] = actions.erasePatient.mock.calls[0] as [string, FormData];
    expect(id).toBe(PATIENT);
    expect(form.get("phrase")).toBe("ERASE 01A1E942");
    expect(form.get("acknowledged")).toBe("on");
  });

  it("says so once the data is erased, and offers nothing more", () => {
    render(
      <PrivacyTab
        patientId={PATIENT}
        privacy={{
          consent: consent({ erased: true, consent_to_message: false }),
          erasurePhrase: "ERASE 01A1E942",
          canRecordConsent: true,
        }}
      />,
    );

    expect(
      screen.getByText("This patient's personal data was erased"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
