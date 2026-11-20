import type { RiskModelInfo } from "@prefix/shared";
import { Gauge } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/** "Model: v1 rules" or "Model: v2 ML": which risk model ranks the list (docs/model_report.md). */
export function RiskModelBadge({ model }: { model: RiskModelInfo | null }) {
  if (!model) return null;
  return (
    <Badge
      variant={model.name === "v2" ? "attention" : "default"}
      title={`Cancellation risk model ${model.version}`}
    >
      <Gauge aria-hidden />
      Model: {model.label}
    </Badge>
  );
}
