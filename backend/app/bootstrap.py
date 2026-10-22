"""Idempotent initial service configuration."""

from sqlalchemy import select

from app.database import transaction
from app.models import BudgetAccount, MemberGroup, User
from app.security import hash_password
from app.settings import get_settings

DEFAULT_CONFIGURATION = {
    "capabilities": {
        "training": True,
        "nutrition": True,
        "substitutions": True,
        "reassessment_reminders": True,
        "continuity_reminders": True,
        "weekly_summary": True,
    },
    "agent_versions": {},
    "field_permissions": {},
    "review_required": False,
}


def bootstrap():
    settings = get_settings()
    with transaction() as db:
        if not db.scalar(select(User).where(User.email == settings.owner_email)):
            db.add(
                User(
                    email=settings.owner_email,
                    display_name="Studio Owner",
                    role="Owner",
                    password_hash=hash_password(settings.owner_password.get_secret_value()),
                )
            )
        for name in ["Default", "Canary"]:
            if not db.scalar(select(MemberGroup).where(MemberGroup.name == name)):
                db.add(MemberGroup(name=name, configuration=DEFAULT_CONFIGURATION))
        if not db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == "global")):
            db.add(BudgetAccount(scope_key="global", limit_micros=0))


if __name__ == "__main__":
    bootstrap()
