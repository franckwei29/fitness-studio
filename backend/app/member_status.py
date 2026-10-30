"""Public service explanations without internal financial or provider details."""

DELAY = "We’ve saved your request. Preparation is temporarily delayed while we restore service availability. It will continue automatically when available."
REVIEW = "Your request needs a studio review before we can continue. We’ll update this conversation when the review is complete."
CHECKING = "We’re checking the previous attempt before continuing, so your request isn’t processed twice."


def waiting_status(reason):
    if reason in {"BudgetRequired", "HourlyCallLimit", "ModelDispatchUnavailable"}:
        return "Temporarily delayed", DELAY, True
    if reason == "AccountDailyCallLimit":
        return (
            "Waiting for your allowance",
            "Your daily AI allowance is currently used up. Your saved request will continue automatically when allowance becomes available.",
            True,
        )
    if reason == "UsageReconciliation":
        return "Checking your request", CHECKING, False
    return "Awaiting studio review", REVIEW, False


def public_event(payload):
    # SSE is a member API too: raw failure reasons and operational evidence stay private.
    result = {key: value for key, value in payload.items() if key in {"status", "role"}}
    if result.get("status") == "Waiting":
        result["message"] = waiting_status(payload.get("reason"))[1]
    return result
