import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TestMessageForm } from "./test-message-form";

const sendTestMessage = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}));

vi.mock("./actions", () => ({ sendTestMessage }));
vi.mock("@/components/ui/toast", () => ({ toast }));

function submit(phone: string): void {
  fireEvent.change(screen.getByLabelText("Your phone number"), {
    target: { value: phone },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Send test reminder to my phone" }),
  );
}

describe("test message form", () => {
  beforeEach(() => vi.clearAllMocks());

  it("sends the number and channel, and confirms when it was sent", async () => {
    sendTestMessage.mockResolvedValue({
      ok: true,
      result: {
        outcome: "sent",
        mode: "live",
        detail: "Sent to ***678 by SMS.",
      },
    });
    render(<TestMessageForm whatsapp />);

    submit("+919812345678");

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Test message sent", {
        description: "Sent to ***678 by SMS.",
      }),
    );
    const form = sendTestMessage.mock.calls[0]?.[0] as FormData;
    expect(form.get("phone")).toBe("+919812345678");
    expect(form.get("channel")).toBe("SMS");
  });

  it("says when demo mode only logged it", async () => {
    sendTestMessage.mockResolvedValue({
      ok: true,
      result: { outcome: "logged", mode: "demo", detail: "Demo mode." },
    });
    render(<TestMessageForm whatsapp={false} />);

    submit("+919812345678");

    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith("Logged, not sent", {
        description: "Demo mode.",
      }),
    );
  });

  it("shows a refusal as an error", async () => {
    sendTestMessage.mockResolvedValue({
      ok: false,
      message: "That is three test messages this hour. Try again later.",
    });
    render(<TestMessageForm whatsapp />);

    submit("+919812345678");

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "That is three test messages this hour. Try again later.",
      ),
    );
  });

  it("offers WhatsApp only when a WhatsApp sender is set", () => {
    render(<TestMessageForm whatsapp={false} />);

    expect(screen.getByRole("radio", { name: "WhatsApp" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "SMS" })).toBeChecked();
  });
});
