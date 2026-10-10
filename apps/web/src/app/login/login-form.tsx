"use client";

import { Button, Field, inputClass } from "@/components/ui/primitives";
import { ErrorState } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

const DEMO_USERS = [
  ["analyst@acme.demo", "Analyst: imports, investigations, requests actions"],
  ["approver@acme.demo", "Approver: approves or rejects actions"],
  ["viewer@acme.demo", "Viewer: read-only"],
  ["auditor@acme.demo", "Auditor: read-only + audit trail"],
  ["admin@acme.demo", "Admin: everything"],
] as const;

function safeNext(value: string | null): string {
  // Only same-site relative paths: prevents open redirects.
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("analyst@acme.demo");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/auth/login", { body: { email, password } })),
    onSuccess: () => {
      queryClient.clear();
      router.push(safeNext(params.get("next")));
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    login.mutate();
  }

  return (
    <div className="w-full max-w-md space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Sign in to Nimbrix</h1>
        <p className="mt-1 text-sm text-muted">
          Local demo authentication. Production deployments use OIDC (not part of this demo).
        </p>
      </div>
      <form onSubmit={submit} className="space-y-4 rounded-lg border border-border bg-surface p-5">
        <Field label="Email">
          <input
            className={inputClass}
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field
          label="Password"
          hint="Demo users share the password set in NIMBRIX_DEMO_PASSWORD (.env)."
        >
          <input
            className={inputClass}
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {login.isError && <ErrorState error={login.error} />}
        <Button type="submit" variant="primary" loading={login.isPending} className="w-full">
          Sign in
        </Button>
      </form>
      <div className="rounded-lg border border-border p-4 text-sm">
        <p className="mb-2 font-medium">Demo accounts (tenant: Acme Corp demo)</p>
        <ul className="space-y-1">
          {DEMO_USERS.map(([user, role]) => (
            <li key={user}>
              <button
                type="button"
                className="text-accent hover:underline"
                onClick={() => setEmail(user)}
              >
                {user}
              </button>
              <span className="text-muted"> — {role}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
