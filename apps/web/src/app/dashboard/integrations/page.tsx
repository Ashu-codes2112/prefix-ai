import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonList } from "@/components/ui/skeleton";
import {
  canManageCompliance,
  getCurrentUser,
  getIntegrationHealth,
  listFacilities,
} from "@/lib/server/dal";

import { FhirForm, HealthCard, RunList, Webhooks } from "./panels";

export const metadata: Metadata = { title: "Integrations" };

async function Content() {
  const user = await getCurrentUser();
  // Integrations and compliance are both for administrators only.
  if (!canManageCompliance(user.role)) {
    return (
      <p className="rounded-2xl border bg-card p-5 text-sm shadow-card">
        The EHR connection and webhooks are for administrators.
      </p>
    );
  }
  const [health, facilities] = await Promise.all([
    getIntegrationHealth(),
    listFacilities(),
  ]);
  return (
    <>
      <HealthCard health={health} />
      <FhirForm health={health} facilities={facilities} />
      <RunList runs={health.runs} />
      <Webhooks
        endpoints={health.webhooks}
        deliveries={health.recent_deliveries}
        counts={health.deliveries}
      />
    </>
  );
}

export default function IntegrationsPage() {
  return (
    <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 px-4 py-6 sm:px-8 sm:py-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">
          Integrations
        </h1>
        <p className="text-muted-foreground">
          Patients, conditions and bookings from the hospital&apos;s EHR (FHIR
          R4), and appointment changes back to it.
        </p>
      </header>
      <Suspense fallback={<SkeletonList label="Loading integrations" />}>
        <Content />
      </Suspense>
    </main>
  );
}
