"use client";

import type { MeResponse } from "@nimbrix/contracts";
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async (): Promise<MeResponse> => unwrap(await api.GET("/api/v1/me")),
    staleTime: 60_000,
  });
}

export function useCan(permission: string): boolean {
  const me = useMe();
  return Boolean(me.data?.permissions.includes(permission as MeResponse["permissions"][number]));
}

const TERMINAL_JOB_STATES = new Set(["succeeded", "failed", "cancelled"]);

/** Poll a background job until it reaches a terminal state. */
export function useJob(jobId: string | null | undefined) {
  return useQuery({
    queryKey: ["job", jobId],
    enabled: Boolean(jobId),
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId! } } })),
    refetchInterval: (q) =>
      q.state.data && TERMINAL_JOB_STATES.has(q.state.data.state) ? false : 1500,
  });
}
