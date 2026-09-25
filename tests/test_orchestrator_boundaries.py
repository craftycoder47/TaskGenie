import pytest

import orchestrator_bot as bot


def test_worker_refuses_to_start_without_telegram_token(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_TOKEN", "")
    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN is not configured"):
        bot.main()


def test_ai_without_client_returns_safe_configuration_message(monkeypatch):
    monkeypatch.setattr(bot, "client", None)
    result = bot.ask_claude("system", "hello")
    assert "not configured" in result.lower()
    assert "api key" not in result.lower()


def test_business_pack_prompt_preserves_unknown_facts():
    profile = {
        "business_type": "Cleaner",
        "location": "Luton",
        "services": "Home cleaning",
        "customer": "Busy families",
        "goal": "Get more enquiries",
    }
    prompt = bot.business_pack_prompt(profile)
    assert "Do not" in prompt
    assert "[ADD ...]" in prompt
    assert "UK" in prompt
