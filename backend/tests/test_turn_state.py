"""TurnState enum + transition table (#427 S8). Pure, no DB."""
from __future__ import annotations

from app.modules.conversation_turns.turn_state import (
    ACTIVE_TURN_STATES,
    ALLOWED_TRANSITIONS,
    NON_TERMINAL_TURN_STATES,
    TURN_RUNNER_MAX_DISPATCH_ATTEMPTS,
    TurnState,
)


def test_values_equal_existing_strings():
    assert TurnState.OPEN == "open" and TurnState.RUNNING == "running"
    assert TurnState.CONTINUED == "continued" and TurnState.QUARANTINED == "quarantined"
    assert [s.value for s in TurnState] == [
        "open", "starting", "running", "finalizing", "continued", "completed", "quarantined"]


def test_state_tuples():
    assert ACTIVE_TURN_STATES == (TurnState.OPEN, TurnState.STARTING, TurnState.RUNNING, TurnState.FINALIZING)
    assert (*ACTIVE_TURN_STATES, TurnState.CONTINUED) == NON_TERMINAL_TURN_STATES


def test_transition_table_complete_and_terminal_empty():
    # every state is a key; terminals map to empty
    assert set(ALLOWED_TRANSITIONS) == set(TurnState)
    assert ALLOWED_TRANSITIONS[TurnState.COMPLETED] == frozenset()
    assert ALLOWED_TRANSITIONS[TurnState.QUARANTINED] == frozenset()
    # lock the WHOLE table (the 13 ground-truth edges) — catches a missing
    # edge AND an accidental extra one
    expected = {
        TurnState.OPEN: frozenset({TurnState.STARTING, TurnState.COMPLETED}),
        TurnState.STARTING: frozenset({TurnState.RUNNING, TurnState.OPEN,
                                       TurnState.COMPLETED, TurnState.QUARANTINED}),
        TurnState.RUNNING: frozenset({TurnState.FINALIZING, TurnState.CONTINUED,
                                      TurnState.COMPLETED}),
        TurnState.FINALIZING: frozenset({TurnState.COMPLETED, TurnState.CONTINUED}),
        TurnState.CONTINUED: frozenset({TurnState.STARTING, TurnState.COMPLETED}),
        TurnState.COMPLETED: frozenset(),
        TurnState.QUARANTINED: frozenset(),
    }
    assert expected == ALLOWED_TRANSITIONS


def test_max_dispatch_attempts_constant():
    assert TURN_RUNNER_MAX_DISPATCH_ATTEMPTS == 3
