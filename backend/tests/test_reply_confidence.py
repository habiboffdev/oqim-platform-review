"""Tests for the reply confidence scorer (P5a S2)."""

import pytest

from app.modules.agent_runtime_v2.confidence import score_confidence


@pytest.mark.parametrize(
    ("grounding_hits", "tool_errors", "expected"),
    [
        # grounded, no errors -> evidence confidence is high
        (3, 0, 1.0),
        # no grounding -> capped at 0.5 (ungrounded reply must not autopilot-send)
        (0, 0, 0.50),
        # any tool error -> capped at 0.4
        (3, 1, 0.40),
        # both caps apply -> the lower (tool-error) cap wins
        (0, 2, 0.40),
    ],
)
def test_score_confidence(grounding_hits, tool_errors, expected):
    assert score_confidence(
        grounding_hits=grounding_hits,
        tool_errors=tool_errors,
    ) == pytest.approx(expected)
