"""Read-only comparison previews and atomic, explicitly confirmed execution."""

from types import SimpleNamespace
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select

from app import version_admin
from app.admission import fingerprint
from app.agent_versions import verify_version, version_fingerprint
from app.database import atomic_business_transaction, transaction
from app.model_catalog import catalog, model_info, validate_options
from app.models import AgentVersion, MemberGroup
from app.settings import get_settings


class ChatComparison(version_admin.CurrentModelComparison):
    # Chat must resolve the role from the conversation, never a schema default.
    role: Literal["Intake", "Coach", "Nutrition", "TechnicalManager"]


def preview(body):
    """Resolve without materializing versions or dispatching provider requests."""
    with transaction() as db:
        group = db.scalar(select(MemberGroup).where(MemberGroup.id == body.group_id).with_for_update())
        if not group:
            raise ValueError("Group not found. Read groups and ask which existing group to compare.")
        configured = group.configuration.get("agent_versions", {}).get(body.role)
        if configured:
            current = verify_version(db.get(AgentVersion, configured), body.role)
        else:
            current = SimpleNamespace(
                model=get_settings().default_model,
                prompt=version_admin.base_instruction(body.role),
                max_output_tokens=None,
                reasoning_effort=None,
            )
            current.fingerprint = version_fingerprint(body.role, current.model, current.prompt)
        models = catalog(get_settings())
        matches = [
            m
            for m in models
            if body.model.casefold() in (m["id"].casefold(), m["label"].casefold(), m["provider"].casefold())
        ]
        if len(matches) != 1:
            raise ValueError(
                "Target model is unknown or ambiguous. Read versions and ask for a supported model."
            )
        model = matches[0]
        if current.model == model["id"]:
            raise ValueError("The group already uses this model. Ask for a different model to compare.")
        if not model["configured"]:
            raise ValueError("Target model credentials are not configured. Configure them before comparing.")
        if not get_settings().credentials(model_info(current.model)["provider"]).get_secret_value():
            raise ValueError("Current model credentials are not configured.")
        tokens = current.max_output_tokens or 2048
        validate_options(model["id"], model["provider"], tokens, None)
        cases = version_admin.evaluation_cases(body.role)
        snapshot = {
            "group_id": group.id,
            "group_version": group.version,
            "role": body.role,
            "baseline_fingerprint": current.fingerprint,
            "candidate_fingerprint": version_fingerprint(
                body.role, model["id"], current.prompt, tokens, None
            ),
            "suite_fingerprint": fingerprint({"version": version_admin.SUITE_VERSION, "cases": cases}),
        }
        return {
            **snapshot,
            "fingerprint": fingerprint(snapshot),
            "group_name": group.name,
            "baseline_model": current.model,
            "model": model["id"],
            "planned_calls": len(cases) * 2,
            "cases_per_model": len(cases),
            "same_instructions": True,
            "same_test_cases": True,
        }


def validate_legacy(tool, args):
    if tool.name != "comparison.prepare":
        return
    with transaction() as db:
        candidate = db.get(AgentVersion, tool.target)
        baseline = db.get(AgentVersion, args["baseline_id"])
        if not candidate or not baseline:
            raise ValueError(
                "Comparison needs existing version IDs, not group IDs. Prepare a new comparison in chat."
            )
        if candidate.role != baseline.role or candidate.id == baseline.id:
            raise ValueError("Compare two distinct versions of the same role.")
        verify_version(candidate, candidate.role)
        verify_version(baseline, candidate.role)


def execute(body, snapshot, user):
    if not snapshot:
        raise HTTPException(409, "This comparison has no reviewed snapshot. Ask in chat to prepare it again.")
    with atomic_business_transaction():
        latest = preview(body)
        if latest["fingerprint"] != snapshot["fingerprint"]:
            raise HTTPException(
                409, "Comparison settings changed. Ask in chat for an updated proposal before confirming."
            )
        run = version_admin.compare_current(body.model_copy(update={"model": latest["model"]}), user)
        return version_admin.start_comparison(run["candidate_id"], run["id"], user)
