from __future__ import annotations

from dataclasses import dataclass


STATES = (
    "idea",
    "research",
    "topic_approved",
    "script",
    "script_approved",
    "production",
    "qa",
    "private_uploaded",
    "publish_approved",
    "published",
)

# States that require an explicit human approval note.
HUMAN_APPROVAL_STATES = {"topic_approved", "script_approved", "publish_approved", "published"}

# States that allow going back one step (rejection / revision).
REVERTABLE_STATES = {"research", "script", "production", "qa"}


@dataclass(frozen=True)
class TransitionResult:
    allowed: bool
    message: str


def validate_transition(current: str, target: str, approval_note: str | None = None) -> TransitionResult:
    """Validate a workflow state transition.

    Rules
    -----
    * Forward by exactly one step is allowed.
    * Backward by one step is allowed for REVERTABLE_STATES (revision/reject).
    * Human-approval states require a non-empty *approval_note*.
    * Cannot advance past the final state ``published``.
    """
    if current not in STATES:
        return TransitionResult(False, f"Unknown current state: '{current}'.")
    if target not in STATES:
        return TransitionResult(False, f"Unknown target state: '{target}'.")

    current_index = STATES.index(current)
    target_index = STATES.index(target)

    # --- Final state guard ---
    if current == "published":
        return TransitionResult(False, "Video is already published. No further transitions allowed.")

    # --- Forward by one ---
    is_forward = target_index == current_index + 1

    # --- Backward by one (revision / rejection) ---
    is_revert = target_index == current_index - 1 and target in REVERTABLE_STATES

    if not is_forward and not is_revert:
        next_state = STATES[current_index + 1]
        prev_hint = ""
        if current_index > 0:
            prev_state = STATES[current_index - 1]
            if prev_state in REVERTABLE_STATES:
                prev_hint = f" Or revert to '{prev_state}'."
        return TransitionResult(
            False,
            f"From '{current}', you can advance to '{next_state}'.{prev_hint} "
            f"Requested '{target}' is not allowed.",
        )

    # --- Human approval check ---
    if is_forward and target in HUMAN_APPROVAL_STATES and not approval_note:
        return TransitionResult(False, f"A human approval note is required for '{target}'.")

    direction = "advance" if is_forward else "revert"
    return TransitionResult(True, f"Transition allowed ({direction}).")
