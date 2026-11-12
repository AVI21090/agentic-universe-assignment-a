"""Small, independently testable business rules."""
from __future__ import annotations

from typing import Any, Iterable


def latest_disposition(
    set_candidates: Iterable[tuple[int | None, str, str]],
    override_candidates: Iterable[tuple[int | None, str, str]],
) -> str | None:
    """Overrides take precedence; within each kind, latest event time wins."""
    overrides = list(override_candidates)
    candidates = overrides if overrides else list(set_candidates)
    if not candidates:
        return None
    # Missing timestamps sort before real timestamps; event_id breaks exact ties.
    return max(
        candidates,
        key=lambda item: (-1 if item[0] is None else item[0], item[1]),
    )[2]


def lead_bucket(calls: list[Any], dispositions: dict[str, dict[str, Any]]) -> str:
    if not calls:
        return "yet_to_reach_out"
    for call in calls:
        code = call.effective_disposition
        if code is not None and dispositions.get(code, {}).get("is_terminal", False):
            return "completed"
    if all(call.telephony_failed for call in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"

