import { ApiRequestError } from "@/lib/api/client";
import type { ReactNode } from "react";
import { Button } from "./primitives";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 py-6 text-sm text-muted"
    >
      <span
        aria-hidden="true"
        className="h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-transparent"
      />
      {label}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-border p-6 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 text-sm text-muted">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const apiError = error instanceof ApiRequestError ? error : null;
  const message = error instanceof Error ? error.message : "Something went wrong";
  return (
    <div role="alert" className="rounded-md border border-fail/50 p-4 text-sm">
      <p className="font-medium text-fail">
        {apiError?.status === 403
          ? "You do not have permission to view this"
          : "Could not load data"}
      </p>
      <p className="mt-1 text-muted">{message}</p>
      {apiError?.details.length ? (
        <ul className="mt-2 list-disc pl-5 text-muted">
          {apiError.details.map((d, i) => (
            <li key={i}>
              {d.field ? <code>{d.field}</code> : null} {d.message}
            </li>
          ))}
        </ul>
      ) : null}
      {apiError?.requestId && (
        <p className="mt-1 font-mono text-xs text-muted">request id: {apiError.requestId}</p>
      )}
      {onRetry && (
        <Button className="mt-3" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

/** Render loading / error / empty / data states for a TanStack Query result. */
export function QueryView<T>({
  query,
  empty,
  isEmpty,
  children,
  loadingLabel,
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    data: T | undefined;
    refetch: () => unknown;
  };
  empty?: ReactNode;
  isEmpty?: (data: T) => boolean;
  children: (data: T) => ReactNode;
  loadingLabel?: string;
}) {
  if (query.isPending) return <LoadingState label={loadingLabel} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  if (query.data === undefined) return null;
  if (isEmpty?.(query.data)) return <>{empty ?? <EmptyState title="Nothing here yet" />}</>;
  return <>{children(query.data)}</>;
}
