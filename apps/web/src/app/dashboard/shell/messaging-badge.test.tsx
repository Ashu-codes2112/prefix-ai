import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessagingBadge } from "./messaging-badge";

describe("messaging badge", () => {
  it("says so in demo mode, and links to the messaging page", () => {
    render(<MessagingBadge mode="demo" />);

    expect(
      screen.getByRole("link", { name: "Messaging: demo mode" }),
    ).toHaveAttribute("href", "/dashboard/messaging");
  });

  it("is not shown when messages really go out, or the mode is unknown", () => {
    const { rerender } = render(<MessagingBadge mode="live" />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();

    rerender(<MessagingBadge mode={null} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
