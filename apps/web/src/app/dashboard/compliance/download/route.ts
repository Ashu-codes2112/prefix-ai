import { type NextRequest, NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/server/api";
import { getAccessToken } from "@/lib/server/session";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Where each download comes from. The API checks the role and audits the export. */
function upstreamPath(params: URLSearchParams): string | null {
  const id = params.get("id") ?? "";
  if (!UUID.test(id)) return null;
  switch (params.get("kind")) {
    case "patient": {
      const format = params.get("format") === "pdf" ? "pdf" : "json";
      return `/patients/${id}/export?format=${format}`;
    }
    case "incident":
      return `/compliance/incidents/${id}/report`;
    default:
      return null;
  }
}

/**
 * Streams a patient's data export (JSON or PDF) or an incident report for the authority,
 * using the signed-in admin's own session. Nothing is cached on the way.
 */
export async function GET(request: NextRequest): Promise<Response> {
  const path = upstreamPath(request.nextUrl.searchParams);
  if (!path) return new Response("Unknown download.", { status: 400 });
  const token = await getAccessToken();
  if (!token) return NextResponse.redirect(new URL("/login", request.url));

  const upstream = await fetch(`${apiBaseUrl()}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (upstream.status === 401) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  if (!upstream.ok) {
    const message =
      upstream.status === 403
        ? "Only an administrator can download this."
        : "The download failed.";
    return new Response(message, { status: upstream.status });
  }
  return new Response(await upstream.arrayBuffer(), {
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/octet-stream",
      "Content-Disposition":
        upstream.headers.get("content-disposition") ?? "attachment",
      "Cache-Control": "no-store",
    },
  });
}
