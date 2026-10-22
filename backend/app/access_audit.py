"""Record rejected authenticated access without request content or credentials."""

import logging

from fastapi.exception_handlers import http_exception_handler
from starlette.concurrency import run_in_threadpool

from app.database import transaction
from app.models import AuditEvent

logger = logging.getLogger(__name__)


def record_rejection(actor_id, route, method, status):
    with transaction() as db:
        db.add(
            AuditEvent(
                actor_id=actor_id,
                event_type="access.rejected",
                details={"route": route, "method": method, "status": status},
            )
        )


async def access_exception_handler(request, exc):
    actor_id = getattr(request.state, "actor_id", None)
    route = request.scope.get("route")
    if actor_id and route and exc.status_code in {401, 403, 404}:
        try:
            await run_in_threadpool(record_rejection, actor_id, route.path, request.method, exc.status_code)
        except Exception as error:
            # Preserve the denial even when its independent audit transaction fails.
            logger.error("Access rejection audit failed (%s).", type(error).__name__)
    return await http_exception_handler(request, exc)
