"""Public service explanations without internal financial or provider details."""

DELAY = "We’ve saved your request. Preparation is temporarily delayed while we restore service availability. It will continue automatically when available."
REVIEW = "Your request needs a studio review before we can continue. We’ll update this conversation when the review is complete."
CHECKING = "We’re checking the previous attempt before continuing, so your request isn’t processed twice."


def waiting_status(reason):
    if reason == "ContextCapacityExceeded":
        return ("Conversation too large", "This request exceeds the model’s context capacity. Cancel this update and start a new conversation with the plan you want to change. Your current plan is unchanged.", False)
    if reason == "TokenCountUnavailable":
        return ("Temporarily delayed", "We could not check the request size. Your request is saved and will retry when this service is available.", True)
    if reason == "CatalogueLookupFailed":
        return (
            "Unable to look up this item",
            "We could not retrieve usable data for the requested food or activity. Please try again or describe the item differently. No replacement was added.",
            False,
        )
    if reason == "CatalogueDietaryConflict":
        return (
            "Check dietary requirements",
            "The requested food conflicts with your dietary requirements, or its dietary information is unknown. Please choose another food or clarify your request.",
            False,
        )
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
