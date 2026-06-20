"""Message intake normalizer tests for reusable channel payload parsing."""

from datetime import datetime, timezone


class TestNormalizeTelegramBridge:
    def test_telegram_bridge_payload_produces_persist_input(self):
        from app.modules.message_intake.normalizer import normalize

        raw = {
            "chatId": "123456",
            "senderId": "789",
            "messageId": 42,
            "text": "iPhone bormi?",
            "date": 1700000000,
            "isOutgoing": False,
            "mediaType": None,
            "replyToMsgId": None,
        }

        result = normalize(raw, channel="telegram_dm")

        assert result.text == "iPhone bormi?"
        assert result.sender_id == 789
        assert result.telegram_chat_id == 123456
        assert result.telegram_message_id == 42
        assert result.channel == "telegram_dm"
        assert result.is_outgoing is False
        assert result.message_ts == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
        assert result.media_type is None
        assert result.reply_to_msg_id is None

    def test_optional_fields_default_to_none(self):
        from app.modules.message_intake.normalizer import normalize

        raw = {
            "chatId": "100",
            "senderId": "200",
            "messageId": 1,
            "text": "",
            "date": 1700000000,
            "isOutgoing": True,
        }

        result = normalize(raw, channel="telegram_dm")
        assert result.media_type is None
        assert result.reply_to_msg_id is None
        assert result.text == ""
        assert result.is_outgoing is True

    def test_non_telegram_channel_uses_generic_normalizer(self):
        from app.modules.message_intake.normalizer import normalize

        result = normalize(
            {
                "chatId": "1",
                "senderId": "2",
                "messageId": "3",
                "text": "hello",
                "date": 1700000000,
            },
            channel="whatsapp_dm",
        )
        assert result.channel == "whatsapp_dm"
        assert result.telegram_chat_id is None
