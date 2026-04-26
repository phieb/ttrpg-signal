"""
Thin adapter over OpenAI / Anthropic / Gemini for DM storytelling calls.
Character extraction and session compression stay on Anthropic (Claude) directly
in dm_engine.py — those benefit from Claude's structured-JSON instruction-following.
"""

import logging
from dataclasses import dataclass

from config import (
    DM_PROVIDER,
    ANTHROPIC_API_KEY, ANTHROPIC_DM_MODEL,
    OPENAI_API_KEY, OPENAI_DM_MODEL,
    GEMINI_API_KEY, GEMINI_DM_MODEL,
    MAX_CONTEXT_TOKENS,
)

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    provider: str


# ── Provider implementations ──────────────────────────────────────────────────

def _call_openai(system: str, messages: list[dict]) -> LLMResponse:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    oai_messages = [{"role": "system", "content": system}] + [
        {"role": m["role"], "content": m["content"]} for m in messages
    ]
    resp = client.chat.completions.create(
        model=OPENAI_DM_MODEL,
        max_tokens=MAX_CONTEXT_TOKENS,
        messages=oai_messages,
    )
    return LLMResponse(
        text=resp.choices[0].message.content,
        input_tokens=resp.usage.prompt_tokens,
        output_tokens=resp.usage.completion_tokens,
        provider="openai",
    )


def _call_anthropic(system_blocks: list[dict], messages: list[dict]) -> LLMResponse:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=ANTHROPIC_DM_MODEL,
        max_tokens=MAX_CONTEXT_TOKENS,
        system=system_blocks,
        messages=messages,
    )
    return LLMResponse(
        text=resp.content[0].text,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        provider="anthropic",
    )


def _call_gemini(system: str, messages: list[dict]) -> LLMResponse:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_DM_MODEL,
        system_instruction=system,
    )
    gemini_history = []
    for m in messages[:-1]:
        gemini_history.append({
            "role": "user" if m["role"] == "user" else "model",
            "parts": [m["content"]],
        })
    chat = model.start_chat(history=gemini_history)
    resp = chat.send_message(messages[-1]["content"])
    # Gemini token counts via usage_metadata
    usage = getattr(resp, "usage_metadata", None)
    return LLMResponse(
        text=resp.text,
        input_tokens=getattr(usage, "prompt_token_count", 0),
        output_tokens=getattr(usage, "candidates_token_count", 0),
        provider="gemini",
    )


# ── Public interface ──────────────────────────────────────────────────────────

def chat(system_blocks: list[dict], messages: list[dict]) -> LLMResponse:
    """
    Call the configured DM provider.
    system_blocks: Anthropic-style list of {"type": "text", "text": ..., ["cache_control": ...]}
    For OpenAI and Gemini the blocks are joined into a single system string.
    """
    if not messages:
        raise ValueError("messages must not be empty")

    provider = DM_PROVIDER.lower()

    if provider == "openai":
        system_str = "\n\n".join(b["text"] for b in system_blocks)
        return _call_openai(system_str, messages)

    if provider == "anthropic":
        return _call_anthropic(system_blocks, messages)

    if provider == "gemini":
        system_str = "\n\n".join(b["text"] for b in system_blocks)
        return _call_gemini(system_str, messages)

    raise ValueError(f"Unknown DM_PROVIDER: {DM_PROVIDER!r} — must be openai, anthropic, or gemini")
