"""HTTP entry point for Fitness Studio."""

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.access_audit import access_exception_handler
from app.admission import router as admission_router
from app.agent_release import router as release_router
from app.budget_admin import router as budget_router
from app.candidate_correction import router as correction_router
from app.chat import router as chat_router
from app.cycle_requests import router as cycle_router
from app.database import transaction
from app.feedback import router as feedback_router
from app.group_configuration import router as group_router
from app.incidents import router as incidents_router
from app.member_admin import router as member_admin_router
from app.models import LoginSession, User
from app.notifications import router as notifications_router
from app.operations import router as operations_router
from app.plans import router as plans_router
from app.readiness import check_readiness
from app.rollback_recovery import router as rollback_recovery_router
from app.security import hash_password, issue_session, principal, verify_password
from app.settings import get_settings
from app.substitution import router as substitution_router
from app.trace_export import router as trace_export_router
from app.usage_admin import router as usage_router
from app.version_admin import router as version_router

app = FastAPI(title="Fitness Studio", version="0.1.0")
app.add_exception_handler(StarletteHTTPException, access_exception_handler)


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=100)


def user_view(user):
    return dict(id=user.id, email=user.email, display_name=user.display_name, role=user.role)


def set_session(response, token):
    response.set_cookie(
        "studio_session",
        token,
        httponly=True,
        samesite="strict",
        secure=get_settings().cookie_secure,
        max_age=43200,
        path="/",
    )


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.get("/health/ready")
def ready():
    try:
        check_readiness()
    except Exception:
        raise HTTPException(503, "Database, schema, or queue service is not ready.") from None
    return {"status": "ready", "model_calls_enabled": get_settings().model_calls_enabled}


@app.post("/api/auth/register", status_code=201)
def register(body: Registration, response: Response):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "Enter a valid email address.")
    try:
        with transaction() as db:
            user = User(
                email=email,
                password_hash=hash_password(body.password),
                display_name=body.display_name.strip(),
                role="Member",
            )
            db.add(user)
            db.flush()
            token, csrf = issue_session(db, user)
            result = user_view(user)
    except IntegrityError:
        raise HTTPException(409, "An account with this email already exists.")
    set_session(response, token)
    return {"user": result, "csrf_token": csrf}


@app.post("/api/auth/login")
def login(body: Credentials, response: Response):
    with transaction() as db:
        user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
        if not user or not user.active or not verify_password(user.password_hash, body.password):
            raise HTTPException(401, "Email or password is incorrect.")
        token, csrf = issue_session(db, user)
        result = user_view(user)
    set_session(response, token)
    return {"user": result, "csrf_token": csrf}


@app.get("/api/auth/me")
def me(request: Request, user: User = Depends(principal)):
    return {"user": user_view(user), "csrf_token": request.state.csrf_token}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user: User = Depends(principal)):
    with transaction() as db:
        db.execute(delete(LoginSession).where(LoginSession.id == request.state.session_id))
    response.delete_cookie("studio_session", path="/")
    return {"status": "signed_out"}


app.include_router(admission_router)

app.include_router(chat_router)
app.include_router(correction_router)

app.include_router(plans_router)

app.include_router(operations_router)
app.include_router(trace_export_router)

app.include_router(feedback_router)

app.include_router(notifications_router)

app.include_router(group_router)

app.include_router(incidents_router)

app.include_router(budget_router)

app.include_router(member_admin_router)

app.include_router(version_router)

app.include_router(release_router)
app.include_router(rollback_recovery_router)

app.include_router(substitution_router)

app.include_router(cycle_router)

app.include_router(usage_router)
