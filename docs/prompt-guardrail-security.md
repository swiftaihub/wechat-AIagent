# Prompt, Guardrail, and Runtime Safety

This repo no longer uses a single free-form chatbot prompt as the primary runtime.
The active production path is a hybrid flow:

1. request admission and abuse protection
2. input guardrail checks
3. structured product-helper orchestration
4. optional controlled naturalization through DashScope / Model Studio
5. output guardrail and channel trimming

## Active Runtime Graph

```text
web UI / WeChat
  -> app.llm_core.generate_reply_result()
     -> app.usage_guard
     -> app.guardrail.GuardrailEngine.check_input()
     -> app.product_helper.service.handle()
        -> product_helper high-risk precheck
        -> intent routing
        -> structured retrieval / ranking / cautions / links
        -> direct-answer-first draft response
     -> optional app.llm_provider.llm_chat()
        -> Alibaba Cloud DashScope / Model Studio
     -> app.guardrail.GuardrailEngine.sanitize_output()
     -> channel trim + memory update
```

`app.llm_provider.py` is the only active LLM provider entrypoint in this repo.
It targets the DashScope OpenAI-compatible API and does not require Ollama.

## Separation of Responsibilities

- `app/llm_core.py`
  - shared request entry for web and WeChat
  - language resolution
  - usage guard admission
  - prompt guardrail checks
  - optional naturalization call
  - final output sanitization and memory update

- `app/product_helper/service.py`
  - high-risk escalation before recommendation logic
  - intent routing
  - structured product, ingredient, gifting, compare, and article handling
  - response planning and deterministic draft composition

- `app/product_helper/guardrails.py`
  - high-risk detection
  - caution note collection
  - domain-safe fallback enforcement

- `app/usage_guard.py`
  - short-window rate limit
  - repeated prompt abuse detection
  - session message ceiling
  - hourly and daily quotas
  - single in-flight request lock
  - optional Redis-backed shared state

- `app/guardrail.py`
  - generic input/output filtering and sanitization backstop

- `app/llm_provider.py`
  - DashScope request building
  - timeout handling
  - retry logic for retryable failures only
  - circuit breaker
  - structured logging without secret leakage

## Prompt and Config Loading

- Private prompt content stays in local-only files such as `config/prompt.private.yaml`.
- Public-safe starter contracts live in `config/*.example.yaml`.
- Runtime config is loaded via:
  - `.env`
  - `app/runtime_config.py`
  - `app/prompt_runtime.py`

The prompt layer should define behavior, style, and naturalization policy.
It should not replace the structured product-helper logic or the usage guard layer.

## Guardrail Integration Notes

Guardrails are intentionally applied in multiple layers:

1. `app.usage_guard` blocks abusive or over-limit requests before any paid model call.
2. `app.guardrail.GuardrailEngine.check_input()` blocks generic unsafe input patterns.
3. `app.product_helper.guardrails` shapes the response plan itself:
   - acute-risk escalation
   - medical boundary enforcement
   - relevant caution injection
4. `app.guardrail.GuardrailEngine.sanitize_output()` remains the final backstop.

This means safety is not only a post-processing string filter.

## Production Notes

- Set `DASHSCOPE_API_KEY` through environment variables or a secret manager.
- Keep `DASHSCOPE_BASE_URL` configurable; the default is the Alibaba Cloud compatible endpoint.
- Prefer `REDIS_URL` in multi-instance deployments so quotas and cooldowns stay consistent.
- Do not log raw API keys, full user health disclosures, or full prompt payloads.

## Minimal Example

```python
outcome = await generate_reply_result(
    user_id="wechat-user-123",
    text="送妈妈的话哪款更稳妥？",
    channel="wechat",
)

if outcome.blocked:
    return outcome.reply

return outcome.reply
```

The returned text may be a deterministic draft or a controlled DashScope-polished reply,
but the surrounding safety and product-helper orchestration stay in charge.
