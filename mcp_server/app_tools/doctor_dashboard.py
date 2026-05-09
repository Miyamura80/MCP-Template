"""App-only tools used by the doctor_dashboard MCP App."""

from mcp_server.server import mcp
from models.doctor import DoctorInput, DoctorResult
from services.doctor_svc import doctor as _doctor_service


@mcp.tool(
    name="doctor_dashboard.refresh",
    description="Re-run doctor checks (called by the doctor dashboard).",
    meta={"ui": {"visibility": ["app"]}},
)
def refresh() -> DoctorResult:
    return _doctor_service(DoctorInput(fix=False))
