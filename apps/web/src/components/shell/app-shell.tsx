"use client";

import { api, unwrap } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

const NAV: { href: string; label: string; permission?: string }[] = [
  { href: "/", label: "Overview" },
  { href: "/costs", label: "Cost Explorer" },
  { href: "/budgets", label: "Budgets" },
  { href: "/anomalies", label: "Anomalies" },
  { href: "/forecasts", label: "Forecasting" },
  { href: "/recommendations", label: "Recommendations" },
  { href: "/investigations", label: "Investigations" },
  { href: "/approvals", label: "Approval Center" },
  { href: "/resources", label: "Resource Inventory" },
  { href: "/audit", label: "Audit Timeline", permission: "audit:read" },
  { href: "/settings", label: "Integrations & Settings" },
  { href: "/status", label: "System status" },
];

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const me = useMe();
  const [menuOpen, setMenuOpen] = useState(false);

  async function logout() {
    try {
      unwrap(await api.POST("/api/v1/auth/logout"));
    } finally {
      queryClient.clear();
      router.push("/login");
    }
  }

  const permissions = me.data?.permissions ?? [];
  const nav = NAV.filter(
    (item) => !item.permission || permissions.includes(item.permission as never),
  );

  return (
    <div className="flex min-h-full flex-col lg:flex-row">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <aside className="border-b border-border bg-surface lg:w-60 lg:shrink-0 lg:border-b-0 lg:border-r">
        <div className="flex items-center justify-between px-4 py-3">
          <Link href="/" className="text-lg font-semibold tracking-tight">
            Nimbrix
          </Link>
          <button
            type="button"
            className="rounded border border-border px-2 py-1 text-xs lg:hidden"
            aria-expanded={menuOpen}
            aria-controls="primary-nav"
            onClick={() => setMenuOpen((v) => !v)}
          >
            Menu
          </button>
        </div>
        <nav
          id="primary-nav"
          aria-label="Primary"
          className={`${menuOpen ? "block" : "hidden"} px-2 pb-3 lg:block`}
        >
          <ul className="space-y-0.5">
            {nav.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  onClick={() => setMenuOpen(false)}
                  aria-current={isActive(pathname, item.href) ? "page" : undefined}
                  className={`block rounded px-3 py-1.5 text-sm ${
                    isActive(pathname, item.href)
                      ? "bg-background font-medium text-accent"
                      : "text-muted hover:text-foreground"
                  }`}
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2 text-xs">
          <span
            className="rounded-full border border-warn/60 px-3 py-1 font-medium text-warn"
            title="Remediation is simulation-only; no cloud resources are changed."
          >
            Simulation mode · no cloud changes
          </span>
          {me.data && (
            <div className="flex items-center gap-3 text-muted">
              <span>
                {me.data.user.display_name} ·{" "}
                <span className="font-medium text-foreground">{me.data.role}</span> ·{" "}
                {me.data.tenant.name}
              </span>
              <button
                type="button"
                onClick={() => void logout()}
                className="rounded border border-border px-2 py-1 hover:border-accent"
              >
                Sign out
              </button>
            </div>
          )}
        </header>
        <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
          {children}
        </main>
      </div>
    </div>
  );
}
