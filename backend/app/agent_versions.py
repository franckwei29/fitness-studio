"""Resolve immutable role versions and pin each task before model dispatch."""

from sqlalchemy import select

from app.admission import fingerprint
from app.model_catalog import model_info, validate_options
from app.models import AgentVersion, MemberGroup
from app.policy import PolicyDenied
from app.pricing import price_for
from app.settings import get_settings


def version_fingerprint(role, model, prompt, max_output_tokens=None, reasoning_effort=None):
    values = {"role": role, "model": model, "prompt": prompt}
    if max_output_tokens is not None or reasoning_effort is not None:
        values |= {"max_output_tokens": max_output_tokens, "reasoning_effort": reasoning_effort}
    return fingerprint(values)


def verify_version(version, role):
    if not version or version.role != role:
        raise PolicyDenied("The configured agent version does not match this role.")
    try:
        validate_options(
            version.model,
            getattr(version, "provider", None),
            getattr(version, "max_output_tokens", 2048) or 2048,
            getattr(version, "reasoning_effort", None),
        )
    except ValueError as exc:
        raise PolicyDenied(str(exc)) from None
    digest = version_fingerprint(
        version.role,
        version.model,
        version.prompt,
        getattr(version, "max_output_tokens", None),
        getattr(version, "reasoning_effort", None),
    )
    if version.fingerprint != digest:
        raise PolicyDenied("The agent version contents no longer match their approved fingerprint.")
    price_for(version.model)
    return version


def resolve_version(db, item, authorized, instruction):
    pinned = item.input_versions.get("agent_version")
    if pinned:
        return verify_version(db.get(AgentVersion, pinned), item.role)
    group = db.get(MemberGroup, authorized["group_id"]) if authorized.get("group_id") else None
    configured = group.configuration.get("agent_versions", {}).get(item.role) if group else None
    if configured:
        version = verify_version(db.get(AgentVersion, configured), item.role)
    else:
        model = get_settings().default_model
        digest = fingerprint({"role": item.role, "model": model, "prompt": instruction})
        version = db.scalar(select(AgentVersion).where(AgentVersion.fingerprint == digest))
        if not version:
            version = AgentVersion(
                role=item.role,
                label=f"{item.role} initial",
                model=model,
                provider=model_info(model)["provider"],
                prompt=instruction,
                fingerprint=digest,
            )
            db.add(version)
            db.flush()
        verify_version(version, item.role)
    item.input_versions = item.input_versions | {"agent_version": version.id}
    return version
