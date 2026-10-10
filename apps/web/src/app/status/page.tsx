import type { Metadata } from "next";
import { SystemStatus } from "@/components/status/system-status";

export const metadata: Metadata = { title: "System status" };

export default function StatusPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">System status</h1>
        <p className="mt-1 text-sm text-muted">
          Live readiness of the Nimbrix API and its database, refreshed every 10 seconds.
        </p>
      </div>
      <SystemStatus />
    </div>
  );
}
