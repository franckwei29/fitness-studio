"""Resolve immutable role versions and pin each task before model dispatch."""

from sqlalchemy import select

from app.admission import fingerprint
from app.models import AgentVersion, MemberGroup
from app.policy import PolicyDenied
from app.pricing import price_for
from app.settings import get_settings


def verify_version(version, role):
    if not version or version.role != role:
        raise PolicyDenied("The configured agent version does not match this role.")
    digest = fingerprint({"role": version.role, "model": version.model, "prompt": version.prompt})
    if version.fingerprint != digest:
        raise PolicyDenied("The agent version contents no longer match their approved fingerprint.")
    price_for(version.model)
    return version


def resolve_version(db, item, authorized, instruction):
    group = db.get(MemberGroup, authorized["group_id"]) if authorized.get("group_id") else None
    configured = group.configuration.get("agent_versions", {}).get(item.role) if group else None
    if configured:
        version = verify_version(db.get(AgentVersion, configured), item.role)
    else:
        model = get_settings().gemini_model
        digest = fingerprint({"role": item.role, "model": model, "prompt": instruction})
        version = db.scalar(select(AgentVersion).where(AgentVersion.fingerprint == digest))
        if not version:
            version = AgentVersion(
                role=item.role,
                label=f"{item.role} initial",
                model=model,
                prompt=instruction,
                fingerprint=digest,
            )
            db.add(version)
            db.flush()
        verify_version(version, item.role)
    pinned = item.input_versions.get("agent_version")
    if pinned and pinned != version.id:
        raise PolicyDenied("The task agent version changed. Review the request before continuing.")
    item.input_versions = item.input_versions | {"agent_version": version.id}
    return version
