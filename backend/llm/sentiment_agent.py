"""
LLM-powered news sentiment classifier.
Falls back to keyword scoring from analysis_agent when LLM is unavailable.
"""
import json
import logging
from typing import Optional

from llm.llm_client import call_llm

_logger = logging.getLogger("sentiment_agent")

_SYSTEM_PROMPT = (
    "You are a financial news sentiment classifier for Indian stock markets. "
    "Classify the sentiment of the provided news headlines as it relates to "
    "the stock price movement. "
    "Respond ONLY with valid JSON. No explanation. No markdown."
)

_MAX_HEADLINES = 20


def classify_sentiment(
    headlines: list,
    symbol: str,
    trace_id: Optional[str] = None,
) -> str:
    """
    Classify sentiment as "positive", "negative", or "neutral".

    Args:
        headlines: List of news headline strings.
        symbol:    Stock symbol for context.
        trace_id:  Pipeline trace UUID.

    Returns:
        "positive" | "negative" | "neutral"
    """
    result = classify_sentiment_with_score(headlines, symbol, trace_id)
    return result["sentiment"]


def classify_sentiment_with_score(
    headlines: list,
    symbol: str,
    trace_id: Optional[str] = None,
) -> dict:
    """
    Classify sentiment with confidence score and reasoning.

    Returns:
        {
            "sentiment":    "positive" | "negative" | "neutral",
            "confidence":   float [0, 1],
            "key_signals":  list[str],
            "reasoning":    str,
            "source":       "llm" | "keyword_fallback"
        }
    """
    if not headlines:
        return {
            "sentiment":   "neutral",
            "confidence":  0.5,
            "key_signals": [],
            "reasoning":   "No headlines provided.",
            "source":      "keyword_fallback",
        }

    # Truncate to first MAX_HEADLINES
    if len(headlines) > _MAX_HEADLINES:
        _logger.warning(
            f"[LLM_SENTIMENT] Truncating {len(headlines)} headlines to {_MAX_HEADLINES}"
        )
        headlines = headlines[:_MAX_HEADLINES]

    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    user_prompt = (
        f"Symbol: {symbol}\n"
        f"Headlines:\n{numbered}\n\n"
        'Respond with exactly this JSON:\n'
        '{\n'
        '  "sentiment": "positive" | "negative" | "neutral",\n'
        '  "confidence": 0.0-1.0,\n'
        '  "key_signals": ["signal1", "signal2"],\n'
        '  "reasoning": "one sentence"\n'
        '}'
    )

    raw = call_llm(
        prompt=user_prompt,
        system=_SYSTEM_PROMPT,
        model="llama-3.1-8b-instant",
        max_tokens=256,
        trace_id=trace_id,
        agent_name="sentiment_agent",
    )

    if raw is not None:
        parsed = _parse_response(raw, headlines)
        if parsed is not None:
            return parsed

    # Fallback to keyword scoring
    _logger.info("[LLM_SENTIMENT] Falling back to keyword scoring")
    kw_sentiment = _keyword_fallback(headlines)
    return {
        "sentiment":   kw_sentiment,
        "confidence":  0.5,
        "key_signals": [],
        "reasoning":   "Keyword-based fallback.",
        "source":      "keyword_fallback",
    }


def _parse_response(raw: str, headlines: list) -> Optional[dict]:
    """Parse LLM JSON response. Returns None if parsing fails."""
    try:
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text  = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        data = json.loads(text)

        sentiment = data.get("sentiment", "").lower()
        if sentiment not in ("positive", "negative", "neutral"):
            _logger.warning(f"[LLM_SENTIMENT] Unexpected sentiment value: {sentiment!r}")
            return None

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))   # clamp to [0, 1]

        return {
            "sentiment":   sentiment,
            "confidence":  confidence,
            "key_signals": data.get("key_signals", []),
            "reasoning":   data.get("reasoning", ""),
            "source":      "llm",
        }

    except (json.JSONDecodeError, ValueError, TypeError) as e:
        _logger.warning(f"[LLM_SENTIMENT] JSON parse error: {e}")
        return None


# ---------------------------------------------------------------------------
# Keyword fallback — same logic as analysis_agent._compute_sentiment
# ---------------------------------------------------------------------------

_POSITIVE_WORDS = {
    "growth": 1.0, "profit": 1.0, "upgrade": 1.5, "beat": 1.0,
    "surge": 1.5, "earnings": 1.0, "outperform": 1.5, "revenue": 0.5,
    "expansion": 1.0, "dividend": 0.5, "acquisition": 0.5, "record": 1.0,
    "strong": 1.0, "recovery": 1.0, "rally": 1.5, "buyback": 1.0,
    "optimistic": 1.0, "gain": 1.0, "raised": 1.0, "bullish": 1.5,
}

_NEGATIVE_WORDS = {
    "loss": 1.0, "fraud": 2.0, "downgrade": 1.5, "miss": 1.0,
    "crash": 2.0, "decline": 1.0, "fall": 1.0, "underperform": 1.5,
    "lawsuit": 1.5, "bankruptcy": 2.0, "debt": 0.5, "warning": 1.0,
    "weak": 1.0, "cut": 1.0, "layoff": 1.5, "penalty": 1.0,
    "probe": 1.0, "investigation": 1.5, "default": 2.0, "bearish": 1.5,
}

_SENTIMENT_THRESHOLD = 0.05


def _keyword_fallback(headlines: list) -> str:
    if not headlines:
        return "neutral"
    total = 0.0
    for h in headlines:
        h_lower = h.lower()
        pos = sum(w for kw, w in _POSITIVE_WORDS.items() if kw in h_lower)
        neg = sum(w for kw, w in _NEGATIVE_WORDS.items() if kw in h_lower)
        total += pos - neg
    normalised = total / len(headlines)
    if normalised > _SENTIMENT_THRESHOLD:
        return "positive"
    if normalised < -_SENTIMENT_THRESHOLD:
        return "negative"
    return "neutral"
