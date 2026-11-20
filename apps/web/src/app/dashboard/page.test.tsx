import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DashboardPage from "./page";

// The page reads the signed-in user and the procedures in streamed sections, and those layers
// are server-only, so they are replaced here.
vi.mock("@/lib/server/dal", () => ({
  canManageCompliance: vi.fn(),
  canRecordConsent: vi.fn(),
  getBadges: vi.fn(),
  getConsentStatus: vi.fn(),
  getCurrentUser: vi.fn(),
  getErasurePhrase: vi.fn(),
  getPatientDrawer: vi.fn(),
  getRiskModel: vi.fn().mockResolvedValue(null),
  listUpcoming: vi.fn().mockResolvedValue([]),
}));
vi.mock("./compliance/actions", () => ({
  erasePatient: vi.fn(),
  recordConsent: vi.fn(),
}));
vi.mock("./demo/actions", () => ({
  openDemoPatient: vi.fn(),
  resetDemo: vi.fn(),
}));
vi.mock("./coordinator/actions", () => ({
  approveRequest: vi.fn(),
  completeTask: vi.fn(),
  confirmStep: vi.fn(),
  declineRequest: vi.fn(),
  logCall: vi.fn(),
  messagePatient: vi.fn(),
  releaseSlot: vi.fn(),
  reschedule: vi.fn(),
  resendFailed: vi.fn(),
}));

describe("DashboardPage", () => {
  it("renders the overview title and loading skeletons first", () => {
    const props = {
      params: Promise.resolve({}),
      searchParams: Promise.resolve({}),
    } as unknown as Parameters<typeof DashboardPage>[0];
    render(<DashboardPage {...props} />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Overview" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: "Loading procedures" }),
    ).toBeInTheDocument();
  });
});
