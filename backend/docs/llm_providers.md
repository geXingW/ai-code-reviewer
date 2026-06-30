# LLM Provider Abstraction

> **Status:** stable MVP for Issue 4. Providers are async, provider-neutral,
> and tested with mocked HTTP clients only.

## 1. Scope

The provider layer lives under `app/llm/` and hides protocol differences
from review engines. Engines talk to a single contract:

```python
from app.llm import ChatMessage, build_provider

provider = build_provider(db_provider_or_runtime_config)
response = await provider.chat([ChatMessage(role="user", content="ping")])
print(response.content)
```

Supported protocols:

- `openai_compatible` — `/chat/completions`, covers DeepSeek, Qwen, GLM,
  Kimi, 火山方舟, Ollama, vLLM, and other OpenAI-compatible gateways.
- `anthropic` — native Claude Messages API (`/messages`, `x-api-key`).
- `custom` — generic JSON POST endpoint with configurable auth header
  template.

## 2. Public types

- `ChatMessage` — provider-neutral `{role, content}` message.
- `ChatResponse` — provider-neutral response with `content`, `model`,
  normalized `usage`, and `raw` provider payload for diagnostics.
- `LLMProviderConfig` — resolved provider config with plaintext API key.
- `LLMProvider` — abstract base class exposing:
  - `chat(messages) -> ChatResponse`
  - `stream_chat(messages) -> AsyncIterator[str]`
  - `embed(texts)` reserved for later semantic features.

## 3. Factory inputs

`build_provider()` accepts three shapes:

- SQLAlchemy `Provider` model-like object from `app.models.provider`.
- Runtime `ProviderConfig` from `app.engines.types`.
- Already-normalized `LLMProviderConfig`.

This keeps the orchestrator free to pass either DB rows or resolved
runtime config while preserving one provider implementation path.

## 4. JSON mode

OpenAI-compatible providers enable JSON mode by default:

```json
{"response_format": {"type": "json_object"}}
```

Runtime provider config may disable it with:

```python
ProviderConfig(..., extra={"default_json_mode": False})
```

The `llm-direct` engine relies on JSON output and now delegates its
default client through this provider layer.

## 5. Errors and retry policy

Provider failures are normalized into the `LLMError` family:

- `RateLimitError` for HTTP 429.
- `AuthError` for HTTP 401/403.
- `TimeoutError` for request timeouts.
- `ServerError` for HTTP 5xx after bounded retry.

Retry behavior is intentionally small and deterministic in the MVP:
server errors and timeouts are retried with exponential backoff, while
auth and rate-limit errors fail fast.

## 6. Token budget helpers

`count_tokens(text)` uses `tiktoken` when installed and falls back to
whitespace tokenization in minimal environments.

`truncate_to_budget(messages, max_tokens=N)` keeps the most recent
messages first and trims an oversized latest message when necessary.

## 7. Security notes

- API keys come from encrypted DB columns or resolved runtime config.
- Provider code never logs API keys or raw headers.
- Tests use fake keys only and do not send real network requests.
