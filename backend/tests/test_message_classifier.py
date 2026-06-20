"""Message intake gate tests."""

from app.modules.message_intake.classifier import classify_local


class TestLocalFilter:
    def test_greeting_salom_enters_reply_lifecycle(self):
        assert classify_local("Salom").should_enter_reply_lifecycle is True

    def test_greeting_assalomu_alaykum_enters_reply_lifecycle(self):
        assert classify_local("Assalomu alaykum").should_enter_reply_lifecycle is True

    def test_greeting_with_punctuation_enters_reply_lifecycle(self):
        assert classify_local("Salom!").should_enter_reply_lifecycle is True

    def test_greeting_privet_enters_reply_lifecycle(self):
        assert classify_local("privet").should_enter_reply_lifecycle is True

    def test_reaction_thumbs_up_enters_planner(self):
        r = classify_local("👍")
        assert r.should_enter_reply_lifecycle is True
        assert r.reason == "defer_to_lifecycle_planner"

    def test_reaction_multiple_emoji_enters_planner(self):
        r = classify_local("😂🤣")
        assert r.should_enter_reply_lifecycle is True

    def test_acknowledgement_ok_enters_planner(self):
        assert classify_local("ok").should_enter_reply_lifecycle is True

    def test_acknowledgement_ha_enters_planner(self):
        assert classify_local("ha").should_enter_reply_lifecycle is True

    def test_acknowledgement_rahmat_enters_planner(self):
        assert classify_local("rahmat").should_enter_reply_lifecycle is True

    def test_personal_contact_enters_reply_lifecycle(self):
        r = classify_local("Kechki kurs bormi?")
        assert r.should_enter_reply_lifecycle is True

    def test_supplier_contact_enters_reply_lifecycle(self):
        r = classify_local("Narxi qancha?")
        assert r.should_enter_reply_lifecycle is True

    def test_media_only_enters_planner(self):
        r = classify_local("", media_type="photo")
        assert r.should_enter_reply_lifecycle is True
        assert r.reason == "defer_to_lifecycle_planner"

    def test_business_question_passes(self):
        r = classify_local("Kechki kurs bormi?")
        assert r.should_enter_reply_lifecycle is True
        assert r.reason == "defer_to_lifecycle_planner"

    def test_price_ask_passes(self):
        r = classify_local("Narxi qancha?")
        assert r.should_enter_reply_lifecycle is True

    def test_longer_message_passes(self):
        r = classify_local("Salom, kechki kurs bormi? Narxi qancha?")
        assert r.should_enter_reply_lifecycle is True

    def test_ambiguous_message_passes(self):
        r = classify_local("Rahmat, o'ylab ko'raman")
        assert r.should_enter_reply_lifecycle is True

    def test_empty_message(self):
        r = classify_local("")
        assert r.should_enter_reply_lifecycle is False
        assert r.reason == "empty_message"

    def test_structural_gate_does_not_filter_by_contact_type(self):
        r = classify_local("Kechki kurs bormi?")
        assert r.should_enter_reply_lifecycle is True
