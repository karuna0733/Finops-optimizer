"""
guardrails.py

Deterministic safety layer that sits between the LLM agents and any real
infrastructure action. This is what makes the system "production-grade"
rather than a toy: the LLM's output is NEVER trusted directly.

Two guardrails implemented per the spec:
1. Hallucination Guardrail -- agent's proposed instance type must exist in
   an explicit allow-list, or the action is rejected outright.
2. Cooldown Guardrail -- a given service can only be resized once per
   COOLDOWN_HOURS, even if the agent insists otherwise.
"""

from datetime import datetime, timedelta, timezone
import os
import sys
from typing import Optional

from pydantic import BaseModel, field_validator, ValidationError

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "db"))
import db_helper

ALLOWED_INSTANCE_TYPES = {
    "t3.micro", "t3.small", "t3.medium", "t3.large", "t3.xlarge"
}

COOLDOWN_HOURS = 4


class ResizeProposal(BaseModel):
    """Strict schema the DevOps Agent's output MUST conform to before any
    code generation or execution happens."""

    service_id: str
    current_instance_type: str
    proposed_instance_type: str
    reason: str
    estimated_monthly_savings_usd: float

    @field_validator("current_instance_type", "proposed_instance_type")
    @classmethod
    def must_be_allowed_type(cls, v: str) -> str:
        if v not in ALLOWED_INSTANCE_TYPES:
            raise ValueError(
                f"'{v}' is not an allowed instance type. "
                f"Allowed: {sorted(ALLOWED_INSTANCE_TYPES)}"
            )
        return v

    @field_validator("estimated_monthly_savings_usd")
    @classmethod
    def savings_must_be_sane(cls, v: float) -> float:
        # Sanity bound -- no single t3-class resize realistically saves
        # more than ~$200/mo. Anything above that is almost certainly a
        # hallucinated number and should be rejected for manual review.
        if v < 0 or v > 200:
            raise ValueError(f"Savings estimate {v} is out of plausible bounds")
        return v


class GuardrailRejection(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def validate_proposal(raw_proposal: dict) -> ResizeProposal:
    """Step 1 guardrail: schema + allow-list validation.
    Raises GuardrailRejection if the LLM hallucinated something invalid."""
    try:
        return ResizeProposal(**raw_proposal)
    except ValidationError as e:
        raise GuardrailRejection(f"Schema/allow-list validation failed: {e}")


def check_cooldown(service_id: str, db_config: dict) -> Optional[datetime]:
    """Step 2 guardrail: cooldown window check.
    Returns None if the service is eligible for modification.
    Returns the datetime it becomes eligible again if still cooling down."""
    conn, db_type = db_helper.get_db_connection(db_config)
    cur = conn.cursor()
    sql = "SELECT last_modified FROM resize_cooldown WHERE service_id = %s"
    sql = db_helper.translate_sql(sql, db_type)
    cur.execute(sql, (service_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row is None:
        return None

    last_modified = row[0]
    if isinstance(last_modified, str):
        # sqlite returns string
        last_modified = last_modified.replace('T', ' ')
        if '.' in last_modified:
            last_modified = last_modified.split('.')[0]
        try:
            last_modified = datetime.strptime(last_modified, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            last_modified = datetime.strptime(last_modified, "%Y-%m-%d %H:%M:%S%z")
        last_modified = last_modified.replace(tzinfo=timezone.utc)
    elif last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=timezone.utc)

    eligible_at = last_modified + timedelta(hours=COOLDOWN_HOURS)
    now = datetime.now(timezone.utc)

    if now < eligible_at:
        return eligible_at
    return None


def record_modification(service_id: str, db_config: dict):
    """Mark a service as just-modified, starting its cooldown clock."""
    conn, db_type = db_helper.get_db_connection(db_config)
    cur = conn.cursor()
    sql = """
        INSERT INTO resize_cooldown (service_id, last_modified)
        VALUES (%s, now())
        ON CONFLICT (service_id) DO UPDATE SET last_modified = now()
        """
    sql = db_helper.translate_sql(sql, db_type)
    cur.execute(sql, (service_id,))
    conn.commit()
    cur.close()
    conn.close()


def enforce_guardrails(raw_proposal: dict, db_config: dict) -> ResizeProposal:
    """Run both guardrails in sequence. Raises GuardrailRejection on failure,
    otherwise returns the validated proposal, cleared for execution."""
    proposal = validate_proposal(raw_proposal)

    cooldown_until = check_cooldown(proposal.service_id, db_config)
    if cooldown_until is not None:
        raise GuardrailRejection(
            f"Service '{proposal.service_id}' is in cooldown until "
            f"{cooldown_until.isoformat()}. Modified too recently."
        )

    return proposal
