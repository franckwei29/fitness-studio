"""Fixed role boundaries with group-level restrictions and fresh input checks."""

from sqlalchemy import select

from app.models import ChangeRequest, Member, MemberGroup, ProfileSnapshot, User

PROFILE_FIELDS = {
    "Intake": frozenset(
        {
            "age",
            "goal",
            "experience",
            "available_days",
            "session_minutes",
            "equipment",
            "dietary_restrictions",
            "prescribed_exercise",
            "prescribed_nutrition",
            "medical_supervision",
            "pregnancy_postpartum",
            "postoperative_acute_injury",
            "resolved_past_injury",
        }
    ),
    "Coach": frozenset(
        {
            "age",
            "goal",
            "experience",
            "available_days",
            "session_minutes",
            "equipment",
            "resolved_past_injury",
        }
    ),
    "Nutrition": frozenset({"age", "goal", "dietary_restrictions", "available_days", "session_minutes"}),
    "TechnicalManager": frozenset(),
}
TOOLS = {
    "Intake": frozenset({"propose_intake", "ask_clarification"}),
    "Coach": frozenset({"propose_training", "record_training_feedback", "request_nutrition_review"}),
    "Nutrition": frozenset({"propose_nutrition", "record_nutrition_feedback", "propose_substitution"}),
    "TechnicalManager": frozenset({"analyze_incident", "propose_change", "summarize_operations"}),
}


class PolicyDenied(Exception):
    """The current task is not authorized for this action or data."""


def permitted_fields(role, configuration):
    if role not in PROFILE_FIELDS:
        raise PolicyDenied("The agent role is not recognized.")
    configured = configuration.get("field_permissions", {}).get(role)
    if configured is None:
        return PROFILE_FIELDS[role]
    if not isinstance(configured, list) or any(not isinstance(value, str) for value in configured):
        raise PolicyDenied("Field permissions are invalid.")
    return PROFILE_FIELDS[role] & set(configured)


def filter_profile(role, facts, configuration):
    allowed = permitted_fields(role, configuration)
    return {key: value for key, value in facts.items() if key in allowed}


def authorize_tool(role, tool):
    if tool not in TOOLS.get(role, ()):
        raise PolicyDenied("This tool is not available to the assigned role.")


def verify_group_context(db, item, group, versions):
    configured = group.configuration.get("agent_versions", {}).get(item.role)
    if configured:
        failed = db.scalars(
            select(ChangeRequest).where(
                ChangeRequest.group_id == group.id,
                ChangeRequest.kind == "AgentRelease",
                ChangeRequest.status == "RollbackBlocked",
            )
        )
        if any(
            change.candidate.get("role") == item.role
            and change.candidate.get("agent_version_id") == configured
            for change in failed
        ):
            raise PolicyDenied(
                "This role is degraded after a failed release rollback. An owner must restore a reviewed configuration before it can run."
            )
    for key, expected in item.input_versions.items():
        if key in versions and expected is not None and expected != versions[key]:
            raise PolicyDenied("The task input has changed and must be refreshed.")
    if item.input_versions.get("agent_version"):
        from app.agent_versions import verify_version
        from app.models import AgentVersion

        pinned = item.input_versions["agent_version"]
        verify_version(db.get(AgentVersion, pinned), item.role)
        configured = group.configuration.get("agent_versions", {}).get(item.role)
        if configured and configured != pinned:
            raise PolicyDenied("The configured agent version changed during this task.")


def execution_context(db, item, request):
    if item.request_id != request.id:
        raise PolicyDenied("The work item does not belong to this request.")
    user = db.get(User, request.user_id)
    if not user or not user.active:
        raise PolicyDenied("The account is unavailable.")
    from app.recovery_prerequisites import verify_prerequisites

    verify_prerequisites(db, request.payload.get("recovery_prerequisites", []))
    role = item.role
    if role not in PROFILE_FIELDS:
        raise PolicyDenied("The assigned role is invalid.")
    if request.kind == "Evaluation":
        from app.evaluation_records import verify_evaluation_snapshot
        from app.models import EvaluationRun

        run = db.get(EvaluationRun, item.subject_id)
        if (
            user.role != "Owner"
            or not run
            or run.created_by != user.id
            or item.subject_kind != "Evaluation"
            or run.status != "Running"
            or run.evidence_mode != "Live"
            or run.role != role
        ):
            raise PolicyDenied("This evaluation task is not authorized.")
        verify_evaluation_snapshot(db, run)
        version_id = request.payload.get("agent_version_id")
        if (
            request.id not in (run.summary or {}).get("request_ids", [])
            or version_id not in {run.baseline_id, run.candidate_id}
            or request.payload.get("case_key") not in {case["key"] for case in run.cases}
        ):
            raise PolicyDenied("Evaluation inputs do not belong to the prepared comparison.")
        return {
            "role": role,
            "profile": {},
            "versions": {"agent_version": version_id},
            "scope_keys": ["global", f"role:{role}"],
        }
    if role == "TechnicalManager":
        if user.role != "Owner":
            raise PolicyDenied("Owner access is required for operations.")
        identifier = request.payload.get("operations_group_id")
        group = (
            db.get(MemberGroup, identifier)
            if identifier
            else db.scalar(select(MemberGroup).where(MemberGroup.name == "Default"))
        )
        if not group:
            raise PolicyDenied("The operations agent configuration is unavailable.")
        versions = {"group": group.version, "group_id": group.id}
        verify_group_context(db, item, group, versions)
        return {
            "role": role,
            "profile": {},
            "versions": versions,
            "group_id": group.id,
            "scope_keys": ["global", "role:TechnicalManager"],
        }
    if user.role != "Member":
        raise PolicyDenied("This task requires a member account.")
    member = db.scalar(select(Member).where(Member.user_id == user.id))
    if role == "Intake" and not member:
        group = db.scalar(select(MemberGroup).where(MemberGroup.name == "Default"))
        if not group:
            raise PolicyDenied("The default intake configuration is unavailable.")
        versions = {"group": group.version, "group_id": group.id}
        verify_group_context(db, item, group, versions)
        return {
            "role": role,
            "profile": {},
            "versions": versions,
            "group_id": group.id,
            "scope_keys": ["global", "role:Intake"],
        }
    if not member:
        raise PolicyDenied("A confirmed member profile is required.")
    if role != "Intake" and member.status != "Accepted":
        raise PolicyDenied("Admission must be valid before planning can continue.")
    group = db.get(MemberGroup, member.group_id)
    if not group:
        raise PolicyDenied("Member group configuration is unavailable.")
    versions = {"profile": member.profile_version, "group": group.version, "group_id": group.id}
    verify_group_context(db, item, group, versions)
    from app.planning_context import verify_planning_baseline

    verify_planning_baseline(db, request, member.id)
    required = set()
    if role == "Coach":
        required.add("training")
    if role == "Nutrition":
        required.add("nutrition")
    if request.kind == "Combined":
        required.update(["training", "nutrition"])
    if request.kind == "FoodSubstitution":
        required.update(["nutrition", "substitutions"])
    if request.kind == "CycleReview" and request.payload.get("coordinate_nutrition"):
        required.update(["training", "nutrition"])
    capabilities = group.configuration.get("capabilities", {})
    if any(capabilities.get(key) is not True for key in required):
        raise PolicyDenied("This capability is disabled for the member group.")
    snapshot = db.scalar(
        select(ProfileSnapshot).where(
            ProfileSnapshot.member_id == member.id, ProfileSnapshot.version == member.profile_version
        )
    )
    if not snapshot:
        raise PolicyDenied("The confirmed profile snapshot is missing.")
    return {
        "role": role,
        "profile": filter_profile(role, snapshot.facts, group.configuration),
        "versions": versions,
        "member_id": member.id,
        "group_id": group.id,
        "scope_keys": ["global", f"group:{group.id}", f"member:{member.id}", f"role:{role}"],
    }
