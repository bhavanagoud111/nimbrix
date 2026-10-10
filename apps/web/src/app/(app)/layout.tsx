import { AppShell } from "@/components/shell/app-shell";
import { LoadingState } from "@/components/ui/states";
import { Suspense } from "react";

// The dashboard is per-user and per-tenant, so it renders on the client after the
// static fallback; nothing tenant-specific is ever prerendered.
export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <Suspense
      fallback={
        <main className="p-6">
          <LoadingState label="Loading Nimbrix…" />
        </main>
      }
    >
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
