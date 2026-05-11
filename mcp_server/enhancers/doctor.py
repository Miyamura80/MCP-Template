"""Doctor enhancer - elicits before fixing, attaches a dashboard if the client supports it."""

from mcp.server.elicitation import AcceptedElicitation

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from mcp_server.enhancers.schemas import ConfirmFix
from models.doctor import DoctorInput, DoctorResult


@enhance("doctor", fallback="headless")
async def doctor_enhanced(
    tool: EnhancedTool[DoctorInput, DoctorResult],
) -> DoctorResult:
    result = tool.call()

    if result.has_failures and tool.can_elicit and not tool.input.fix:
        r = await tool.elicit("Issues found. Auto-fix the fixable ones?", ConfirmFix)
        match r:
            case AcceptedElicitation(data=data) if data.fix:
                fixed_input = tool.input.model_copy(update={"fix": True})
                result = tool.call(override_input=fixed_input)
            case _:
                pass

    if tool.can_show_app:
        tool.send_app("ui://mymcp/doctor_dashboard")

    return result
