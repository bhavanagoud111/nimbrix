/**
 * Typed contracts for the Nimbrix API.
 *
 * `schema.d.ts` is generated from `openapi.json` (exported by the API) — never
 * edit it by hand. Regenerate with `make contracts`.
 */
import type { components, paths } from "./schema";

export type { components, paths };

type Schemas = components["schemas"];

export type ActionList = Schemas["ActionList"];
export type AgentStepOut = Schemas["AgentStepOut"];
export type AnomalyList = Schemas["AnomalyList"];
export type AnomalyOut = Schemas["AnomalyOut"];
export type ApprovalList = Schemas["ApprovalList"];
export type ApprovalOut = Schemas["ApprovalOut"];
export type AuditEventOut = Schemas["AuditEventOut"];
export type AuditEventPage = Schemas["AuditEventPage"];
export type BudgetAlertList = Schemas["BudgetAlertList"];
export type BudgetAlertOut = Schemas["BudgetAlertOut"];
export type BudgetList = Schemas["BudgetList"];
export type BudgetOut = Schemas["BudgetOut"];
export type CheckStatus = Schemas["CheckStatus"];
export type ConnectionList = Schemas["ConnectionList"];
export type ConnectionOut = Schemas["ConnectionOut"];
export type CostBreakdown = Schemas["CostBreakdown"];
export type CostSummary = Schemas["CostSummary"];
export type CostTimeseries = Schemas["CostTimeseries"];
export type Dimensions = Schemas["Dimensions"];
export type ErrorResponse = Schemas["ErrorResponse"];
export type ForecastList = Schemas["ForecastList"];
export type ForecastOut = Schemas["ForecastOut"];
export type ImportList = Schemas["ImportList"];
export type ImportOut = Schemas["ImportOut"];
export type InvestigationList = Schemas["InvestigationList"];
export type InvestigationOut = Schemas["InvestigationOut"];
export type JobOut = Schemas["JobOut"];
export type LivenessResponse = Schemas["LivenessResponse"];
export type LoginResponse = Schemas["LoginResponse"];
export type MeResponse = Schemas["MeResponse"];
export type Permission = Schemas["Permission"];
export type PolicyPreview = Schemas["PolicyPreview"];
export type ReadinessResponse = Schemas["ReadinessResponse"];
export type RecommendationList = Schemas["RecommendationList"];
export type RecommendationOut = Schemas["RecommendationOut"];
export type RemediationList = Schemas["RemediationList"];
export type RemediationOut = Schemas["RemediationOut"];
export type ResourceDetail = Schemas["ResourceDetail"];
export type ResourceList = Schemas["ResourceList"];
export type ResourceOut = Schemas["ResourceOut"];
export type SimulationOut = Schemas["SimulationOut"];
export type UnitCosts = Schemas["UnitCosts"];
