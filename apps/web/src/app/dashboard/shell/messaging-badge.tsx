import type { AssistantStatus, MessagingMode } from "@prefix/shared";
import { FlaskConical, UserRound } from "lucide-react";
import Link from "next/link";

const BADGE =
  "inline-flex h-7 shrink-0 pressable items-center gap-1.5 rounded-full bg-attention-soft px-2.5 text-xs font-medium whitespace-nowrap text-attention-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/30";

/** In demo mode, staff see at a glance that messages are logged, not sent. */
export function MessagingBadge({ mode }: { mode: MessagingMode | null }) {
  if (mode !== "demo") return null;
  return (
    <Link href="/dashboard/messaging" className={BADGE}>
      <FlaskConical className="size-3.5" aria-hidden />
      Messaging: demo mode
    </Link>
  );
}

/** Without an LLM key, staff see that every patient question comes to a nurse. */
export function AssistantBadge({
  mode,
}: {
  mode: AssistantStatus["mode"] | null;
}) {
  if (mode !== "nurse_handoff") return null;
  return (
    <Link href="/dashboard/messaging#assistant-heading" className={BADGE}>
      <UserRound className="size-3.5" aria-hidden />
      Assistant: nurse hand-off mode
    </Link>
  );
}
