"""
API-Nutzungs-Tracking für Anthropic (Claude), OpenAI, Gemini und Vertex AI (Imagen).
Persistiert in TTRPG_PATH/usage.json — bleibt über Bot-Neustarts erhalten.

Kosten (Stand 2025):
  Claude Haiku 4.5:  Input $0.80/M, Output $4.00/M, Cache-Read $0.08/M, Cache-Write $1.00/M
  GPT-4o:            Input $2.50/M, Output $10.00/M
  Gemini 2.0 Flash:  Input $0.10/M, Output $0.40/M
  Imagen 4 Fast:     $0.02/Bild
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from config import TTRPG_PATH

logger = logging.getLogger(__name__)

USAGE_FILE = Path(TTRPG_PATH) / "usage.json"

# Kosten in USD pro Token (bzw. pro Bild)
_ANTHROPIC_COST = {
    "input":       0.80 / 1_000_000,
    "output":      4.00 / 1_000_000,
    "cache_read":  0.08 / 1_000_000,
    "cache_write": 1.00 / 1_000_000,
}
_OPENAI_COST = {
    "input":  2.50 / 1_000_000,
    "output": 10.00 / 1_000_000,
}
_GEMINI_COST = {
    "input":  0.10 / 1_000_000,
    "output": 0.40 / 1_000_000,
}
_IMAGEN_COST_PER_IMAGE = 0.02


def _load() -> dict:
    try:
        if USAGE_FILE.exists():
            return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"usage.json lesen fehlgeschlagen: {e}")
    return {
        "anthropic": {"total": _empty_anthropic(), "by_month": {}},
        "openai":    {"total": _empty_tokens(),    "by_month": {}},
        "gemini":    {"total": _empty_tokens(),    "by_month": {}},
        "vertex":    {"total": _empty_vertex(),    "by_month": {}},
    }


def _save(data: dict) -> None:
    try:
        USAGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"usage.json schreiben fehlgeschlagen: {e}")


def _empty_anthropic() -> dict:
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _empty_tokens() -> dict:
    return {"input": 0, "output": 0}


def _empty_vertex() -> dict:
    return {"images": 0}


def _month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def track_anthropic(input_tokens: int, output_tokens: int,
                    cache_read: int = 0, cache_write: int = 0) -> None:
    """Zählt einen Anthropic API-Aufruf mit."""
    data = _load()
    for bucket in (data["anthropic"]["total"],
                   data["anthropic"]["by_month"].setdefault(_month(), _empty_anthropic())):
        bucket["input"]       += input_tokens
        bucket["output"]      += output_tokens
        bucket["cache_read"]  += cache_read
        bucket["cache_write"] += cache_write
    _save(data)


def track_dm(provider: str, input_tokens: int, output_tokens: int) -> None:
    """Tracks a DM storytelling call for any provider."""
    if provider == "anthropic":
        track_anthropic(input_tokens, output_tokens)
        return
    key = provider  # "openai" or "gemini"
    data = _load()
    if key not in data:
        data[key] = {"total": _empty_tokens(), "by_month": {}}
    for bucket in (data[key]["total"],
                   data[key]["by_month"].setdefault(_month(), _empty_tokens())):
        bucket["input"]  += input_tokens
        bucket["output"] += output_tokens
    _save(data)


def track_imagen(count: int = 1) -> None:
    """Zählt generierte Vertex AI Bilder."""
    data = _load()
    for bucket in (data["vertex"]["total"],
                   data["vertex"]["by_month"].setdefault(_month(), _empty_vertex())):
        bucket["images"] += count
    _save(data)


def _anthropic_cost(b: dict) -> float:
    return (b["input"]       * _ANTHROPIC_COST["input"]
          + b["output"]      * _ANTHROPIC_COST["output"]
          + b["cache_read"]  * _ANTHROPIC_COST["cache_read"]
          + b["cache_write"] * _ANTHROPIC_COST["cache_write"])


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _tokens_cost(b: dict, cost: dict) -> float:
    return b["input"] * cost["input"] + b["output"] * cost["output"]


def _tokens_line(b: dict) -> str:
    return f"{_fmt_tokens(b['input'])} input · {_fmt_tokens(b['output'])} output"


def get_summary() -> str:
    data = _load()
    month = _month()
    lines = ["📊 **API-Nutzung**\n"]

    # ── Anthropic (utility: extraction, compression) ──────────────────────────
    at = data["anthropic"]["total"]
    am = data["anthropic"]["by_month"].get(month, _empty_anthropic())
    lines.append("**Anthropic (Claude Haiku — utility)**")
    lines.append(f"Gesamt:  {_fmt_tokens(at['input'])} input · {_fmt_tokens(at['output'])} output · "
                 f"cache-read {_fmt_tokens(at['cache_read'])} · cache-write {_fmt_tokens(at['cache_write'])}")
    lines.append(f"{month}:  {_fmt_tokens(am['input'])} input · {_fmt_tokens(am['output'])} output")
    lines.append(f"Kosten {month}: ~${_anthropic_cost(am):.3f} | gesamt: ~${_anthropic_cost(at):.3f}\n")

    # ── OpenAI ────────────────────────────────────────────────────────────────
    ot = data.get("openai", {}).get("total", _empty_tokens())
    om = data.get("openai", {}).get("by_month", {}).get(month, _empty_tokens())
    if ot["input"] or ot["output"]:
        lines.append("**OpenAI (GPT-4o — DM)**")
        lines.append(f"Gesamt:  {_tokens_line(ot)}")
        lines.append(f"{month}:  {_tokens_line(om)}")
        lines.append(f"Kosten {month}: ~${_tokens_cost(om, _OPENAI_COST):.3f} | gesamt: ~${_tokens_cost(ot, _OPENAI_COST):.3f}\n")

    # ── Gemini ────────────────────────────────────────────────────────────────
    gt = data.get("gemini", {}).get("total", _empty_tokens())
    gm = data.get("gemini", {}).get("by_month", {}).get(month, _empty_tokens())
    if gt["input"] or gt["output"]:
        lines.append("**Gemini (DM)**")
        lines.append(f"Gesamt:  {_tokens_line(gt)}")
        lines.append(f"{month}:  {_tokens_line(gm)}")
        lines.append(f"Kosten {month}: ~${_tokens_cost(gm, _GEMINI_COST):.3f} | gesamt: ~${_tokens_cost(gt, _GEMINI_COST):.3f}\n")

    # ── Vertex AI ─────────────────────────────────────────────────────────────
    vt = data["vertex"]["total"]
    vm = data["vertex"]["by_month"].get(month, _empty_vertex())
    lines.append("**Vertex AI (Imagen 4 Fast)**")
    lines.append(f"Gesamt:  {vt['images']} Bilder (~${vt['images'] * _IMAGEN_COST_PER_IMAGE:.2f})")
    lines.append(f"{month}:  {vm['images']} Bilder (~${vm['images'] * _IMAGEN_COST_PER_IMAGE:.2f})")

    return "\n".join(lines)
