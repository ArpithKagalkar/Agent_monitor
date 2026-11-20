"""Otari-only inference client and a small, explicit live-check CLI.

Uses Otari's documented OpenAI-compatible HTTP API. Nothing falls back to a
provider endpoint, and requests are not automatically retried or logged.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from pydantic import SecretStr


class OtariError(RuntimeError):
    """A safe-to-display error without credentials, prompts, or response bodies."""

    def __init__(
        self,
        message,
        *,
        status_code=None,
        error_code=None,
        request_id=None,
        budget_scope=None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.request_id = request_id
        self.budget_scope = budget_scope


@dataclass(frozen=True)
class OtariConfig:
    base_url: str
    api_key: SecretStr
    model: str = ""

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        local_http = parsed.scheme == "http" and parsed.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            not parsed.hostname
            or (parsed.scheme != "https" and not local_http)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise OtariError(
                "OTARI_BASE_URL must be HTTPS, or HTTP on localhost, without credentials or query parameters"
            )
        if not parsed.path.rstrip("/").endswith("/v1"):
            raise OtariError(
                "OTARI_BASE_URL must include the OpenAI-compatible /v1 API prefix"
            )
        if (
            parsed.hostname in {"api.otari.ai", "eu.api.otari.ai"}
            and parsed.path.rstrip("/") != "/api/v1"
        ):
            raise OtariError("Hosted Otari requires /api/v1 in OTARI_BASE_URL")
        if not self.api_key.get_secret_value().strip():
            raise OtariError(
                "Set OTARI_AGENT_KEY in the local .env file before a live request"
            )

    @classmethod
    def from_env(cls, env_file: Path | None = None, *, role="agent") -> "OtariConfig":
        # Read only the explicitly located repo file; process variables take precedence.
        env_file = env_file or Path(__file__).resolve().parents[1] / ".env"
        values = {**dotenv_values(env_file), **os.environ}
        base_url = (values.get("OTARI_BASE_URL") or "").strip().rstrip("/")
        if not base_url:
            raise OtariError(
                "Set OTARI_BASE_URL to your Otari account's OpenAI-compatible endpoint"
            )
        if role not in {"agent", "reviewer"}:
            raise OtariError("Unknown model role")
        key_name = "OTARI_JUDGE_KEY" if role == "reviewer" else "OTARI_AGENT_KEY"
        model_name = "OTARI_JUDGE_MODEL" if role == "reviewer" else "OTARI_AGENT_MODEL"
        return cls(
            base_url=base_url,
            api_key=SecretStr(
                values.get(key_name) or values.get("OTARI_AGENT_KEY") or ""
            ),
            model=(values.get(model_name) or "").strip(),
        )


@dataclass(frozen=True)
class OtariReply:
    body: dict[str, Any]
    run_id: UUID
    request_id: str | None
    resolved_model: str | None = None
    provider: str | None = None
    attempt_count: int | None = None
    fallback: bool | None = None
    cost_usd: str | None = None
    pricing_source: str | None = None

    def usage_summary(self) -> dict[str, Any]:
        """Return reported metadata only; never synthesize a price or trace ID."""
        return {
            "run_id": str(self.run_id),
            "otari_request_id": self.request_id,
            "completion_id": self.body.get("id"),
            "model": self.body.get("model"),
            "usage": self.body.get("usage"),
            "resolved_model": self.resolved_model,
            "provider": self.provider,
            "attempt_count": self.attempt_count,
            "fallback": self.fallback,
        }


def reply_from_response(response, run_id):
    attempts = response.headers.get("Otari-Attempt-Count", "")
    fallback = response.headers.get("Otari-Fallback")
    return OtariReply(
        OtariClient._json(response),
        run_id,
        response.headers.get("Otari-Request-ID"),
        resolved_model=response.headers.get("Otari-Model"),
        provider=response.headers.get("Otari-Provider"),
        attempt_count=int(attempts) if attempts.isdigit() else None,
        fallback=fallback.lower() == "true" if fallback is not None else None,
        cost_usd=response.headers.get("Otari-Cost-USD"),
        pricing_source=response.headers.get("Otari-Pricing-Source"),
    )


class OtariClient:
    def __init__(
        self, config: OtariConfig, *, transport: httpx.BaseTransport | None = None
    ):
        self.config = config
        self._http = httpx.Client(
            timeout=60.0,
            follow_redirects=False,
            transport=transport,
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "User-Agent": "agent-monitor/0.1",
            },
        )

    def __enter__(self) -> "OtariClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(
                method, f"{self.config.base_url.rstrip('/')}/{path}", **kwargs
            )
        except httpx.RequestError:
            raise OtariError(
                "Otari request failed or timed out; no provider fallback was attempted"
            ) from None
        if not response.is_success:
            hint = {
                401: "check the configured API key",
                403: "check key scopes and workspace permissions",
                404: "check the base URL and configured model",
                421: "check the account region and base URL",
                429: "check the rate limit or budget",
            }.get(
                response.status_code,
                "check Otari service status and workspace configuration",
            )
            try:
                error_body = response.json()
                body_code = (
                    error_body.get("code") if isinstance(error_body, dict) else None
                )
            except ValueError:
                body_code = None
            code = response.headers.get("Otari-Error-Code") or body_code
            safe_codes = {
                "budget_exceeded",
                "guardrail_blocked",
                "insufficient_funds",
                "pricing_required",
                "all_candidates_rejected",
                "model_not_found",
            }
            raise OtariError(
                f"Otari returned HTTP {response.status_code}; {hint}",
                status_code=response.status_code,
                error_code=code if code in safe_codes else None,
                request_id=response.headers.get("Otari-Request-ID"),
                budget_scope=response.headers.get("Otari-Budget-Scope"),
            )
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            raise OtariError("Otari returned a non-JSON response") from None
        if not isinstance(body, dict):
            raise OtariError("Otari returned an unexpected response shape")
        return body

    def list_models(self) -> list[str]:
        body = self._json(self._request("GET", "models"))
        models = body.get("data")
        if not isinstance(models, list) or any(
            not isinstance(model, dict) or not isinstance(model.get("id"), str)
            for model in models
        ):
            raise OtariError(
                "Otari model catalog did not contain the expected model IDs"
            )
        return sorted({model["id"] for model in models})

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        run_id: UUID,
        tools: list[dict[str, Any]] | None = None,
        max_completion_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        guardrails: list[dict[str, Any]] | None = None,
    ) -> OtariReply:
        if not self.config.model:
            raise OtariError("Set OTARI_AGENT_MODEL to an exact ID from --list-models")
        if not isinstance(run_id, UUID):
            raise OtariError("A UUID run_id is required to group requests in Otari")
        payload: dict[str, Any] = {"model": self.config.model, "messages": messages}
        if response_format is not None:
            payload["response_format"] = response_format
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        if guardrails is not None:
            payload["guardrails"] = guardrails
        if tools is not None:
            if any(tool.get("type") != "function" for tool in tools):
                raise OtariError(
                    "Only caller-executed function tool definitions are supported"
                )
            payload["tools"] = tools
        if max_completion_tokens is not None:
            if (
                isinstance(max_completion_tokens, bool)
                or not isinstance(max_completion_tokens, int)
                or max_completion_tokens < 1
            ):
                raise OtariError("max_completion_tokens must be a positive integer")
            payload["max_completion_tokens"] = max_completion_tokens
        response = self._request(
            "POST",
            "chat/completions",
            json=payload,
            headers={"Otari-Conversation-Id": str(run_id)},
        )
        body = self._json(response)
        if not isinstance(body.get("choices"), list) or not body["choices"]:
            raise OtariError("Otari did not return a chat completion")
        return reply_from_response(response, run_id)

    def fetch(self, url: str, *, run_id: UUID, max_chars=2400) -> OtariReply:
        response = self._request(
            "POST",
            "fetch",
            json={
                "url": url,
                "fetch_tool_name": "builtin_fetch",
                "max_chars": max_chars,
            },
            headers={"Otari-Conversation-Id": str(run_id)},
        )
        return reply_from_response(response, run_id)

    def execute_code(self, code: str, *, run_id: UUID, timeout_seconds=5) -> OtariReply:
        response = self._request(
            "POST",
            "code/execute",
            json={"code": code, "timeout_seconds": timeout_seconds},
            headers={"Otari-Conversation-Id": str(run_id)},
        )
        return reply_from_response(response, run_id)

    def key_budget(self):
        return self._json(self._request("GET", "key/budget"))

    def mcp_servers(self):
        return self._json(self._request("GET", "mcp/servers"))

    def mcp_tools(self, server_id: UUID):
        return self._json(
            self._request("GET", f"mcp/servers/{UUID(str(server_id))}/tools")
        )

    def execute_mcp(
        self,
        server_id: UUID,
        server_revision: str,
        tool_name: str,
        arguments: dict,
        *,
        run_id: UUID,
        execution_id: UUID,
    ):
        # Correlation is not idempotency. Exactly one HTTP attempt; never retry.
        response = self._request(
            "POST",
            "mcp/execute",
            json={
                "mcp_server_id": str(server_id),
                "server_revision": server_revision,
                "tool_name": tool_name,
                "arguments": arguments,
                "client_execution_id": str(execution_id),
            },
            headers={"Otari-Conversation-Id": str(run_id)},
            timeout=50.0,
        )
        return reply_from_response(response, run_id)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect configured Otari models or make one synthetic inference request"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--list-models",
        action="store_true",
        help="Read available IDs without guessing a model",
    )
    action.add_argument(
        "--smoke",
        action="store_true",
        help="Make one billable, synthetic inference request",
    )
    parser.add_argument(
        "--run-id",
        type=UUID,
        help="Conversation ID to locate in Otari; generated if omitted",
    )
    args = parser.parse_args()
    try:
        with OtariClient(OtariConfig.from_env()) as client:
            if args.list_models:
                print(json.dumps({"models": client.list_models()}, indent=2))
            else:
                reply = client.chat(
                    [{"role": "user", "content": "Reply with exactly OTARI_OK."}],
                    run_id=args.run_id or uuid4(),
                    max_completion_tokens=128,
                )
                print(json.dumps(reply.usage_summary(), indent=2))
                print(
                    "Inference returned. Verify this run_id in Otari Activity/Sessions to confirm the trace."
                )
    except OtariError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
