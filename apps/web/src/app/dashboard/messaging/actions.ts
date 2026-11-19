"use server";

import type { MessagingTestResult } from "@prefix/shared";
import { redirect } from "next/navigation";

import { api, ApiError } from "@/lib/server/api";

const E164 = /^\+[1-9]\d{6,14}$/;

export type TestMessageResult =
  { ok: true; result: MessagingTestResult } | { ok: false; message: string };

/** Admins only (the API checks): one test message to the number typed in. */
export async function sendTestMessage(
  formData: FormData,
): Promise<TestMessageResult> {
  const phone = String(formData.get("phone") ?? "").replace(/[\s-]/g, "");
  const channel = formData.get("channel") === "WHATSAPP" ? "WHATSAPP" : "SMS";
  if (!E164.test(phone)) {
    return {
      ok: false,
      message: "Enter the number with its country code, e.g. +919812345678.",
    };
  }
  try {
    const result = await api<MessagingTestResult>("/messaging/test", {
      method: "POST",
      body: { phone, channel },
    });
    return { ok: true, result };
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 401) redirect("/login");
      if (error.status === 403) {
        return { ok: false, message: "Only admins can send a test message." };
      }
      if (error.status === 429) {
        return {
          ok: false,
          message: "That is three test messages this hour. Try again later.",
        };
      }
    }
    return { ok: false, message: "Something went wrong. Please try again." };
  }
}
