"""Transactional reservations; uncertain provider outcomes retain their funds."""

from sqlalchemy import select

from app.database import transaction
from app.models import BudgetAccount, BudgetReservation, ModelCall, Request, now


class BudgetDenied(Exception):
    """A request cannot acquire the required allowance."""

    def __init__(self, message, wait_reason="BudgetRequired", context=None):
        super().__init__(message)
        self.wait_reason = wait_reason
        self.context = context or {}


def reserve(
    request_id,
    role,
    model,
    amount_micros,
    price_version,
    scope_keys,
    request_fingerprint=None,
    provider="gemini",
):
    if not isinstance(amount_micros, int) or amount_micros <= 0:
        raise ValueError("A positive integer reservation is required.")
    scopes = sorted(set(scope_keys) | {"global"})
    with transaction() as db:
        request = db.scalar(select(Request).where(Request.id == request_id).with_for_update())
        if not request or request.deadline <= now() or request.status in {"Cancelled", "Stopped"}:
            raise BudgetDenied("The request is missing or expired.", "RequestEnded")
        if request.model_calls >= request.max_model_calls:
            raise BudgetDenied("The request has reached its model call limit.", "RequestCallLimit")
        accounts = []
        for scope in scopes:
            account = db.scalar(
                select(BudgetAccount).where(BudgetAccount.scope_key == scope).with_for_update()
            )
            if account is None:
                raise BudgetDenied(
                    "A required budget account is not configured.",
                    context={"amount_micros": amount_micros, "model": model, "provider": provider, "scope_key": scope},
                )
            if account.currency != "USD":
                raise BudgetDenied("The budget currency is not supported.", "TechnicalReview")
            if account.limit_micros - account.spent_micros - account.reserved_micros < amount_micros:
                raise BudgetDenied(
                    "There is insufficient model budget.",
                    context={"amount_micros": amount_micros, "model": model, "provider": provider, "scope_key": scope},
                )
            accounts.append(account)
        call = ModelCall(
            request_id=request.id,
            user_id=request.user_id,
            role=role,
            model=model,
            provider=provider,
            reserved_micros=amount_micros,
            price_version=price_version,
            request_fingerprint=request_fingerprint,
        )
        db.add(call)
        db.flush()
        for account in accounts:
            account.reserved_micros += amount_micros
            db.add(BudgetReservation(account_id=account.id, call_id=call.id, amount_micros=amount_micros))
        request.model_calls += 1
        return call.id


def mark_dispatching(call_id, claim=None):
    """Only the first caller may dispatch; replay must never repeat the request."""
    with transaction() as db:
        if claim is not None:
            from app.policy import execution_context
            from app.work_queue import lock_claim

            item, request = lock_claim(db, claim)
            execution_context(db, item, request)
        call = db.scalar(select(ModelCall).where(ModelCall.id == call_id).with_for_update())
        if call is None or call.status != "Reserved":
            return False
        if claim is not None and call.request_id != claim.request_id:
            raise BudgetDenied("The dispatch reservation belongs to a different request.")
        call.status = "Dispatching"
        return True


def mark_unknown(call_id):
    with transaction() as db:
        call = db.scalar(select(ModelCall).where(ModelCall.id == call_id).with_for_update())
        if call and call.status == "Dispatching":
            call.status = "UsageUnknown"


def settle(call_id, actual_micros, usage, provider_id=None, output=None):
    if not isinstance(actual_micros, int) or actual_micros < 0:
        raise ValueError("Actual cost must be a nonnegative integer.")
    with transaction() as db:
        call = db.scalar(select(ModelCall).where(ModelCall.id == call_id).with_for_update())
        if not call:
            raise ValueError("Model call not found.")
        if call.status == "Settled":
            if call.actual_micros != actual_micros or call.usage != usage:
                raise ValueError("Settlement conflicts with the recorded usage.")
            return
        if call.status not in {"Dispatching", "UsageUnknown"}:
            raise ValueError("Only dispatched calls can be settled.")
        reservations = list(db.scalars(select(BudgetReservation).where(BudgetReservation.call_id == call_id)))
        account_ids = [row.account_id for row in reservations]
        accounts = list(
            db.scalars(
                select(BudgetAccount)
                .where(BudgetAccount.id.in_(account_ids))
                .order_by(BudgetAccount.scope_key)
                .with_for_update()
            )
        )
        amounts = {row.account_id: row.amount_micros for row in reservations}
        for account in accounts:
            account.reserved_micros -= amounts[account.id]
            account.spent_micros += actual_micros
        for reservation in reservations:
            reservation.settled = True
        call.actual_micros = actual_micros
        call.output = output
        call.usage = usage
        call.provider_id = provider_id
        call.completed_at = now()
        call.status = "Settled"


def cancel_undispatched(call_id):
    """Return funds only when dispatch has provably never started."""
    with transaction() as db:
        cancel_reserved_in_transaction(db, call_id)


def cancel_reserved_in_transaction(db, call_id):
    """Release an undispatched reservation in the caller's transaction."""
    existing = db.get(ModelCall, call_id)
    if not existing:
        raise ValueError("Model call not found.")
    request = db.scalar(select(Request).where(Request.id == existing.request_id).with_for_update())
    call = db.scalar(select(ModelCall).where(ModelCall.id == call_id).with_for_update())
    if not call:
        raise ValueError("Model call not found.")
    if call.status == "Cancelled":
        return
    if call.status != "Reserved":
        raise ValueError("A dispatched call requires usage reconciliation.")
    reservations = list(db.scalars(select(BudgetReservation).where(BudgetReservation.call_id == call_id)))
    amounts = {row.account_id: row.amount_micros for row in reservations}
    accounts = db.scalars(
        select(BudgetAccount)
        .where(BudgetAccount.id.in_(amounts))
        .order_by(BudgetAccount.scope_key)
        .with_for_update()
    )
    for account in accounts:
        account.reserved_micros -= amounts[account.id]
    for reservation in reservations:
        reservation.settled = True
    call.status = "Cancelled"
    # A capacity denial never dispatched this call and must not exhaust retries.
    request.model_calls = max(0, request.model_calls - 1)
    call.actual_micros = 0
    call.completed_at = now()
