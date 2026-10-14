"""
groq_fallback.py
Place this in: backend/groq_fallback.py

Fallback chain (in order):
  1. OpenRouter (paid):  openai/gpt-4o           ← PRIMARY (heavy)
                         openai/gpt-4o-mini       ← PRIMARY (medium/light)
  2. Groq (free):        openai/gpt-oss-120b → gpt-oss-20b → gpt-oss-safeguard-20b → qwen3.8-27b
  3. Anthropic (paid):   claude-sonnet-4-6        ← FINAL GUARANTEED FALLBACK

Credit checking (live, while pipeline runs):
  - OpenRouter balance is fetched once at startup and cached for 60 seconds.
  - Before every OpenRouter call, the cached balance is checked.
  - If remaining credits drop below LOW_CREDIT_THRESHOLD_USD (default $0.50),
    a WARNING is logged but calls still proceed.
  - If remaining credits drop below SKIP_CREDIT_THRESHOLD_USD (default $0.10),
    all OpenRouter models are skipped and Groq takes over immediately.
  - Balance is also re-fetched after an `insufficient_credits` 402 error.
  - get_openrouter_balance() is exposed so main.py /health can call it too.
"""
import logging
import os
import time
import random
from datetime import date
from openai import OpenAI

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    import urllib.request, json as _json
    _HAS_HTTPX = False

log = logging.getLogger(__name__)

_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "groq":       "https://api.groq.com/openai/v1",
}

_FULL_CHAIN: list[tuple[str, str]] = [
    ("openai/gpt-4o",                          "openrouter"),
    ("openai/gpt-4o-mini",                     "openrouter"),
    ("openai/gpt-oss-120b",                    "groq"),
    ("openai/gpt-oss-20b",                     "groq"),
    ("openai/gpt-oss-safeguard-20b",           "groq"),
    ("qwen/qwen3.8-27b",                       "groq"),
    ("claude-sonnet-4-6",                      "anthropic"),
]

_TASK_START: dict[str, tuple[str, str]] = {
    "heavy":  ("openai/gpt-4o",      "openrouter"),
    "medium": ("openai/gpt-4o-mini", "openrouter"),
    "light":  ("openai/gpt-4o-mini", "openrouter"),
}

_exhausted_today: set[tuple[str, str]] = set()
_exhausted_date: str = date.today().isoformat()

_BODY_ERROR_PHRASES = (
    "rate limit", "rate_limit", "tokens per day", "quota exceeded",
    "too many requests", "please try again", "i cannot", "i'm sorry",
    "i am sorry", "as an ai", "exceeded your",
)

# ── Rate limiting config ───────────────────────────────────────────────────────
# Minimum seconds to wait between successive LLM calls to Groq.
# Groq free tier allows ~30 req/min on most models. With 7 agents firing
# back-to-back, a 2-3s gap prevents most 429s without slowing the pipeline much.
# Set to 0 to disable. Configurable via .env: GROQ_INTER_CALL_DELAY_SEC=2
GROQ_INTER_CALL_DELAY_SEC: float = float(os.environ.get("GROQ_INTER_CALL_DELAY_SEC", "2.0"))

# On a 429, wait this many seconds before trying the NEXT model in the chain
# (on top of any retry the SDK already did). Adds jitter to avoid thundering herd.
GROQ_429_EXTRA_WAIT_SEC: float = float(os.environ.get("GROQ_429_EXTRA_WAIT_SEC", "5.0"))

# Tracks last Groq call time for inter-call spacing
_last_groq_call_at: float = 0.0

# ── Credit check config ────────────────────────────────────────────────────────
# Warn in logs when remaining credits fall below this (but still use OpenRouter)
LOW_CREDIT_THRESHOLD_USD:  float = float(os.environ.get("OR_LOW_CREDIT_WARN_USD",  "0.50"))
# Skip OpenRouter entirely and fall through to Groq when below this
SKIP_CREDIT_THRESHOLD_USD: float = float(os.environ.get("OR_SKIP_CREDIT_USD",      "0.10"))
# How long to cache the balance before re-fetching (seconds)
CREDIT_CACHE_TTL_SEC: int = int(os.environ.get("OR_CREDIT_CACHE_TTL_SEC", "60"))

# ── Credit cache (module-level, shared across all calls in the process) ────────
_credit_cache: dict = {
    "remaining":   None,   # float | None
    "used":        None,
    "limit":       None,
    "fetched_at":  0.0,    # unix timestamp
    "error":       None,   # last fetch error string, if any
}


class ModelExhaustedError(RuntimeError):
    """
    Raised when every model in the fallback chain has failed.
    Agents catch this to return status='error' cleanly so LangGraph
    exits via error_node instead of looping to GraphRecursionError.
    """


# ── Credit balance helpers ─────────────────────────────────────────────────────

def _fetch_openrouter_balance_raw(api_key: str) -> dict:
    """
    Call GET /api/v1/auth/key and return parsed dict with keys:
      remaining_usd, used_usd, limit_usd, label, error
    Uses httpx if available, otherwise falls back to stdlib urllib.
    """
    url     = "https://openrouter.ai/api/v1/auth/key"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        if _HAS_HTTPX:
            r    = httpx.get(url, headers=headers, timeout=5)
            data = r.json().get("data", {})
        else:
            req  = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read()).get("data", {})

        used      = float(data.get("usage",  0) or 0)
        limit     = data.get("limit")               # None = unlimited / prepaid
        limit_f   = float(limit) if limit is not None else None
        remaining = round((limit_f - used), 4) if limit_f is not None else None
        label     = data.get("label", "")

        return {
            "remaining_usd": remaining,
            "used_usd":      round(used, 4),
            "limit_usd":     limit_f,
            "label":         label,
            "error":         None,
        }

    except Exception as e:
        return {
            "remaining_usd": None,
            "used_usd":      None,
            "limit_usd":     None,
            "label":         "",
            "error":         str(e),
        }


def get_openrouter_balance(force_refresh: bool = False) -> dict:
    """
    Return cached OpenRouter balance, refreshing if the cache is stale.

    Called:
      - Before every OpenRouter LLM call (uses cache, very cheap)
      - From main.py /health endpoint (force_refresh=True for fresh data)
      - After a 402 insufficient_credits error (force_refresh=True)

    Returns dict with keys: remaining_usd, used_usd, limit_usd, label, error
    remaining_usd is None if the account is prepaid/unlimited or fetch failed.
    """
    now     = time.monotonic()
    stale   = (now - _credit_cache["fetched_at"]) > CREDIT_CACHE_TTL_SEC
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()

    if not api_key:
        return {"remaining_usd": None, "used_usd": None, "limit_usd": None,
                "label": "", "error": "OPENROUTER_API_KEY not set"}

    if force_refresh or stale or _credit_cache["remaining"] is None:
        result = _fetch_openrouter_balance_raw(api_key)
        _credit_cache.update({
            "remaining":  result["remaining_usd"],
            "used":       result["used_usd"],
            "limit":      result["limit_usd"],
            "fetched_at": now,
            "error":      result["error"],
        })
        if result["error"]:
            log.warning("[credits] balance fetch failed: %s", result["error"])
        else:
            log.info(
                "[credits] OpenRouter balance — used: $%.4f | remaining: %s | limit: %s",
                result["used_usd"] or 0,
                f"${result['remaining_usd']:.4f}" if result["remaining_usd"] is not None else "unlimited/prepaid",
                f"${result['limit_usd']:.2f}"     if result["limit_usd"]     is not None else "n/a",
            )

    return {
        "remaining_usd": _credit_cache["remaining"],
        "used_usd":      _credit_cache["used"],
        "limit_usd":     _credit_cache["limit"],
        "label":         "",
        "error":         _credit_cache["error"],
    }


def _openrouter_has_sufficient_credits() -> bool:
    """
    Returns False (skip OpenRouter, use Groq) if we know remaining credits
    are below SKIP_CREDIT_THRESHOLD_USD.

    Returns True if balance is unknown (None = unlimited/prepaid or fetch failed)
    so we don't block calls when we can't determine the balance.
    """
    balance = get_openrouter_balance()
    remaining = balance["remaining_usd"]

    if remaining is None:
        # Can't determine — could be prepaid/unlimited; allow the call
        return True

    if remaining < SKIP_CREDIT_THRESHOLD_USD:
        log.error(
            "[credits] OpenRouter remaining $%.4f < skip threshold $%.2f "
            "— skipping all OpenRouter models, falling back to Groq",
            remaining, SKIP_CREDIT_THRESHOLD_USD,
        )
        return False

    if remaining < LOW_CREDIT_THRESHOLD_USD:
        log.warning(
            "[credits] OpenRouter remaining $%.4f is LOW (threshold $%.2f) "
            "— top up at openrouter.ai/credits",
            remaining, LOW_CREDIT_THRESHOLD_USD,
        )

    return True


# ── Internal chain helpers ─────────────────────────────────────────────────────

def _reset_if_new_day() -> None:
    global _exhausted_today, _exhausted_date
    today = date.today().isoformat()
    if today != _exhausted_date:
        _exhausted_today = set()
        _exhausted_date  = today
        log.info("[llm_fallback] new UTC day — cleared exhausted model set")


def _mark_exhausted(model: str, provider: str) -> None:
    if provider == "anthropic":
        return
    _exhausted_today.add((model, provider))
    log.warning("[llm_fallback] marked exhausted: %s/%s", provider, model)


def _build_chain(task: str, primary_override: str | None) -> list[tuple[str, str]]:
    _reset_if_new_day()

    # If OpenRouter credits are too low, skip all OpenRouter entries upfront
    skip_openrouter = not _openrouter_has_sufficient_credits()

    start_entry = _TASK_START.get(task, _FULL_CHAIN[0])
    if primary_override:
        for entry in _FULL_CHAIN:
            if entry[0] == primary_override:
                start_entry = entry
                break

    try:
        start_idx = _FULL_CHAIN.index(start_entry)
    except ValueError:
        start_idx = 0

    chain = []
    for e in _FULL_CHAIN[start_idx:]:
        if e in _exhausted_today:
            continue
        if skip_openrouter and e[1] == "openrouter":
            continue
        chain.append(e)

    # Always ensure Anthropic is reachable as last resort
    anthropic_entry = next((e for e in _FULL_CHAIN if e[1] == "anthropic"), None)
    if anthropic_entry and anthropic_entry not in chain:
        chain.append(anthropic_entry)

    if not chain:
        chain = [e for e in _FULL_CHAIN if e[1] == "anthropic"]

    if _exhausted_today:
        log.info("[llm_fallback] skipping exhausted today: %s", sorted(_exhausted_today))

    return chain


def _get_openrouter_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or api_key.startswith("sk-or-your"):
        raise RuntimeError("OPENROUTER_API_KEY not configured")
    return OpenAI(
        api_key=api_key,
        base_url=_BASE_URLS["openrouter"],
        max_retries=1,          # our fallback chain handles retries — 1 SDK retry is enough
        timeout=30.0,           # hard cap; OpenRouter paid is fast, 30s is generous
        default_headers={
            "Authorization":  f"Bearer {api_key}",   # explicit header — some OR configs need it
            "HTTP-Referer":   "https://github.com/fde-financial-analysis",
            "X-Title":        "FDE Financial Analysis",
        },
    )


def _get_groq_client(groq_api_key: str) -> OpenAI:
    if not groq_api_key:
        raise RuntimeError("GROQ_API_KEY not configured")
    return OpenAI(
        api_key=groq_api_key,
        base_url=_BASE_URLS["groq"],
        max_retries=1,          # was 2 — the 25s retry-after wait comes from SDK retry 1→2
                                # reducing to 1 caps the wait; our chain then tries next model
        timeout=60.0,           # Groq can be slow under load; 60s before we give up
    )


def _call_anthropic(messages: list, max_tokens: int, temperature: float) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed. Run: pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)
    system_content = ""
    chat_messages  = []
    for m in messages:
        if m["role"] == "system":
            system_content = m["content"]
        else:
            chat_messages.append({"role": m["role"], "content": m["content"]})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_content or "You are a helpful assistant.",
        messages=chat_messages,
    )
    return response.content[0].text


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e).lower()
    return (
        "rate_limit_exceeded" in msg or "429" in msg
        or "tokens per day" in msg   or "quota" in msg
        or "insufficient credits" in msg  # OpenRouter 402
        or "billing" in msg
    )


def _is_insufficient_credits(e: Exception) -> bool:
    msg = str(e).lower()
    return "insufficient_credits" in msg or "402" in msg or "insufficient credits" in msg


def _is_model_error(e: Exception) -> bool:
    msg = str(e).lower()
    return (
        "model_not_found" in msg or "404" in msg or "400" in msg
        or "does not exist" in msg or "not a valid model" in msg
        or "invalid model" in msg
    )


def _is_bad_response(content: str | None) -> bool:
    if not content or not content.strip():
        return True
    lower = content.strip().lower()
    if len(lower) < 400 and not (lower.startswith("{") or lower.startswith("[")):
        return any(phrase in lower for phrase in _BODY_ERROR_PHRASES)
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

def groq_call_with_fallback(
    client,
    messages:        list,
    primary_model:   str   = None,
    temperature:     float = 0.3,
    max_tokens:      int   = 2048,
    task:            str   = "heavy",
) -> str:
    """
    Call LLMs with automatic fallback: OpenRouter (paid) → Groq (free) → Anthropic.

    Credit check happens before entering the chain:
      - If OpenRouter remaining < $0.10 → skip to Groq immediately
      - If OpenRouter remaining < $0.50 → warn but still try OpenRouter
      - After a 402 error → refresh balance cache and mark OpenRouter exhausted

    Raises:
        ModelExhaustedError: All providers failed.
        Exception:           Non-quota errors surface immediately.
    """
    models = _build_chain(task, primary_model)

    if not models:
        raise ModelExhaustedError(
            "All providers exhausted. Check OPENROUTER_API_KEY credits, "
            "GROQ_API_KEY, and ANTHROPIC_API_KEY in your .env."
        )

    groq_api_key = getattr(client, "api_key", "") or os.environ.get("GROQ_API_KEY", "")

    log.info("[llm_fallback] task=%s max_tokens=%d chain=%s",
             task, max_tokens, [(m, p) for m, p in models])

    last_error: Exception | None = None

    for model, provider in models:
        try:
            # ── Anthropic ─────────────────────────────────────────────────────
            if provider == "anthropic":
                log.info("[llm_fallback] trying anthropic/%s", model)
                content = _call_anthropic(messages, max_tokens, temperature)
                if _is_bad_response(content):
                    last_error = RuntimeError("Bad response from Anthropic")
                    continue
                log.info("[llm_fallback] ✓ anthropic/%s", model)
                return content

            # ── OpenRouter / Groq ─────────────────────────────────────────────
            active_client = (
                _get_openrouter_client() if provider == "openrouter"
                else _get_groq_client(groq_api_key)
            )

            # Inter-call delay for Groq to avoid 429s from back-to-back agents.
            # OpenRouter (paid) has much higher rate limits — no delay needed.
            if provider == "groq" and GROQ_INTER_CALL_DELAY_SEC > 0:
                global _last_groq_call_at
                elapsed = time.monotonic() - _last_groq_call_at
                wait    = GROQ_INTER_CALL_DELAY_SEC - elapsed
                if wait > 0:
                    log.debug("[llm_fallback] rate-limit spacing: sleeping %.1fs before Groq call", wait)
                    time.sleep(wait)

            log.info("[llm_fallback] trying %s/%s", provider, model)
            resp = active_client.chat.completions.create(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=messages,
            )
            if provider == "groq":
                _last_groq_call_at = time.monotonic()

            content = resp.choices[0].message.content

            if _is_bad_response(content):
                log.warning("[llm_fallback] %s/%s bad response — marking exhausted",
                            provider, model)
                _mark_exhausted(model, provider)
                last_error = RuntimeError(f"Bad response from {provider}/{model}")
                continue

            tokens = getattr(resp.usage, "total_tokens", "?")
            log.info("[llm_fallback] ✓ %s/%s — %s tokens", provider, model, tokens)

            # Refresh balance after a successful OpenRouter call so the next
            # agent sees an up-to-date remaining figure
            if provider == "openrouter":
                get_openrouter_balance(force_refresh=True)

            return content

        except RuntimeError as e:
            msg = str(e)
            if "OPENROUTER_API_KEY" in msg:
                log.error("[llm_fallback] OpenRouter key missing — skipping provider")
                for m, p in _FULL_CHAIN:
                    if p == "openrouter":
                        _exhausted_today.add((m, p))
                last_error = e
                continue
            if "GROQ_API_KEY" in msg:
                log.warning("[llm_fallback] Groq key missing — skipping provider")
                for m, p in _FULL_CHAIN:
                    if p == "groq":
                        _exhausted_today.add((m, p))
                last_error = e
                continue
            if "ANTHROPIC_API_KEY" in msg or "anthropic package" in msg:
                log.warning("[llm_fallback] Anthropic unavailable: %s", e)
                last_error = e
                continue
            raise

        except Exception as e:
            last_error = e

            if _is_insufficient_credits(e) and provider == "openrouter":
                log.error(
                    "[credits] OpenRouter 402 insufficient credits — "
                    "refreshing balance and skipping all OpenRouter models"
                )
                get_openrouter_balance(force_refresh=True)   # update cache immediately
                for m, p in _FULL_CHAIN:
                    if p == "openrouter":
                        _exhausted_today.add((m, p))
                continue

            if _is_rate_limit(e):
                # Extra wait before trying next model — SDK already retried 2x,
                # so add jitter to avoid immediately hammering the next model too.
                jitter   = random.uniform(1.0, 3.0)
                wait_sec = GROQ_429_EXTRA_WAIT_SEC + jitter
                log.warning(
                    "[llm_fallback] %s/%s 429 — waiting %.1fs before next model",
                    provider, model, wait_sec,
                )
                time.sleep(wait_sec)
                _mark_exhausted(model, provider)
                continue
            elif _is_model_error(e):
                log.warning("[llm_fallback] %s/%s model unavailable — trying next: %s",
                            provider, model, str(e)[:120])
                _mark_exhausted(model, provider)
                continue
            else:
                raise

    raise ModelExhaustedError(
        f"All providers exhausted (OpenRouter → Groq → Anthropic). "
        f"Last error: {last_error}."
    )
