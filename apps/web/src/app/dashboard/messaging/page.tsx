import type { AssistantStatus, MessagingStatus } from "@prefix/shared";
import { Bot, FlaskConical, Radio, UserRound } from "lucide-react";
import type { Metadata } from "next";
import { Suspense } from "react";

import { Badge } from "@/components/ui/badge";
import { SkeletonList } from "@/components/ui/skeleton";
import { getAssistantStatus, getMessagingStatus } from "@/lib/server/dal";

import { TestMessageForm } from "./test-message-form";

export const metadata: Metadata = { title: "Messaging" };

function Senders({ status }: { status: MessagingStatus }) {
  return (
    <dl className="grid gap-1 text-sm sm:grid-cols-[8rem_1fr]">
      <dt className="text-muted-foreground">SMS from</dt>
      <dd>{status.sms_sender ?? "Not set (TWILIO_FROM_NUMBER)"}</dd>
      <dt className="text-muted-foreground">WhatsApp from</dt>
      <dd>{status.whatsapp_sender ?? "Not set (TWILIO_WHATSAPP_FROM)"}</dd>
    </dl>
  );
}

function Assistant({ status }: { status: AssistantStatus | null }) {
  const live = status?.mode === "live";
  return (
    <section
      aria-labelledby="assistant-heading"
      className="flex scroll-mt-20 flex-col gap-3 rounded-2xl border bg-card p-5 shadow-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        {live ? (
          <Bot className="size-4 text-ready" aria-hidden />
        ) : (
          <UserRound className="size-4 text-attention" aria-hidden />
        )}
        <h2
          id="assistant-heading"
          className="text-lg font-semibold tracking-tight"
        >
          {live ? "Assistant: live" : "Assistant: nurse hand-off mode"}
        </h2>
        {live && status?.model ? (
          <Badge variant="ready">{status.model}</Badge>
        ) : null}
      </div>
      <p className="text-sm">
        {live
          ? "The assistant answers patients only from their own approved prep plan, in English or Hindi, and names the steps it used. Anything else goes to a nurse."
          : "No ANTHROPIC_API_KEY is set, so every question a patient asks goes to the nurse inbox. Set it to let the assistant answer from the approved plan."}
      </p>
      <p className="text-sm text-muted-foreground">
        Red-flag symptoms always go to a nurse, in either mode, and the patient
        is told to call 112. Names, phone numbers and record ids are removed
        before a question reaches the model.
      </p>
    </section>
  );
}

async function Board() {
  const [status, assistant] = await Promise.all([
    getMessagingStatus(),
    getAssistantStatus(),
  ]);
  if (status === null) {
    return (
      <p className="text-sm text-muted-foreground">
        Messaging status is not available right now.
      </p>
    );
  }
  const live = status.mode === "live";
  return (
    <>
      <section
        aria-labelledby="mode-heading"
        className="flex flex-col gap-3 rounded-2xl border bg-card p-5 shadow-card"
      >
        <div className="flex flex-wrap items-center gap-2">
          {live ? (
            <Radio className="size-4 text-ready" aria-hidden />
          ) : (
            <FlaskConical className="size-4 text-attention" aria-hidden />
          )}
          <h2
            id="mode-heading"
            className="text-lg font-semibold tracking-tight"
          >
            {live ? "Messaging: live" : "Messaging: demo mode"}
          </h2>
          <Badge variant={live ? "ready" : "attention"}>
            {live ? "Twilio" : "Logged, not sent"}
          </Badge>
        </div>
        <p className="text-sm">
          {live
            ? "Reminders and check-ins go to patients by SMS or WhatsApp through Twilio."
            : "Reminders and check-ins are written to the log, with the number masked, and never sent. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER to send them."}
        </p>
        {live ? <Senders status={status} /> : null}
        {status.demo_hospital ? (
          <p className="text-sm text-muted-foreground">
            This is the Demo Hospital: its made-up patients never get real
            messages. The test message below is the one exception.
          </p>
        ) : null}
      </section>

      <Assistant status={assistant} />

      <section
        aria-labelledby="test-heading"
        className="flex flex-col gap-3 rounded-2xl border bg-card p-5 shadow-card"
      >
        <h2 id="test-heading" className="text-lg font-semibold tracking-tight">
          Send a test reminder
        </h2>
        {status.can_send_test ? (
          <>
            <p className="text-sm text-muted-foreground">
              One fixed message to your own phone, to check that messages
              arrive. Up to three an hour, and each one is recorded in the audit
              log.
            </p>
            <TestMessageForm whatsapp={status.whatsapp_sender !== null} />
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            Only admins can send a test message.
          </p>
        )}
      </section>
    </>
  );
}

export default function MessagingPage() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-6 sm:px-8 sm:py-8">
      <div>
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">
          Messaging
        </h1>
        <p className="text-sm text-muted-foreground">
          How reminders reach patients, whether the assistant answers, and a way
          to check messages arrive.
        </p>
      </div>
      <Suspense fallback={<SkeletonList label="Loading" rows={2} />}>
        <Board />
      </Suspense>
    </main>
  );
}
