"""Turn lifecycle state machine: the enum + the allowed-transition table (#427 S8).

Values equal the existing DB strings, so the column stays String (no migration) and
existing queries keep working. The table is the executable spec of the lifecycle,
derived from the 13 verified edges in the design doc §3b.
"""
from __future__ import annotations

from enum import StrEnum


class TurnState(StrEnum):
    OPEN = "open"
    STARTING = "starting"
    RUNNING = "running"
    FINALIZING = "finalizing"
    CONTINUED = "continued"
    COMPLETED = "completed"
    QUARANTINED = "quarantined"


ACTIVE_TURN_STATES = (TurnState.OPEN, TurnState.STARTING, TurnState.RUNNING, TurnState.FINALIZING)
NON_TERMINAL_TURN_STATES = (*ACTIVE_TURN_STATES, TurnState.CONTINUED)

# Allowed `to`-states keyed by `from` (design §3b). Edge 1 (create→open) is the
# initial state, not a row. Terminals map to empty.
ALLOWED_TRANSITIONS: dict[TurnState, frozenset[TurnState]] = {
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

# A turn that fails dispatch this many times is poisoned (race-residue / a
# structurally broken turn): the TurnLifecycle coordinator quarantines it to a
# terminal state instead of re-leasing it every cycle forever (#415). The cap
# lives here, in the dependency-leaf module, so both the runner and the
# coordinator consume it without an import cycle (lifecycle → runner →
# service → lifecycle).
TURN_RUNNER_MAX_DISPATCH_ATTEMPTS = 3
