import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RiskModelBadge } from "./risk-model-badge";

describe("risk model badge", () => {
  it("names the model that ranks the list", () => {
    const { rerender } = render(
      <RiskModelBadge
        model={{ name: "v1", version: "rules-v1", label: "v1 rules" }}
      />,
    );
    expect(screen.getByText("Model: v1 rules")).toHaveAttribute(
      "title",
      "Cancellation risk model rules-v1",
    );

    rerender(
      <RiskModelBadge
        model={{ name: "v2", version: "lr-v2-synthetic-1", label: "v2 ML" }}
      />,
    );
    expect(screen.getByText("Model: v2 ML")).toBeInTheDocument();
  });

  it("shows nothing when the API cannot say", () => {
    const { container } = render(<RiskModelBadge model={null} />);

    expect(container).toBeEmptyDOMElement();
  });
});
