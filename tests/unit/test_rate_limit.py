from geochem.core.runtime import RuntimeSettings
from geochem.web.rate_limit import ApiRateLimiter


def test_development_rate_limiter_separates_ai_and_api_buckets():
    limiter = ApiRateLimiter(RuntimeSettings(chat_rate_per_minute=1, request_rate_per_minute=10))

    first_ai = limiter.check("user-1", "POST", "/api/v1/chat/threads/CHAT_1/messages")
    second_ai = limiter.check("user-1", "POST", "/api/v1/chat/threads/CHAT_1/messages")
    first_api = limiter.check("user-1", "GET", "/api/v1/articles")

    assert first_ai.allowed is True
    assert first_ai.bucket == "ai"
    assert second_ai.allowed is False
    assert second_ai.remaining == 0
    assert first_api.allowed is True
    assert first_api.bucket == "read"


def test_chat_polling_does_not_consume_ai_message_limit():
    limiter = ApiRateLimiter(RuntimeSettings(chat_rate_per_minute=1, request_rate_per_minute=10))

    polls = [
        limiter.check("user-1", "GET", "/api/v1/chat/threads/CHAT_1/state")
        for _ in range(25)
    ]
    message = limiter.check("user-1", "POST", "/api/v1/chat/threads/CHAT_1/messages")

    assert all(item.allowed and item.bucket == "read" for item in polls)
    assert message.allowed is True
    assert message.bucket == "ai"
