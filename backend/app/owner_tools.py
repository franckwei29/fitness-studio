"""Typed, allowlisted owner tools. Models propose; existing services enforce changes."""

import json
from datetime import date

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.database import transaction
from app.models import Approval, Request


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=80)
    target: str = Field(default="", max_length=200)
    arguments: str = Field(default="{}", max_length=24000)


def arguments(tool):
    value = json.loads(tool.arguments)
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be an object.")
    return value


def write_registry():
    from app import (
        admission,
        agent_release,
        budget_admin,
        call_limits,
        group_configuration,
        incidents,
        member_admin,
        rollback_recovery,
        version_admin,
    )

    # Entries reuse the same validated handlers as the business pages. There is
    # no arbitrary HTTP, SQL, filesystem, shell or remote-export tool.
    return {
        "configuration.propose": (group_configuration.Proposal, group_configuration.propose, True, "groups"),
        "configuration.review": (group_configuration.Review, group_configuration.review, True, "groups"),
        "configuration.execute": (None, group_configuration.execute, True, "groups"),
        "approval.decide": (admission.Decision, admission.decide, True, "approvals"),
        "budget.change": (budget_admin.BudgetChange, budget_admin.change_budget, False, "budgets"),
        "allowance.change": (call_limits.LimitChange, call_limits.save_limit, False, "budgets"),
        "member.assign": (member_admin.Assignment, member_admin.assign_group, True, "members"),
        "members.assign": (member_admin.BulkAssignment, member_admin.assign_members, False, "members"),
        "comparison.current": (
            version_admin.CurrentModelComparison,
            version_admin.compare_current,
            False,
            "versions",
        ),
        "candidate.save": (version_admin.Candidate, version_admin.create_candidate, False, "versions"),
        "comparison.prepare": (
            version_admin.EvaluationPreparation,
            version_admin.prepare_comparison,
            True,
            "versions",
        ),
        "release.propose": (agent_release.ReleaseProposal, agent_release.propose_release, False, "releases"),
        "release.review": (agent_release.ReleaseDecision, agent_release.review_release, True, "releases"),
        "release.execute": (None, agent_release.execute_release, True, "releases"),
        "release.confirm": (
            agent_release.ReleaseConfirmation,
            agent_release.confirm_release,
            True,
            "releases",
        ),
        "release.rollback": (agent_release.RollbackRequest, agent_release.rollback_release, True, "releases"),
        "release.recovery": (
            rollback_recovery.RecoveryProposal,
            rollback_recovery.propose_recovery,
            True,
            "releases",
        ),
        "incident.prerequisites": (
            incidents.RecoveryPrerequisites,
            incidents.set_prerequisites,
            True,
            "incidents",
        ),
        "incident.recover": (incidents.RecoveryRequest, incidents.request_recovery, True, "incidents"),
        "incident.verify": (None, incidents.verify_recovery, True, "incidents"),
        "incident.close": (incidents.Closure, incidents.close_unresolved, True, "incidents"),
    }


def tool_catalog():
    return {
        "read": {
            "trial_requests": "{}; target release ID; exact group/version request records for operational handoff",
            "workspace": "{area:attention|members|approvals|groups|budgets|incidents|versions|releases|dashboard|requests|notifications|usage, query?:name, offset?:0}; target optional exact record",
            "groups": "{}; configurations, capabilities and fixed field boundaries",
            "members": "{query?:name,offset?:0}; identities, group and status only",
            "budgets": "{}; spending limits and fingerprints",
            "allowance": "{}; target exact scope key such as group:UUID",
            "versions": "{}; target optional version ID; otherwise version metadata and supported models",
            "comparison": "{run_id:UUID}; target candidate ID; fixed synthetic test outputs, per-model metrics and checks; use these to explain results, not invented quality scores",
            "changes": "{}; target optional change ID; configuration/release records",
            "approval": "{}; target approval ID; metadata only, private evidence is available locally to the owner",
            "incident": "{}; target incident ID; restricted operational evidence",
            "trace": "{}; target request ID; redacted operational evidence",
            "requests": "{member_id?:UUID,offset?:0}; task IDs, roles, states only",
            "overview": "{start?:YYYY-MM-DD,end?:YYYY-MM-DD}; dated metrics",
        },
        "write": {
            name: {
                "target_required": targeted,
                "arguments_schema": schema.model_json_schema()
                if schema
                else {"type": "object", "properties": {}},
            }
            for name, (schema, _, targeted, _) in write_registry().items()
        },
        "panels": [
            "dashboard",
            "approvals",
            "members",
            "groups",
            "budgets",
            "incidents",
            "versions",
            "releases",
            "requests",
            "notifications",
        ],
        "rules": "Writes only become saved previews. Human confirmation runs the exact preview. Comparison start, local-slot release, usage acknowledgement, cancellation and exports are available in the in-conversation panel. Never invent a rating or call an unlisted tool.",
    }


def read_tool(tool, user):
    from app import (
        budget_admin,
        call_limits,
        group_configuration,
        member_admin,
        operations,
        owner_workspace,
        version_admin,
    )
    from app.admission import view
    from app.models import ChangeRequest, Member, WorkItem

    args = arguments(tool)
    with transaction() as db:
        if tool.name == "workspace":
            area = args.get("area", "attention")
            if area not in {
                "attention",
                "members",
                "approvals",
                "groups",
                "budgets",
                "incidents",
                "versions",
                "releases",
                "dashboard",
                "requests",
                "notifications",
                "usage",
            }:
                raise ValueError("Unsupported workspace.")
            result = owner_workspace.workspace(
                area,
                str(args.get("query", ""))[:200],
                tool.target or None,
                max(0, int(args.get("offset", 0))),
                10,
                user,
            )
        elif tool.name == "groups":
            result = {
                "groups": group_configuration.groups(user),
                "fixed_field_boundaries": group_configuration.group_policy(user),
            }
        elif tool.name == "members":
            rows = member_admin.members(user)
            needle = str(args.get("query", "")).casefold()
            rows = [r for r in rows if not needle or needle in str(r.get("display_name", "")).casefold()]
            offset = max(0, int(args.get("offset", 0)))
            result = {"total": len(rows), "members": rows[offset : offset + 15]}
        elif tool.name == "budgets":
            result = budget_admin.budgets(user)
        elif tool.name == "allowance":
            result = call_limits.entry(db, tool.target)
        elif tool.name == "versions":
            if tool.target:
                result = version_admin.version_detail(tool.target, user)
            else:
                from app.admission import fingerprint
                from app.models import AgentVersion, MemberGroup

                data = version_admin.versions(user)
                groups = list(db.scalars(select(MemberGroup)))
                defaults = {}
                for role in ("Intake", "Coach", "Nutrition", "TechnicalManager"):
                    digest = fingerprint(
                        {
                            "role": role,
                            "model": data["default_model"],
                            "prompt": version_admin.base_instruction(role),
                        }
                    )
                    initial = db.scalar(select(AgentVersion).where(AgentVersion.fingerprint == digest))
                    defaults[role] = {
                        "version_id": initial.id if initial else None,
                        "model": data["default_model"],
                        "base": "Current immutable application instructions; instantiated on first use if version_id is null",
                        "inherited_groups": [
                            {"id": g.id, "name": g.name}
                            for g in groups
                            if not g.configuration.get("agent_versions", {}).get(role)
                        ],
                    }
                result = data | {
                    "runtime_defaults": defaults,
                    "versions": [
                        {
                            k: r.get(k)
                            for k in (
                                "id",
                                "label",
                                "role",
                                "model",
                                "max_output_tokens",
                                "configured_groups",
                            )
                        }
                        for r in data["versions"]
                    ],
                }
        elif tool.name == "comparison":
            data = version_admin.comparison_detail(tool.target, args["run_id"], user)
            result = {
                "run": data["run"],
                "usage": data["usage"],
                "models": data["models"],
                "results": [
                    {
                        k: r.get(k)
                        for k in (
                            "id",
                            "case_key",
                            "agent_version_id",
                            "passed",
                            "latency_ms",
                            "checks",
                            "output",
                        )
                    }
                    for r in data["results"]
                ],
            }
        elif tool.name == "trial_requests":
            from app.agent_release import trial_requests

            result = trial_requests(tool.target, user)
        elif tool.name == "changes":
            rows = db.scalars(select(ChangeRequest).order_by(ChangeRequest.created_at.desc()).limit(30))
            result = [view(r) for r in rows if not tool.target or r.id == tool.target]
        elif tool.name == "approval":
            row = db.get(Approval, tool.target)
            if not row:
                raise ValueError("Approval unavailable.")
            result = {
                k: getattr(row, k)
                for k in (
                    "id",
                    "subject_kind",
                    "subject_id",
                    "subject_version",
                    "fingerprint",
                    "decision",
                    "expires_at",
                )
            }
        elif tool.name == "incident":
            from app.incident_context import incident_context
            from app.models import Incident

            result = incident_context(db, tool.target)
            incident = db.get(Incident, tool.target)
            work = db.get(WorkItem, incident.evidence.get("work_id"))
            result["current_fence"] = work.fence if work else None
            result["required_changes"] = incident.evidence.get("required_changes", [])
        elif tool.name == "trace":
            result = operations.trace(tool.target, user)
        elif tool.name == "requests":
            statement = select(Request).order_by(Request.created_at.desc())
            if args.get("member_id"):
                member = db.get(Member, args["member_id"])
                if not member:
                    raise ValueError("Member unavailable.")
                statement = statement.where(Request.user_id == member.user_id)
            rows = db.scalars(statement.offset(max(0, int(args.get("offset", 0)))).limit(15))
            result = [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "status": r.status,
                    "created_at": r.created_at,
                    "model_calls": r.model_calls,
                    "deadline": r.deadline,
                }
                for r in rows
            ]
        elif tool.name == "overview":
            result = operations.dashboard(
                user,
                date.fromisoformat(args["start"]) if args.get("start") else None,
                date.fromisoformat(args["end"]) if args.get("end") else None,
            )
        else:
            raise ValueError("Unknown read tool.")
    return jsonable_encoder(result)


def validate_action(tool):
    registry = write_registry()
    if tool.name not in registry:
        raise ValueError("This action is not available to the agent. Use its in-conversation panel.")
    schema, _, targeted, _ = registry[tool.name]
    if targeted and not tool.target:
        raise ValueError("Choose an exact target before preparing an action.")
    args = arguments(tool)
    if schema:
        args = schema.model_validate(args).model_dump(mode="json")
    elif args:
        raise ValueError("This action takes no additional arguments.")
    return tool.model_copy(update={"arguments": json.dumps(args)})


def budget_baseline(tool, evidence):
    """Capture reviewed limits, excluding usage accrued by the investigation itself."""
    if tool.name != "budget.change":
        return None
    args = arguments(tool)
    for item in reversed(evidence):
        if item["tool"]["name"] != "budgets" or not isinstance(item["result"], list):
            continue
        rows = item["result"]
        row = next((r for r in rows if r["scope_key"] == args["scope_key"]), None)
        if row and row["fingerprint"] == args.get("expected_fingerprint"):
            return {k: row[k] for k in ("id", "scope_key", "currency", "limit_micros")}
        if row is None and args.get("expected_fingerprint") is None:
            return {"scope_key": args["scope_key"], "absent": True}
    raise ValueError("Read the current budgets before proposing a spending-limit change.")


def execute_action(tool, user, baseline=None):
    tool = validate_action(tool)
    schema, handler, targeted, page = write_registry()[tool.name]
    args = arguments(tool)
    if tool.name == "budget.change" and baseline is not None:
        from app.budget_admin import budget_view
        from app.models import BudgetAccount

        with transaction() as db:
            db.scalar(select(BudgetAccount).where(BudgetAccount.scope_key == "global").with_for_update())
            row = db.scalar(
                select(BudgetAccount).where(BudgetAccount.scope_key == args["scope_key"]).with_for_update()
            )
            current = (
                {k: getattr(row, k) for k in ("id", "scope_key", "currency", "limit_micros")}
                if row
                else {"scope_key": args["scope_key"], "absent": True}
            )
            if current != baseline:
                raise HTTPException(409, "The spending limit changed. Ask for an updated proposal.")
            args["expected_fingerprint"] = budget_view(row)["fingerprint"] if row else None
    body = schema.model_validate(args) if schema else None
    # Decisions for configuration/release approvals must go through their own
    # lifecycle handlers, not the generic plan/intake decision service.
    if tool.name == "approval.decide":
        from app import agent_release, group_configuration

        with transaction() as db:
            approval = db.get(Approval, tool.target)
            if not approval:
                raise HTTPException(404, "Approval unavailable.")
            if approval.subject_kind == "GroupConfiguration":
                result = group_configuration.review(
                    approval.subject_id, group_configuration.Review(**args), user
                )
                return jsonable_encoder(result), {"page": "approvals", "id": approval.id}
            if approval.subject_kind == "AgentRelease":
                result = agent_release.review_release(
                    approval.subject_id, agent_release.ReleaseDecision(**args), user
                )
                return jsonable_encoder(result), {"page": "approvals", "id": approval.id}
    positional = ([tool.target] if targeted else []) + ([body] if schema else [])
    result = jsonable_encoder(handler(*positional, user=user))
    identifier = result.get("id", tool.target) if isinstance(result, dict) else tool.target
    target = {"page": page}
    if identifier:
        target["id"] = identifier
    if tool.name == "release.recovery":
        target["page"] = "groups"
        target["section"] = "change"
    if tool.name.startswith("configuration."):
        target["section"] = "change"
    if tool.name == "comparison.current":
        target.update(id=result["candidate_id"], section="evaluation:" + result["id"])
    if tool.name == "members.assign":
        target.pop("id", None)
    if tool.name == "comparison.prepare":
        target.update(id=tool.target, section="evaluation:" + result["id"])
    if tool.name == "allowance.change":
        target.update(id=args["scope_key"], section="call-limit")
    return result, target
