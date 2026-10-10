import type { Metadata } from "next";
import Link from "next/link";
import { SystemStatus } from "@/components/status/system-status";

export const metadata: Metadata = { title: "System status" };

/** Public page: no login required, no tenant data shown. */
export default function StatusPage() {
  return (
    <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          Nimbrix
        </Link>
        <span className="rounded-full border border-warn/60 px-3 py-1 text-xs font-medium text-warn">
          Simulation mode · no cloud changes
        </span>
      </div>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">System status</h1>
          <p className="mt-1 text-sm text-muted">
            Live readiness of the Nimbrix API and its database, refreshed every 10 seconds.
          </p>
        </div>
        <SystemStatus />
      </div>
    </main>
  );
}
