"""All Claude calls live here.

Two generations power the product:

  suggest_topics()  -> the end-of-day digest: what's adjacent to today's work
  generate_lesson() -> the Duolingo-style lesson for one chosen topic

Both use structured outputs (`output_config.format`) so the response is
schema-valid JSON rather than prose we have to parse defensively. Both stream,
because a full lesson is a large generation and non-streaming requests at high
max_tokens hit SDK HTTP timeouts.
"""

from __future__ import annotations

import json
import logging
import re

import anthropic

from .config import EFFORT, MODEL

log = logging.getLogger("tangent.llm")

_client: anthropic.Anthropic | None = None
# Server-side refusal fallbacks are a beta; if the installed SDK or the account
# doesn't have it, we fall back to the plain endpoint after the first failure.
_use_fallbacks = True


class LLMError(RuntimeError):
    pass


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login`
        # profile — in that order. Don't pass a key explicitly.
        _client = anthropic.Anthropic()
    return _client


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

TOPICS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "topics"],
    "properties": {
        "summary": {
            "type": "string",
            "description": "One sentence naming what this person actually worked on today.",
        },
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "blurb", "why_now", "category", "difficulty"],
                "properties": {
                    "title": {"type": "string"},
                    "blurb": {
                        "type": "string",
                        "description": "Two sentences on what the topic covers.",
                    },
                    "why_now": {
                        "type": "string",
                        "description": "One sentence tying it to today's specific work.",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "domain",
                            "technical",
                            "regulatory",
                            "commercial",
                            "frontier",
                        ],
                    },
                    "difficulty": {
                        "type": "string",
                        "enum": ["intro", "working", "advanced"],
                    },
                },
            },
        },
    },
}

LESSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "subtitle", "estimated_minutes", "cards", "questions"],
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "estimated_minutes": {"type": "integer"},
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["heading", "body", "key_terms", "diagram_svg"],
                "properties": {
                    "heading": {"type": "string"},
                    "body": {
                        "type": "string",
                        "description": (
                            "80-140 words of plain prose. No markdown headers. "
                            "Use **bold** for the terms that matter."
                        ),
                    },
                    "key_terms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["term", "definition"],
                            "properties": {
                                "term": {"type": "string"},
                                "definition": {"type": "string"},
                            },
                        },
                    },
                    "diagram_svg": {
                        "type": "string",
                        "description": (
                            "Optional inline SVG illustrating the card, or an empty "
                            "string. Must start with <svg and set "
                            'viewBox="0 0 640 320". Use currentColor for strokes and '
                            "text so it works in light and dark mode. No scripts, no "
                            "external references, no raster images."
                        ),
                    },
                },
            },
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "prompt", "options", "answer_index", "explanation"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["mcq", "true_false", "fill_blank"],
                    },
                    "prompt": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "2 options for true_false, 4 for mcq and fill_blank. "
                            "Wrong options must be plausible, not filler."
                        ),
                    },
                    "answer_index": {
                        "type": "integer",
                        "description": "0-based index into options.",
                    },
                    "explanation": {
                        "type": "string",
                        "description": "Why the answer is right — shown after answering.",
                    },
                },
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Core call
# --------------------------------------------------------------------------- #


def _is_unsupported_fallbacks(exc: Exception) -> bool:
    """Distinguish 'this SDK/account doesn't have the fallbacks beta' from every
    other TypeError — notably the auth one, which must not silently disable it."""
    text = str(exc).lower()
    return "fallback" in text or "unexpected keyword" in text or "beta" in text


def _stream(params: dict, with_fallbacks: bool):
    if with_fallbacks:
        return client().beta.messages.stream(
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **params,
        )
    return client().messages.stream(**params)


def _generate(system: str, prompt: str, schema: dict, max_tokens: int = 16000) -> dict:
    global _use_fallbacks

    params = dict(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        if _use_fallbacks:
            try:
                with _stream(params, True) as stream:
                    message = stream.get_final_message()
            except (TypeError, AttributeError, anthropic.BadRequestError) as exc:
                if not _is_unsupported_fallbacks(exc):
                    raise
                log.warning("Server-side fallbacks unavailable (%s); continuing without.", exc)
                _use_fallbacks = False
                with _stream(params, False) as stream:
                    message = stream.get_final_message()
        else:
            with _stream(params, False) as stream:
                message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise LLMError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Rate limited by the Anthropic API — try again shortly.") from exc
    except anthropic.APIError as exc:
        raise LLMError(f"Anthropic API error: {exc}") from exc
    except TypeError as exc:
        if "authentication" in str(exc).lower():
            raise LLMError(
                "No Anthropic credentials found. Put ANTHROPIC_API_KEY in your .env "
                "and restart the server."
            ) from exc
        raise

    if message.stop_reason == "refusal":
        raise LLMError(
            "Claude declined to generate this one. Try a differently-worded topic."
        )
    if message.stop_reason == "max_tokens":
        raise LLMError("The lesson came back truncated. Try again.")

    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        raise LLMError("Empty response from the model.")
    return json.loads(text)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

TOPIC_SYSTEM = """You design an adjacent-learning curriculum for working professionals.

The person already knows their own job. Never suggest the thing they spent today \
doing — suggest the ring around it: the neighbouring discipline, the counterparty's \
point of view, the regulation that shapes it, the technique one level past what \
they used, the frontier research that will change it.

Rules for the six topics you return:
- Each must be teachable in a focused 10-minute lesson. "Machine learning" is not a \
  topic; "why gradient boosting beats a GLM on sparse claims data" is.
- Spread them across categories. Two from the same category at most.
- Vary difficulty. At least one `intro` and at least one `advanced`.
- `why_now` must reference something concrete from today's log, not a generic tie-in.
- No corporate filler. Write like a sharp colleague, not a course catalogue."""

LESSON_SYSTEM = """You write short, interactive lessons in the style of a very good \
explainer — Duolingo's pacing with a technical writer's precision.

Structure: 4 to 6 concept cards, then 6 questions.

Cards:
- One idea per card. Build in order; a later card may assume an earlier one.
- Concrete over abstract. Real numbers, real scenarios, named examples.
- Include a `diagram_svg` for at least two cards where a picture genuinely helps \
  (a process flow, a comparison, a distribution, a decision tree). Leave it as an \
  empty string when a diagram would just be decoration.
- SVG rules: viewBox="0 0 640 320", stroke and fill "currentColor" or explicit hex, \
  font-size at least 13, no <script>, no external hrefs, no <image>.

Questions:
- Test understanding, not recall of your own wording.
- Distractors must be things a smart person could actually believe.
- Explanations teach something extra; never just restate the answer.

The reader is a competent professional learning something adjacent to their work. \
Don't flatter them, don't over-explain, don't hedge."""


def suggest_topics(role: str, activities: list[str]) -> dict:
    """Turn a day's activity log into six adjacent topics."""
    activity_block = "\n".join(f"- {a}" for a in activities) or "- (nothing logged)"
    prompt = (
        f"The person describes their role as: {role or 'not specified'}\n\n"
        f"What they worked on / searched for today:\n{activity_block}\n\n"
        "Give me the day's summary and six adjacent topics."
    )
    return _generate(TOPIC_SYSTEM, prompt, TOPICS_SCHEMA, max_tokens=8000)


def generate_lesson(role: str, topic: dict, day_summary: str = "") -> dict:
    """Write a full lesson for one topic."""
    prompt = (
        f"Learner's role: {role or 'not specified'}\n"
        f"What they worked on today: {day_summary or 'not specified'}\n\n"
        f"Topic: {topic['title']}\n"
        f"Scope: {topic.get('blurb', '')}\n"
        f"Relevance: {topic.get('why_now', '')}\n"
        f"Level: {topic.get('difficulty', 'working')}\n\n"
        "Write the lesson."
    )
    lesson = _generate(LESSON_SYSTEM, prompt, LESSON_SCHEMA)
    for card in lesson.get("cards", []):
        card["diagram_svg"] = sanitize_svg(card.get("diagram_svg", ""))
    return lesson


# --------------------------------------------------------------------------- #
# SVG sanitising
# --------------------------------------------------------------------------- #

_SCRIPTISH = re.compile(
    r"<\s*(script|foreignObject|iframe|image|use)\b[^>]*>.*?<\s*/\s*\1\s*>"
    r"|<\s*(script|foreignObject|iframe|image|use)\b[^>]*/?>",
    re.IGNORECASE | re.DOTALL,
)
_EVENT_ATTR = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_URL_SCHEME = re.compile(r"(href|xlink:href|src)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)


def sanitize_svg(svg: str) -> str:
    """Model output is untrusted markup; strip anything executable before it
    reaches the DOM."""
    if not svg or "<svg" not in svg.lower():
        return ""
    cleaned = _SCRIPTISH.sub("", svg)
    cleaned = _EVENT_ATTR.sub("", cleaned)
    cleaned = _URL_SCHEME.sub("", cleaned)
    if "javascript:" in cleaned.lower():
        return ""
    return cleaned.strip()
