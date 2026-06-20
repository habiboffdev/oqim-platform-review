"""Tests for multi-channel intake normalizer (#112)."""


from app.modules.message_intake.normalizer import normalize


class TestNormalizerMultiChannel:
    def test_telegram_works(self):
        result = normalize({
            "chatId": "123", "senderId": "456", "messageId": "789",
            "text": "salom", "date": 1700000000, "isOutgoing": False,
        }, channel="telegram_dm")
        assert result.channel == "telegram_dm"
        assert result.telegram_chat_id == 123

    def test_unknown_channel_does_not_raise(self):
        """Non-Telegram channels should return a generic PersistMessageInput, not raise."""
        result = normalize({
            "chatId": "ig_thread_1", "senderId": "ig_user_1", "messageId": "msg_1",
            "text": "hello from instagram", "date": 1700000000, "isOutgoing": False,
        }, channel="instagram_dm")
        assert result.channel == "instagram_dm"
        assert result.text == "hello from instagram"
        assert result.telegram_chat_id is None
