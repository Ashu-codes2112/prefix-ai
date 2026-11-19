import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonList } from "@/components/ui/skeleton";
import {
  canManageCompliance,
  getCurrentUser,
  getRetentionReport,
  listIncidents,
} from "@/lib/server/dal";

import { IncidentForm, IncidentList } from "./incidents";
import { RetentionCard } from "./retention-card";

export const metadata: Metadata = { title: "Compliance" };

async function Content() {
  const user = await getCurrentUser();
  if (!canManageCompliance(user.role)) {
    return (
      <p className="rounded-2xl border bg-card p-5 text-sm shadow-card">
        Retention, data rights and incidents are for administrators. Ask your
        hospital&apos;s Prefix AI administrator.
      </p>
    );
  }
  const [report, incidents] = await Promise.all([
    getRetentionReport(),
    listIncidents(),
  ]);
  return (
    <>
      <RetentionCard report={report} />
      <section
        aria-labelledby="incidents-heading"
        className="flex flex-col gap-4 rounded-2xl border bg-card p-5 shadow-card"
      >
        <div className="flex flex-col gap-1">
          <h2
            id="incidents-heading"
            className="text-lg font-semibold tracking-tight"
          >
            Security incidents
          </h2>
          <p className="text-sm text-muted-foreground">
            Record a suspected breach as soon as you know of it. The authority
            must be told within 72 hours of discovery. See{" "}
            <span className="font-mono">docs/BREACH_RESPONSE.md</span>.
          </p>
        </div>
        <IncidentList incidents={incidents} />
        <IncidentForm />
      </section>
    </>
  );
}

export default function CompliancePage() {
  return (
    <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 px-4 py-6 sm:px-8 sm:py-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">
          Compliance
        </h1>
        <p className="text-muted-foreground">
          How long records are kept, and what happens when data goes astray
          (HIPAA and India&apos;s DPDP Act).
        </p>
      </header>
      <Suspense fallback={<SkeletonList label="Loading compliance" />}>
        <Content />
      </Suspense>
    </main>
  );
}
