"""
Central wrapper for all Groq API calls.
All LLM agents use this — never call the Groq API directly.

Design rules:
  - Returns None on ANY failure (never raises).
  - GROQ_API_KEY missing → log critical once, return None.
  - 30-second timeout per call.
  - No retries — let the caller's fallback handle it.
  - Logs every call to llm_calls table via audit_log.
"""
import os
import time
import logging
from typing import Optional

_logger = logging.getLogger("llm_client")
_api_key_missing_logged = False

# Token limit safety buffer — truncate prompt if it would exceed this
_MAX_PROMPT_CHARS = 12000   # rough guard (~3000 tokens at 4 chars/token)


def call_llm(
    prompt: str,
    system: str,
    model: str = "llama-3.1-8b-instant",
    max_tokens: int = 1000,
    trace_id: Optional[str] = None,
    agent_name: str = "unknown",
) -> Optional[str]:
    """
    Call the Groq API and return the response text.

    Args:
        prompt:     User-role message content.
        system:     System-role prompt.
        model:      Groq model ID (defaults to llama-3.1-8b-instant for speed/cost).
        max_tokens: Maximum tokens in the response.
        trace_id:   Pipeline trace UUID for audit logging.
        agent_name: Caller name for the llm_calls log.

    Returns:
        Response text string, or None on any failure.
    """
    global _api_key_missing_logged

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        if not _api_key_missing_logged:
            _logger.critical(
                "[LLM_CLIENT] GROQ_API_KEY not set — LLM features disabled. "
                "All agents will use fallbacks."
            )
            _api_key_missing_logged = True
        return None

    # Truncate over-long prompts (preserve first 80% + last 20%).
    if len(prompt) > _MAX_PROMPT_CHARS:
        keep_start = int(_MAX_PROMPT_CHARS * 0.8)
        keep_end   = _MAX_PROMPT_CHARS - keep_start
        truncated  = prompt[:keep_start] + "\n[...truncated...]\n" + prompt[-keep_end:]
        _logger.warning(
            f"[LLM_CLIENT] Prompt truncated from {len(prompt)} to {_MAX_PROMPT_CHARS} chars"
        )
        prompt = truncated

    start_ms = int(time.monotonic() * 1000)
    status   = "failure"
    prompt_tokens     = None
    completion_tokens = None
    response_text     = None

    try:
        from groq import Groq
        client = Groq(api_key=api_key, timeout=30.0)

        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
        )

        if response.choices and len(response.choices) > 0:
            response_text = response.choices[0].message.content

        if response_text:
            status = "success"

        usage = getattr(response, "usage", None)
        if usage:
            prompt_tokens     = getattr(usage, "prompt_tokens",     None)
            completion_tokens = getattr(usage, "completion_tokens", None)

    except Exception as e:
        _logger.warning(f"[LLM_CLIENT] API call failed ({agent_name}): {e}")

    latency_ms = int(time.monotonic() * 1000) - start_ms

    # Log to llm_calls table (fire-and-forget)
    _log_llm_call(
        trace_id=trace_id,
        agent=agent_name,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        status=status,
    )

    return response_text


def _log_llm_call(
    trace_id: Optional[str],
    agent: str,
    model: str,
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    latency_ms: int,
    status: str,
) -> None:
    """Insert a row into llm_calls (best-effort, non-blocking)."""
    import threading

    def _write():
        try:
            from db.connection import db_cursor
            with db_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO llm_calls
                        (trace_id, agent, model, prompt_tokens,
                         completion_tokens, latency_ms, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (trace_id, agent, model, prompt_tokens,
                     completion_tokens, latency_ms, status),
                )
        except Exception:
            pass  # Never crash for logging

    threading.Thread(target=_write, daemon=True).start()
