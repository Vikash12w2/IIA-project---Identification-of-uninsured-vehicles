"""Insurance coverage rules used by the independently deployed vehicle services."""
from datetime import date


def _day(policy, field):
    try:
        return date.fromisoformat(str(policy.get(field))[:10])
    except ValueError:
        return None


def policy_status(policy, today):
    start, end = _day(policy, "start_date"), _day(policy, "end_date")
    if policy.get("status") == "Cancelled":
        return "CANCELLED"
    if policy.get("status") == "Lapsed" or (end and end < today):
        return "EXPIRED"
    if policy.get("status") == "Active" and start and end and start <= today <= end:
        return "VALID"
    return "NOT_INSURED"


def evaluate_insurance(policies, today):
    if not policies:
        return "NOT_INSURED", None
    def priority(policy):
        return (policy_status(policy, today) == "VALID", _day(policy, "end_date") or date.min,
                int(policy.get("policy_id") or 0))
    policy = max(policies, key=priority)
    return policy_status(policy, today), policy
