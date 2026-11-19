"use client";

import { Send } from "lucide-react";
import { type FormEvent, useId, useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toast";

import { sendTestMessage } from "./actions";

/** The admin's "Send test reminder to my phone": one fixed message, a few times an hour. */
export function TestMessageForm({ whatsapp }: { whatsapp: boolean }) {
  const [pending, startTransition] = useTransition();
  const [channel, setChannel] = useState<"SMS" | "WHATSAPP">("SMS");
  const phoneId = useId();

  function send(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    startTransition(async () => {
      const answer = await sendTestMessage(form);
      if (!answer.ok) {
        toast.error(answer.message);
        return;
      }
      const { result } = answer;
      if (result.outcome === "sent") {
        toast.success("Test message sent", { description: result.detail });
      } else if (result.outcome === "logged") {
        toast.info("Logged, not sent", { description: result.detail });
      } else {
        toast.error("The test message failed", {
          description: result.detail,
        });
      }
    });
  }

  return (
    <form onSubmit={send} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={phoneId}>Your phone number</Label>
        <Input
          id={phoneId}
          name="phone"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          placeholder="+919812345678"
          required
          className="max-w-xs"
          aria-describedby={`${phoneId}-hint`}
        />
        <p id={`${phoneId}-hint`} className="text-xs text-muted-foreground">
          With the country code. The number is used once and not stored.
        </p>
      </div>
      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-sm font-medium">Send by</legend>
        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="channel"
              value="SMS"
              checked={channel === "SMS"}
              onChange={() => setChannel("SMS")}
            />
            SMS
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="channel"
              value="WHATSAPP"
              checked={channel === "WHATSAPP"}
              disabled={!whatsapp}
              onChange={() => setChannel("WHATSAPP")}
            />
            WhatsApp
          </label>
        </div>
      </fieldset>
      <div>
        <Button type="submit" disabled={pending}>
          <Send aria-hidden />
          {pending ? "Sending…" : "Send test reminder to my phone"}
        </Button>
      </div>
    </form>
  );
}
