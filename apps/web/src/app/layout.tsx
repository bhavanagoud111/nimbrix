import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: { default: "Nimbrix", template: "%s · Nimbrix" },
  description: "Cloud cost intelligence and optimization.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:rounded focus:bg-surface focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <header className="border-b border-border bg-surface">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
            <Link href="/status" className="text-lg font-semibold tracking-tight">
              Nimbrix
            </Link>
            <span
              className="rounded-full border border-warn/60 px-3 py-1 text-xs font-medium text-warn"
              title="Remediation actions are simulated; no cloud resources are changed."
            >
              Simulation mode · no cloud connections
            </span>
          </div>
        </header>
        <Providers>
          <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
