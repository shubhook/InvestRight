"""
LLM trade explanation agent.
Generates human-readable explanations for BUY/SELL decisions.
Falls back to the original technical reason string on LLM failure.
"""
import logging
from typing import Optional

from llm.llm_client import call_llm

_logger = logging.getLogger("explanation_agent")

_SYSTEM_PROMPT = (
    "You are a professional equity analyst explaining a trading signal "
    "to a portfolio manager. Be concise, factual, and specific. "
    "Use plain English. Maximum 3 sentences. No disclaimers. No fluff."
)

_WAIT_MESSAGE = "No trade signal. Conditions do not meet entry criteria."


def generate_explanation(
    symbol: str,
    decision: dict,
    analysis: dict,
    pattern: dict,
    risk: dict,
    trace_id: Optional[str] = None,
) -> str:
    """
    Generate a human-readable trade explanation for BUY/SELL decisions.

    Args:
        symbol:   Stock symbol.
        decision: Output from decision_agent (action, probability_up, reason, …).
        analysis: Output from analysis_agent (trend, sentiment, …).
        pattern:  Output from pattern_engine (pattern, confidence, direction).
        risk:     Output from risk_engine (entry, stop_loss, target, …).
        trace_id: Pipeline trace UUID.

    Returns:
        Human-readable explanation string. Falls back to technical reason on failure.
    """
    action = decision.get("action", "WAIT")

    if action == "WAIT":
        return _WAIT_MESSAGE

    # Bail out early if critical numbers are missing
    entry = risk.get("entry")
    if entry is None:
        return decision.get("reason", _WAIT_MESSAGE)

    stop_loss    = risk.get("stop_loss")
    target       = risk.get("target")
    max_loss_pct = risk.get("max_loss_pct", 0.0)
    rr_ratio     = risk.get("rr_ratio", 0.0)
    pos_frac     = risk.get("position_size_fraction", 0.0)
    pos_pct      = round((pos_frac or 0.0) * 100, 1)

    trend          = analysis.get("trend", "unknown")
    sentiment      = analysis.get("sentiment", "neutral")
    vol_signal     = analysis.get("volume_signal", 0.0)
    pattern_name   = pattern.get("pattern", "none")
    pat_confidence = pattern.get("confidence", 0.0)
    probability_up = decision.get("probability_up", 0.5)

    user_prompt = (
        f"Generate a trading signal explanation for:\n"
        f"Symbol: {symbol}\n"
        f"Action: {action}\n"
        f"Entry: ₹{entry:.2f}\n"
        f"Stop Loss: ₹{stop_loss:.2f} ({max_loss_pct:.1f}% risk)\n"
        f"Target: ₹{target:.2f} ({rr_ratio:.1f}:1 reward-to-risk)\n"
        f"Position Size: {pos_pct}% of capital\n\n"
        f"Signals:\n"
        f"- Trend: {trend}\n"
        f"- Pattern: {pattern_name} (confidence: {pat_confidence:.2f})\n"
        f"- Sentiment: {sentiment}\n"
        f"- Volume: {vol_signal:.3f}\n"
        f"- Probability up: {probability_up:.3f}\n\n"
        f"Explain WHY this trade is being taken in plain English."
    )

    raw = call_llm(
        prompt=user_prompt,
        system=_SYSTEM_PROMPT,
        model="llama-3.1-8b-instant",
        max_tokens=200,
        trace_id=trace_id,
        agent_name="explanation_agent",
    )

    if raw and raw.strip():
        return raw.strip()

    # Fallback to technical reason
    _logger.info("[LLM_EXPLANATION] Falling back to technical reason")
    return decision.get("reason", _WAIT_MESSAGE)
