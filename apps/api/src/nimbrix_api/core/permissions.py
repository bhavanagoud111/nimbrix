"""Role-based access control. Code is the source of truth; the roles table mirrors it."""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    READ = "read"  # costs, resources, anomalies, forecasts, recommendations, budgets, jobs
    IMPORTS_WRITE = "imports:write"
    BUDGETS_WRITE = "budgets:write"
    ANALYSIS_RUN = "analysis:run"  # forecasts, anomaly detection, recommendation refresh
    ANOMALIES_UPDATE = "anomalies:update"
    INVESTIGATIONS_RUN = "investigations:run"
    RECOMMENDATIONS_SIMULATE = "recommendations:simulate"
    RECOMMENDATIONS_UPDATE = "recommendations:update"
    APPROVALS_REQUEST = "approvals:request"
    APPROVALS_DECIDE = "approvals:decide"
    REMEDIATIONS_EXECUTE = "remediations:execute"
    CONNECTIONS_READ = "connections:read"
    CONNECTIONS_WRITE = "connections:write"
    AUDIT_READ = "audit:read"


_ANALYST = frozenset(
    {
        Permission.READ,
        Permission.IMPORTS_WRITE,
        Permission.ANALYSIS_RUN,
        Permission.ANOMALIES_UPDATE,
        Permission.INVESTIGATIONS_RUN,
        Permission.RECOMMENDATIONS_SIMULATE,
        Permission.RECOMMENDATIONS_UPDATE,
        Permission.APPROVALS_REQUEST,
        Permission.REMEDIATIONS_EXECUTE,
        Permission.CONNECTIONS_READ,
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "viewer": frozenset({Permission.READ}),
    "analyst": _ANALYST,
    "approver": frozenset(
        {
            Permission.READ,
            Permission.APPROVALS_DECIDE,
            Permission.REMEDIATIONS_EXECUTE,
            Permission.AUDIT_READ,
            Permission.CONNECTIONS_READ,
        }
    ),
    "auditor": frozenset({Permission.READ, Permission.AUDIT_READ, Permission.CONNECTIONS_READ}),
    "admin": frozenset(Permission),
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "viewer": "Read-only access to cost, resource, and analysis data.",
    "analyst": "Imports data, runs analysis and investigations, simulates and requests actions.",
    "approver": "Approves or rejects remediation requests; can execute approved simulations.",
    "auditor": "Read-only access including the audit trail.",
    "admin": "Full access, including connections and budgets.",
}


def permissions_for(role: str) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())
