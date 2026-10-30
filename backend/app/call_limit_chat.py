"""Local typed quota previews; natural language never authorizes a write."""

import re

from sqlalchemy import select

from app.call_limits import entry, policy_lock
from app.model_catalog import MODELS
from app.models import Member, MemberGroup, User


def quota_card(db, content, user):
    text = content.casefold()
    if not re.search(r"calls?|allowance|quota|hourly|daily|额度|配额|每小时|每天|limit", text):
        return None
    if re.search(r"budget|spend|\busd\b|\$|预算|金额|花费", text):
        return None
    if re.search(r"unknown|uncertain|release.*slot|未知|释放", text):
        return None
    policy_lock(db)
    changing = bool(
        re.search(
            r"\b(set|give|change|raise|lower|increase|decrease|restore|reset)\b|设置|恢复|调整|提高|降低|改成|改为",
            text,
        )
    )
    restoring = bool(re.search(r"restore|reset|恢复", text))
    number_text = re.sub(r"gpt-6|gemini-3\.5", "", text)
    numbers = re.findall(r"(?<![\w.])[+-]?\d[\d,]*(?![\w.])", number_text)
    calls = int(numbers[0].replace(",", "")) if len(numbers) == 1 else None
    scopes = []
    if re.search(r"owner|my (?:daily|own)|老板", text):
        scopes = [f"owner:{user.id}"]
    elif re.search(r"initial|before membership|入会前|初始", text):
        scopes = ["initial"]
    else:
        for model, info in MODELS.items():
            if (
                ("luna" in text and "luna" in model)
                or ("gemini" in text and info["provider"] == "gemini")
                or model in text
            ):
                scopes.append(f"model:{info['provider']}:{model}")
        if not scopes:
            groups = list(db.scalars(select(MemberGroup)))
            members = list(db.execute(select(Member, User).join(User, Member.user_id == User.id)))
            exact = [
                (m, a) for m, a in members if a.display_name.casefold() in text or a.email.casefold() in text
            ]
            matches = exact or [
                (m, a)
                for m, a in members
                if any(
                    re.search(r"(?<!\w)" + re.escape(token.casefold()) + r"(?!\w)", text)
                    for token in re.findall(r"[\w@.-]{3,}", a.display_name)
                    if token.casefold() not in {"member", "user", "test", "default"}
                )
            ]
            if re.search(r"custom allowances|custom allowance|个人例外|自定义额度", text) and not changing:
                scopes = [
                    f"member:{m.id}" for m, _ in members if entry(db, f"member:{m.id}")["calls"] is not None
                ]
            elif matches:
                scopes = [f"member:{m.id}" for m, _ in matches]
            else:
                scopes = [
                    f"group:{g.id}"
                    for g in groups
                    if re.search(r"(?<!\w)" + re.escape(g.name.casefold()) + r"(?!\w)", text)
                ]
    valid = (not changing) or restoring or calls is not None and 1 <= calls <= 1000000
    hourly = bool(re.search(r"hourly|per hour|每小时", text))
    daily = bool(re.search(r"daily|per day|每天", text))
    if changing and scopes:
        model_scope = all(scope.startswith("model:") for scope in scopes)
        if (model_scope and daily) or (not model_scope and hourly):
            valid = False
    if not scopes and not changing:
        custom_lookup = bool(re.search(r"custom allowances|custom allowance|个人例外|自定义额度", text))
        if custom_lookup:
            return {
                "type": "OwnerQuota",
                "entries": [],
                "message": "No members currently have custom allowances. Open settings to review group defaults.",
            }
        scopes = [f"model:{info['provider']}:{model}" for model, info in MODELS.items()]
        scopes += [f"group:{g.id}" for g in db.scalars(select(MemberGroup))]
        scopes += [f"owner:{user.id}", "initial"]
    if not scopes or not valid or restoring and any(not s.startswith("member:") for s in scopes):
        return {
            "type": "OwnerQuota",
            "entries": [],
            "message": "Which allowance should I change? Choose a studio model hourly limit, a member group's daily allowance per member, a member's personal allowance, or your owner allowance. Include the name and a whole number between 1 and 1,000,000.",
        }
    return {
        "type": "OwnerQuota",
        "message": (
            "Choose the intended object before confirming a change."
            if len(scopes) > 1 and changing
            else "Review the scope and current allowance below. Saving does not reset any usage."
        ),
        "entries": [
            {"rule": entry(db, scope), "editing": changing, "proposed_calls": None if restoring else calls}
            for scope in scopes
        ],
    }
