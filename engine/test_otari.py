"""Offline HTTP boundary tests. These are not evidence of live Otari inference."""

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from engine.otari import OtariClient, OtariConfig, OtariError


@pytest.fixture
def config():
    return OtariConfig(
        base_url="https://api.otari.ai/api/v1",
        api_key=SecretStr("synthetic-test-key"),
        model="workspace-test-model",
    )


def test_chat_uses_only_otari_and_groups_by_run(config):
    run_id = uuid4()
    requests = []
    tool_calls = [{"id": "call-1", "type": "function", "function": {"name": "get_lead", "arguments": '{"lead_id":"L-001"}'}}]

    def upstream(request):
        requests.append(request)
        assert str(request.url) == "https://api.otari.ai/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer synthetic-test-key"
        assert request.headers["Otari-Conversation-Id"] == str(run_id)
        payload = json.loads(request.content)
        assert payload["model"] == "workspace-test-model"
        assert payload["max_completion_tokens"] == 128
        assert payload["tools"][0]["type"] == "function"
        return httpx.Response(200, headers={"Otari-Request-ID": "request-1"}, json={
            "id": "completion-1",
            "model": "workspace-test-model",
            "choices": [{"message": {"role": "assistant", "tool_calls": tool_calls}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost_usd": "0.000003", "pricing_source": "reported"},
        })

    with OtariClient(config, transport=httpx.MockTransport(upstream)) as client:
        reply = client.chat(
            [{"role": "user", "content": "Synthetic request"}], run_id=run_id,
            tools=[{"type": "function", "function": {"name": "get_lead", "parameters": {"type": "object"}}}],
            max_completion_tokens=128,
        )
    assert len(requests) == 1
    assert reply.body["choices"][0]["message"]["tool_calls"] == tool_calls
    assert reply.usage_summary()["otari_request_id"] == "request-1"
    assert reply.usage_summary()["usage"]["cost_usd"] == "0.000003"
    assert "messages" not in reply.usage_summary()


def test_unknown_cost_and_request_id_are_not_fabricated(config):
    def upstream(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "OTARI_OK"}}]})

    with OtariClient(config, transport=httpx.MockTransport(upstream)) as client:
        summary = client.chat([], run_id=uuid4()).usage_summary()
    assert summary["usage"] is None
    assert summary["otari_request_id"] is None


@pytest.mark.parametrize("status", [302, 401, 403, 404, 421, 429, 503])
def test_errors_do_not_retry_redirect_fallback_or_echo_body(config, status):
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://api.openai.com/v1/chat/completions"}, text="synthetic-test-key private prompt")

    with OtariClient(config, transport=httpx.MockTransport(upstream)) as client:
        with pytest.raises(OtariError) as error:
            client.chat([], run_id=uuid4())
    assert len(requests) == 1
    assert str(status) in str(error.value)
    assert "synthetic-test-key" not in str(error.value)
    assert "private prompt" not in str(error.value)


def test_timeout_is_redacted_and_not_retried(config):
    requests = []

    def upstream(request):
        requests.append(request)
        raise httpx.ReadTimeout("private error body", request=request)

    with OtariClient(config, transport=httpx.MockTransport(upstream)) as client:
        with pytest.raises(OtariError, match="timed out") as error:
            client.chat([], run_id=uuid4())
    assert len(requests) == 1
    assert "private error" not in str(error.value)


def test_models_can_be_listed_before_selecting_one(config):
    config = OtariConfig(config.base_url, config.api_key)

    def upstream(request):
        assert request.url.path == "/api/v1/models"
        return httpx.Response(200, json={"data": [{"id": "workspace-model-b"}, {"id": "workspace-model-a"}]})

    with OtariClient(config, transport=httpx.MockTransport(upstream)) as client:
        assert client.list_models() == ["workspace-model-a", "workspace-model-b"]
        with pytest.raises(OtariError, match="OTARI_AGENT_MODEL"):
            client.chat([], run_id=uuid4())


@pytest.mark.parametrize("url", [
    "https://api.otari.ai/v1", "https://eu.api.otari.ai/v1",
    "http://public.example/api/v1", "https://user:secret@example.test/api/v1",
    "https://example.test/api/v1?secret=value", "https://example.test/api/v1#fragment",
    "https://api.otari.ai/api",
])
def test_bad_endpoints_are_rejected(url):
    with pytest.raises(OtariError):
        OtariConfig(url, SecretStr("synthetic-test-key"))


def test_missing_key_is_detected_without_a_request():
    with pytest.raises(OtariError, match="OTARI_AGENT_KEY"):
        OtariConfig("https://api.otari.ai/api/v1", SecretStr(""))


def test_config_repr_does_not_expose_the_key(config):
    assert "synthetic-test-key" not in repr(config)


def test_repo_env_is_read_without_overriding_process_config(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("OTARI_BASE_URL=https://api.otari.ai/api/v1\nOTARI_AGENT_KEY=local-test-key\nOTARI_AGENT_MODEL=file-model\n")
    for name in ("OTARI_BASE_URL", "OTARI_AGENT_KEY", "OTARI_AGENT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTARI_AGENT_MODEL", "process-model")
    config = OtariConfig.from_env(env_file)
    assert config.model == "process-model"
    assert config.api_key.get_secret_value() == "local-test-key"


def test_gateway_executed_mcp_tools_cannot_be_enabled(config):
    with OtariClient(config, transport=httpx.MockTransport(lambda request: pytest.fail("Must not send a request"))) as client:
        with pytest.raises(OtariError, match="caller-executed"):
            client.chat([], run_id=uuid4(), tools=[{"type": "mcp", "server_url": "http://localhost:8100/mcp"}])


@pytest.mark.parametrize("body", [[], {}, {"choices": []}])
def test_malformed_completion_is_not_a_success(config, body):
    with OtariClient(config, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
        with pytest.raises(OtariError):
            client.chat([], run_id=uuid4())
