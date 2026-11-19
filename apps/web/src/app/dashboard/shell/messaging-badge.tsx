import type { MessagingMode } from "@prefix/shared";
import { FlaskConical } from "lucide-react";
import Link from "next/link";

/** In demo mode, staff see at a glance that messages are logged, not sent. */
export function MessagingBadge({ mode }: { mode: MessagingMode | null }) {
  if (mode !== "demo") return null;
  return (
    <Link
      href="/dashboard/messaging"
      className="inline-flex h-7 shrink-0 pressable items-center gap-1.5 rounded-full bg-attention-soft px-2.5 text-xs font-medium whitespace-nowrap text-attention-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/30"
    >
      <FlaskConical className="size-3.5" aria-hidden />
      Messaging: demo mode
    </Link>
  );
}
