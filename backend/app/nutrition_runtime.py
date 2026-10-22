"""Nutrition candidates are validated against a specific training version."""

from datetime import timedelta

from sqlalchemy import func, select

from app.activation import PAIR_RULE_VERSION, pair_fingerprint, plan_fingerprint
from app.agent_contracts import COMMON_INSTRUCTION
from app.agent_graph import graph_config, persistent_graph
from app.agent_versions import resolve_version
from app.candidate_correction import correction_context, queue_correction
from app.catalogue import food_catalogue
from app.cycle_requests import cycle_baseline
from app.database import transaction
from app.dietary_policy import dietary_constraints
from app.model_gateway import generate
from app.models import (
    Approval,
    CurrentPlans,
    Member,
    MemberGroup,
    Message,
    ModelCall,
    PlanPair,
    PlanVersion,
    Request,
    ValidationResult,
    WorkItem,
    now,
)
from app.nutrition import NutritionProposal, validate_nutrition
from app.nutrition_reuse import retained_nutrition
from app.planning_context import revision_context
from app.policy import PolicyDenied, execution_context
from app.substitution import check_substitution, substitution_baseline
from app.work_queue import event, lock_claim

INSTRUCTION = (
    COMMON_INSTRUCTION
    + """
Your role is Nutrition. Produce a general food-based daily meal example, not a
medical prescription or a claim of personalised energy adequacy. Use only supplied
food IDs, exact preparation states, and edible gram portions. Honour all supplied
restrictions. Do not invent nutrient values: the application calculates them.
Reference the supplied training_version_id exactly. Describe limitations, including
that individual energy requirements have not been clinically assessed.
"""
)


def training_for(db, item, authorized):
    if item.subject_kind == "TrainingPlan":
        identifier = item.subject_id
    else:
        current = db.get(CurrentPlans, authorized["member_id"])
        identifier = current.training_id if current else None
    plan = db.get(PlanVersion, identifier) if identifier else None
    if (
        not plan
        or plan.member_id != authorized["member_id"]
        or plan.kind != "Training"
        or plan.status not in {"Validated", "Active"}
        or plan.profile_version != authorized["versions"]["profile"]
        or plan.config_version != authorized["versions"]["group"]
        or plan.fingerprint != plan_fingerprint(plan)
    ):
        raise PolicyDenied("A current validated training version is required for nutrition planning.")
    return plan


def run_nutrition(claim):
    def prepare(state):
        with transaction() as db:
            item, request = lock_claim(db, claim)
            authorized = execution_context(db, item, request)
            cycle_baseline(db, request, authorized["member_id"])
            training = training_for(db, item, authorized)
            baseline = substitution_baseline(db, request, authorized["member_id"])
            allergens, tags = dietary_constraints(authorized["profile"].get("dietary_restrictions"))
            version = resolve_version(db, item, authorized, INSTRUCTION)
            foods = [
                food.model_dump(mode="json")
                for food in food_catalogue().values()
                if not set(allergens) & set(food.allergens) and set(tags) <= set(food.dietary_tags)
            ]
            if not foods:
                raise PolicyDenied("No supported foods match the confirmed restrictions.")
            return {
                "context": {
                    "profile": authorized["profile"],
                    "foods": foods,
                    "training_version_id": training.id,
                    "training": training.content,
                    "request": request.payload["content"],
                    "agent_version_id": version.id,
                    **correction_context(db, item, request, authorized),
                    **revision_context(db, request, authorized, "Nutrition"),
                    "retained_nutrition": retained_nutrition(db, request, authorized, training),
                    **(
                        {
                            "substitution": request.payload["substitution"],
                            "baseline_plan": baseline.content,
                            "change_scope": "Replace only the selected meal portion (zero-based indices). Preserve every other meal and portion exactly. You may update preparation instructions for the selected meal.",
                        }
                        if baseline
                        else {}
                    ),
                },
                "input_versions": authorized["versions"],
            }

    def infer(state):
        retained = state["context"].get("retained_nutrition")
        if retained:
            return {"candidate": {"proposal": retained["proposal"], "call_id": None, "retained": retained}}
        output, call_id = generate(claim, INSTRUCTION, state["context"], NutritionProposal, 4096)
        return {"candidate": {"proposal": output.model_dump(mode="json"), "call_id": call_id}}

    def validate(state):
        allergens, tags = dietary_constraints(state["context"]["profile"].get("dietary_restrictions"))
        result = validate_nutrition(
            NutritionProposal.model_validate(state["candidate"]["proposal"]),
            food_catalogue(),
            state["context"]["training_version_id"],
            allergens,
            tags,
        )
        return {"validation": result, "requires_confirmation": False}

    def commit(state):
        output = NutritionProposal.model_validate(state["candidate"]["proposal"])
        with transaction() as db:
            existing = db.scalar(select(WorkItem).where(WorkItem.id == claim.work_id).with_for_update())
            if existing and existing.status in {"Completed", "Superseded"}:
                return {"outcome": db.get(Request, existing.request_id).result}
            item, request = lock_claim(db, claim)
            authorized = execution_context(db, item, request)
            member = db.scalar(select(Member).where(Member.id == authorized["member_id"]).with_for_update())
            cycle_baseline(db, request, member.id)
            training = training_for(db, item, authorized)
            baseline = substitution_baseline(db, request, authorized["member_id"])
            allergens, tags = dietary_constraints(authorized["profile"].get("dietary_restrictions"))
            validation = validate_nutrition(output, food_catalogue(), training.id, allergens, tags)
            if baseline:
                validation["violations"].extend(
                    check_substitution(output, baseline.content, request.payload["substitution"])
                )
                validation["passed"] = not validation["violations"]
                validation["basis"]["substitution"] = request.payload["substitution"]
            call = None
            if state["candidate"].get("retained"):
                retained = retained_nutrition(db, request, authorized, training)
                if (
                    not retained
                    or retained != state["candidate"]["retained"]
                    or retained["proposal"] != output.model_dump(mode="json")
                ):
                    raise PolicyDenied("Nutrition reuse evidence changed before publication.")
                validation["basis"]["reuse"] = {
                    key: value for key, value in retained.items() if key != "proposal"
                }
            else:
                call = db.get(ModelCall, state["candidate"]["call_id"])
                if not call or call.request_id != request.id or call.status != "Settled":
                    raise ValueError("A settled model result is required.")
            number = (
                db.scalar(
                    select(func.max(PlanVersion.version)).where(
                        PlanVersion.member_id == member.id, PlanVersion.kind == "Nutrition"
                    )
                )
                or 0
            ) + 1
            current = db.get(CurrentPlans, member.id)
            content = output.model_dump(mode="json") | {"nutrient_totals": validation["basis"]["totals"]}
            plan = PlanVersion(
                member_id=member.id,
                request_id=request.id,
                kind="Nutrition",
                version=number,
                status="Validated" if validation["passed"] else "Invalid",
                profile_version=member.profile_version,
                config_version=authorized["versions"]["group"],
                agent_version_id=state["context"]["agent_version_id"],
                baseline_id=current.nutrition_id if current else None,
                training_input_id=training.id,
                content=content,
                rationale=output.rationale,
                fingerprint="pending",
            )
            db.add(plan)
            db.flush()
            plan.fingerprint = plan_fingerprint(plan)
            db.add(
                ValidationResult(
                    subject_fingerprint=plan.fingerprint,
                    rule_version=validation["rule_version"],
                    passed=validation["passed"],
                    violations=validation["violations"],
                    basis=validation["basis"],
                )
            )
            pair = None
            if validation["passed"]:
                pair_validation = ValidationResult(
                    subject_fingerprint=pair_fingerprint(training, plan),
                    rule_version=PAIR_RULE_VERSION,
                    passed=True,
                    violations=[],
                    basis={
                        "training_fingerprint": training.fingerprint,
                        "nutrition_fingerprint": plan.fingerprint,
                        "profile_version": member.profile_version,
                        "group_version": authorized["versions"]["group"],
                    },
                )
                db.add(pair_validation)
                db.flush()
                pair = PlanPair(
                    member_id=member.id,
                    training_id=training.id,
                    nutrition_id=plan.id,
                    validation_id=pair_validation.id,
                )
                db.add(pair)
                db.flush()
                group = db.get(MemberGroup, member.group_id)
                if group.configuration.get("review_required"):
                    db.add(
                        Approval(
                            subject_kind="PlanPair",
                            subject_id=pair.id,
                            subject_version=1,
                            fingerprint=pair_fingerprint(training, plan),
                            scope={"member_id": member.id},
                            context={
                                "training": training.content,
                                "nutrition": plan.content,
                                "validation": validation,
                            },
                            expires_at=now() + timedelta(hours=24),
                        )
                    )
            result = {
                "status": "Completed" if pair else "Waiting",
                "nutrition_id": plan.id,
                "pair_id": pair.id if pair else None,
                "call_id": call.id if call else None,
                "generation_method": "Model" if call else "ValidatedRetention",
                "validation": validation,
            }
            item.status = result["status"]
            if pair and request.kind == "CycleReview":
                result["status"] = "AwaitingConfirmation"
            request.status = result["status"]
            item.wait_reason = None if pair else "CandidateValidation"
            if not pair:
                result.update(queue_correction(db, item, request, plan))
                request.status = result["status"]
            item.lease_until = None
            request.result = result
            db.add(
                Message(
                    session_id=request.session_id,
                    sender="Nutrition",
                    content="Your plan pair is ready for review and confirmation."
                    if pair
                    else (
                        "The nutrition candidate failed validation. A bounded correction is queued; your active plan has not changed."
                        if result["status"] == "Queued"
                        else "Nutrition correction stopped at its limit. Review the validation issues before submitting a new request. No invalid plan was activated."
                    ),
                    cards=[{"type": "PlanPairReview", "pair_id": pair.id if pair else None}],
                )
            )
            event(db, request, "nutrition_candidate", **result)
            return {"outcome": result}

    with persistent_graph(prepare=prepare, generate=infer, validate=validate, commit=commit) as graph:
        config = graph_config(claim.work_id)
        snapshot = graph.get_state(config)
        if snapshot.values:
            return graph.invoke(None, config) if snapshot.next else snapshot.values
        return graph.invoke(
            {"request_id": claim.request_id, "work_id": claim.work_id, "role": claim.role}, config
        )
