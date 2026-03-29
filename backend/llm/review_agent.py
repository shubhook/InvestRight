"""
LLM pre-execution safety review.
Called on BUY/SELL decisions before action_agent stores the trade.
Uses llama-3.3-70b-versatile (Groq) for better reasoning.

Fail-open: if LLM is unavailable, returns approved=True.
"""
import json
import logging
from typing import Optional

from llm.llm_client import call_llm

_logger = logging.getLogger("review_agent")

_SYSTEM_PROMPT = (
    "You are a risk management system reviewing trading signals "
    "before execution. Your job is to flag obviously bad trades. "
    "You are NOT trying to predict market direction. "
    "You are checking for logical inconsistencies and extreme risk "
    "parameters only. "
    "Respond ONLY with valid JSON. No markdown."
)

_AUTO_APPROVED = {
    "approved":      True,
    "flags":         [],
    "risk_level":    "low",
    "reviewer_note": "Auto-approved (LLM reviewer unavailable).",
    "source":        "auto_approved",
}


def review_decision(
    symbol: str,
    decision: dict,
    analysis: dict,
    pattern: dict,
    risk: dict,
    trace_id: Optional[str] = None,
) -> dict:
    """
    Review a BUY/SELL decision for logical consistency.

    Args:
        symbol:   Stock symbol.
        decision: Output from decision_agent.
        analysis: Output from analysis_agent.
        pattern:  Output from pattern_engine.
        risk:     Output from risk_engine.
        trace_id: Pipeline trace UUID.

    Returns:
        {
            "approved":      bool,
            "flags":         list[str],
            "risk_level":    "low" | "medium" | "high",
            "reviewer_note": str,
            "source":        "llm" | "auto_approved"
        }

    Fail-open: returns approved=True if LLM fails.
    """
    action = decision.get("action", "WAIT")
    if action == "WAIT":
        return _AUTO_APPROVED

    entry         = risk.get("entry", 0.0) or 0.0
    stop_loss     = risk.get("stop_loss", 0.0) or 0.0
    target        = risk.get("target", 0.0) or 0.0
    max_loss_pct  = risk.get("max_loss_pct", 0.0) or 0.0
    pos_frac      = risk.get("position_size_fraction", 0.0) or 0.0
    pattern_name  = pattern.get("pattern", "none")
    pat_conf      = pattern.get("confidence", 0.0)
    pat_direction = pattern.get("direction", "neutral")
    trend         = analysis.get("trend", "unknown")
    sentiment     = analysis.get("sentiment", "neutral")
    prob_up       = decision.get("probability_up", 0.5)

    user_prompt = (
        f"Review this trade signal for logical consistency:\n"
        f"Symbol: {symbol}\n"
        f"Action: {action}\n"
        f"Entry: {entry:.2f}\n"
        f"Stop Loss: {stop_loss:.2f}\n"
        f"Target: {target:.2f}\n"
        f"Max Loss: {max_loss_pct:.2f}%\n"
        f"Kelly Fraction: {pos_frac:.4f}\n"
        f"Pattern: {pattern_name} confidence={pat_conf:.2f} direction={pat_direction}\n"
        f"Trend: {trend}\n"
        f"Sentiment: {sentiment}\n"
        f"P(up): {prob_up:.3f}\n\n"
        "Flag ONLY if:\n"
        "1. Stop loss is above entry for a BUY (invalid)\n"
        "2. Stop loss is below entry for a SELL (invalid)\n"
        "3. Max loss exceeds 8% (dangerously high)\n"
        "4. Pattern direction contradicts action "
        "(bearish pattern + BUY = contradiction)\n"
        "5. Pattern confidence below 0.3 (extremely weak signal)\n\n"
        "Respond with exactly:\n"
        "{\n"
        '  "approved": true | false,\n'
        '  "flags": [],\n'
        '  "risk_level": "low" | "medium" | "high",\n'
        '  "reviewer_note": "one sentence"\n'
        "}"
    )

    raw = call_llm(
        prompt=user_prompt,
        system=_SYSTEM_PROMPT,
        model="llama-3.3-70b-versatile",
        max_tokens=300,
        trace_id=trace_id,
        agent_name="review_agent",
    )

    if raw is None:
        return _AUTO_APPROVED

    parsed = _parse_response(raw)
    if parsed is None:
        return _AUTO_APPROVED

    # Guard: if flags is empty but approved=False, treat as approved (inconsistent)
    if not parsed["approved"] and not parsed.get("flags"):
        _logger.warning("[LLM_REVIEW] approved=False with empty flags — treating as approved")
        parsed["approved"] = True

    return parsed


def _parse_response(raw: str) -> Optional[dict]:
    """Parse the JSON review response. Returns None on any parse error."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text  = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        data     = json.loads(text)
        approved = bool(data.get("approved", True))
        flags    = data.get("flags", [])
        if not isinstance(flags, list):
            flags = []

        risk_level = data.get("risk_level", "low")
        if risk_level not in ("low", "medium", "high"):
            risk_level = "low"

        return {
            "approved":      approved,
            "flags":         flags,
            "risk_level":    risk_level,
            "reviewer_note": str(data.get("reviewer_note", "")),
            "source":        "llm",
        }

    except Exception as e:
        _logger.warning(f"[LLM_REVIEW] Parse error: {e}")
        return None
