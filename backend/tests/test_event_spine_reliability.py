from __future__ import annotations

from app.services import event_spine_persist_consumer as consumer_module
from app.services.event_spine_persist_consumer import EventSpinePersistConsumer


def test_event_spine_no_longer_exposes_legacy_autocrm_action_hooks() -> None:
    assert not hasattr(consumer_module, "process_canonical_message_actions")
    assert not hasattr(EventSpinePersistConsumer, "_run_canonical_actions")
    assert not hasattr(EventSpinePersistConsumer, "_spawn_canonical_actions")
