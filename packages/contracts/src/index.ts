/**
 * Typed contracts for the Nimbrix API.
 *
 * `schema.d.ts` is generated from `openapi.json` (exported by the API) — never
 * edit it by hand. Regenerate with `make contracts`.
 */
import type { components, paths } from "./schema";

export type { components, paths };

type Schemas = components["schemas"];

export type ErrorResponse = Schemas["ErrorResponse"];
export type LivenessResponse = Schemas["LivenessResponse"];
export type ReadinessResponse = Schemas["ReadinessResponse"];
export type CheckStatus = Schemas["CheckStatus"];
