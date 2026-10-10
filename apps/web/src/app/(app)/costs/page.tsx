import type { Metadata } from "next";
import { Suspense } from "react";
import { CostExplorer } from "./cost-explorer";

export const metadata: Metadata = { title: "Cost Explorer" };

export default function CostsPage() {
  return (
    <Suspense>
      <CostExplorer />
    </Suspense>
  );
}
