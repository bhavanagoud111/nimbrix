import { redirect } from "next/navigation";

// The product overview arrives in a later phase; until then the root shows system status.
export default function Home() {
  redirect("/status");
}
