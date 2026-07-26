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
import random
import re

import anthropic

from .config import EFFORT, LESSON_EFFORT, LESSON_MODELS, MODEL, VISION_MODEL

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
                "required": [
                    "heading",
                    "body",
                    "intuition",
                    "key_terms",
                    "diagram_svg",
                    "diagram_caption",
                ],
                "properties": {
                    "heading": {"type": "string"},
                    "body": {
                        "type": "string",
                        "description": (
                            "150-260 words of plain prose. Explain the mechanism, not "
                            "just the label — why it works this way, what happens if "
                            "it doesn't, what a real instance looks like with real "
                            "numbers. No markdown headers. **Bold** the terms that "
                            "matter."
                        ),
                    },
                    "intuition": {
                        "type": "string",
                        "description": (
                            "The plain-language version: an analogy, a limiting case, "
                            "or the one sentence that makes it click. This is what the "
                            "reader would repeat to a colleague. Written to stand "
                            "alone — never 'as explained above'. Empty string only if "
                            "the card is purely definitional."
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
                            "Inline SVG illustrating this card's idea. Must start with "
                            '<svg and set viewBox="0 0 640 360". Use currentColor for '
                            "strokes and text so it works in light and dark mode. No "
                            "scripts, no external references, no raster images. Empty "
                            "string only when a picture genuinely adds nothing."
                        ),
                    },
                    "diagram_caption": {
                        "type": "string",
                        "description": (
                            "One sentence telling the reader what to notice in the "
                            "diagram — not a restatement of its title. Empty when "
                            "there is no diagram."
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


# `effort` is not universally accepted: the small/older models reject it
# outright rather than ignoring it, so it has to be omitted per model. A
# blacklist rather than a whitelist, so a future model doesn't silently lose
# the parameter.
_NO_EFFORT_PREFIXES = (
    "claude-haiku",
    "claude-sonnet-4-5",
    "claude-3",
    "claude-2",
)


def _supports_effort(model: str) -> bool:
    return not model.startswith(_NO_EFFORT_PREFIXES)


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


def _generate(
    system: str,
    prompt: str,
    schema: dict,
    max_tokens: int = 16000,
    images: list[tuple[str, str]] | None = None,
    model: str | None = None,
    use_fallbacks: bool = True,
    effort: str | None = None,
) -> dict:
    global _use_fallbacks

    content: list[dict] = []
    for media_type, data in images or []:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            }
        )
    content.append({"type": "text", "text": prompt})

    target = model or MODEL
    output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
    if _supports_effort(target):
        output_config["effort"] = effort or EFFORT

    params = dict(
        model=target,
        max_tokens=max_tokens,
        system=system,
        output_config=output_config,
        messages=[{"role": "user", "content": content}],
    )

    try:
        if _use_fallbacks and use_fallbacks:
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

    # Log real token usage: cost estimates for this kind of generation are easy
    # to get wrong by 5-10x, and the caps are only as good as the numbers behind
    # them. Watch these lines in the platform log to set the caps from evidence.
    usage = getattr(message, "usage", None)
    if usage is not None:
        log.info(
            "generation ok model=%s in=%s out=%s cache_read=%s",
            getattr(message, "model", MODEL),
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
            getattr(usage, "cache_read_input_tokens", 0),
        )

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

LESSON_SYSTEM = """You write interactive lessons in the style of a very good explainer \
— Duolingo's pacing with a technical writer's precision and a good teacher's instinct \
for the example that makes something click.

Structure: 6 to 8 concept cards, then 6 to 8 questions.

You are told how much the learner already knows, on a 1-10 scale, sometimes backed by their answers to two diagnostic questions. Calibrate to it — this is the single most important thing you do:
- 1-3: they have heard the words. Define every term on first use, start from why the thing exists at all, and use one running example throughout rather than several.
- 4-7: they work near this but not in it. Skip the definitions they'd find patronising, spend the space on mechanism, edge cases and how it interacts with what they do know.
- 8-10: they are close to a practitioner. Skip foundations entirely. Go straight to the contested cases, the failure modes, the things that surprise people who thought they knew this. Assume they can read notation and want the real argument.
If the diagnostic answers contradict the self-rating, trust the answers — people are routinely modest or overconfident about their own level. Say nothing about the rating or the diagnostics in the lesson itself; just pitch it correctly.

Teach for intuition first, rigour second:
- Lead with the mechanism. Why does it work this way? What breaks if it doesn't? The \
  reader should finish able to reason about a case you never mentioned, not just \
  recognise the term.
- Every abstract claim gets a concrete instance immediately — real numbers, a named \
  scenario, an actual worked case. "Aggregation can multiply exposure" is weak; \
  "twelve identical bad conveyancing files: one £2m limit if they aggregate, twelve \
  if they don't" is the lesson.
- Use the `intuition` field for the analogy or limiting case that makes the idea \
  obvious — a comparison from ordinary life, or from a field the reader already knows. \
  This is the sentence they'd repeat to a colleague.
- Anticipate the misconception. If practitioners routinely get something backwards, \
  say so explicitly and correct it.
- Build in order. Later cards may lean on earlier ones; each card still has one idea.

Diagrams carry real weight here — aim for one on most cards:
- Choose the form that fits the idea: timelines for sequence, side-by-side panels for \
  comparison, curves with labelled axes for relationships, flows with arrowheads for \
  process, nested boxes for structure, small multiples for how a parameter changes \
  things. Vary them across the lesson; six timelines is a failure.
- A diagram must add information the prose doesn't. Label the axes, mark the values \
  that matter, annotate the interesting region. A box containing the card's title is \
  worse than no diagram.
- SVG rules: viewBox="0 0 640 360", stroke and fill "currentColor" or explicit hex, \
  font-size at least 13, keep all text inside the viewBox, no <script>, no external \
  hrefs, no <image>. Arrowheads via <defs><marker> are encouraged for flows.

Questions:
- Test understanding, not recall of your wording. At least two should apply the idea \
  to a situation the cards never showed.
- Distractors must be things a smart person could actually believe — ideally the \
  misconceptions you corrected.
- Vary which option holds the correct answer across the set. Never put it first every
  time.
- Explanations teach something extra; never just restate the answer.

The reader is a competent professional learning something adjacent to their work. \
Assume intelligence, not knowledge: never condescend, and never leave a step implicit \
because it feels obvious to you. Don't flatter, don't hedge, don't pad."""


def suggest_topics(role: str, activities: list[str]) -> dict:
    """Turn a day's activity log into six adjacent topics."""
    activity_block = "\n".join(f"- {a}" for a in activities) or "- (nothing logged)"
    prompt = (
        f"The person describes their role as: {role or 'not specified'}\n\n"
        f"What they worked on / searched for today:\n{activity_block}\n\n"
        "Give me the day's summary and six adjacent topics."
    )
    return _generate(TOPIC_SYSTEM, prompt, TOPICS_SCHEMA, max_tokens=8000)


def generate_lesson(
    role: str,
    topic: dict,
    day_summary: str = "",
    level: int | None = None,
    placement: list[dict] | None = None,
) -> dict:
    """Write a full lesson for one topic, pitched at the learner's level."""
    lines = [
        f"Learner's role: {role or 'not specified'}",
        f"What they worked on today: {day_summary or 'not specified'}",
        "",
        f"Topic: {topic['title']}",
        f"Scope: {topic.get('blurb', '')}",
        f"Relevance: {topic.get('why_now', '')}",
    ]
    if level:
        lines.append(f"They rate their own knowledge of this topic {level} out of 10.")
    if placement:
        lines.append("")
        lines.append("They answered two diagnostic questions before starting:")
        for item in placement:
            verdict = "correct" if item.get("correct") else "wrong"
            lines.append(
                f"- {item.get('probes') or item.get('prompt', '')} — answered {verdict}"
            )
    # A beginner's lesson is foundational, not exhaustive — fewer, longer cards
    # teach better here and cut generation time, which is dominated by output
    # size and by the SVG diagrams in particular.
    band = "new" if (level or 5) <= 3 else "deep" if (level or 5) >= 8 else "working"
    if band == "new":
        lines.append(
            "Keep this to 5 or 6 cards with a diagram on two or three of them: at "
            "this level, depth of explanation beats breadth of coverage."
        )
    lines += ["", "Write the lesson."]
    prompt = "\n".join(lines)
    # Headroom matters here: max_tokens caps thinking *and* output, and a lesson
    # with a diagram on most of 8 cards is genuinely large. Too tight and it
    # truncates mid-lesson rather than degrading gracefully.
    model = LESSON_MODELS.get(band, MODEL)
    effort = LESSON_EFFORT.get(band, EFFORT)
    log.info("generating %r at level %s (band=%s, %s/%s)", topic.get("title"), level, band, model, effort)
    lesson = _generate(
        LESSON_SYSTEM,
        prompt,
        LESSON_SCHEMA,
        max_tokens=32000,
        model=model,
        effort=effort,
    )
    shuffle_options(lesson)   # the prompt asks; this guarantees
    for card in lesson.get("cards", []):
        card["diagram_svg"] = sanitize_svg(card.get("diagram_svg", ""))
        if not card["diagram_svg"]:
            card["diagram_caption"] = ""
    return lesson


def shuffle_options(content: dict) -> dict:
    """Randomise each question's options and repoint answer_index.

    Language models have a strong position bias when writing multiple choice:
    the correct option tends to come first. A prompt instruction helps but does
    not hold, and a quiz whose answer is always A measures nothing. Shuffling
    after generation makes the bias structurally impossible rather than merely
    discouraged.

    Mutates and returns `content`.
    """
    for question in content.get("questions", []):
        options = question.get("options") or []
        answer = question.get("answer_index")
        if not options or not isinstance(answer, int) or not 0 <= answer < len(options):
            continue
        # True/False must stay in its conventional order — shuffling it just
        # makes the reader stumble, and there is no bias to remove from two
        # options whose labels carry their own meaning.
        if question.get("kind") == "true_false":
            continue
        order = list(range(len(options)))
        random.shuffle(order)
        question["options"] = [options[i] for i in order]
        question["answer_index"] = order.index(answer)
    return content


# --------------------------------------------------------------------------- #
# Placement — two quick questions to calibrate before writing the lesson
# --------------------------------------------------------------------------- #

PLACEMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["prompt", "options", "answer_index", "probes"],
                "properties": {
                    "prompt": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "answer_index": {"type": "integer"},
                    "probes": {
                        "type": "string",
                        "description": "The specific prerequisite this question tests.",
                    },
                },
            },
        }
    },
}

PLACEMENT_SYSTEM = """You write exactly two diagnostic questions to find out how much someone already knows about a topic, before a lesson is written for them.

These are not a test and there is no pass mark — they exist so the lesson can start in the right place. Write them accordingly:

- Question one tests a *foundational* idea: someone who works near this topic would get it right without thinking. Getting it wrong means start from first principles.
- Question two tests a *practitioner's* distinction — the kind of thing you only know from having done the work. Getting it right means skip the basics.
- Four options each. Wrong options must be genuinely plausible; an obviously silly option wastes the signal.
- Keep them short. Someone is answering these to get to the lesson, not to be assessed.
- No trick questions, no trivia, nothing that turns on a definition the field disagrees about."""


def placement_questions(role: str, topic: dict) -> list[dict]:
    """Two calibration questions for a topic. Uses the cheap model — this runs
    before every generated lesson, and it is a small, well-specified job."""
    prompt = (
        f"Learner's role: {role or 'not specified'}\n"
        f"Topic: {topic.get('title', '')}\n"
        f"Scope: {topic.get('blurb', '')}\n\n"
        "Write the two diagnostic questions."
    )
    result = _generate(
        PLACEMENT_SYSTEM,
        prompt,
        PLACEMENT_SCHEMA,
        max_tokens=1500,
        model=VISION_MODEL,
        use_fallbacks=False,
    )
    questions = result.get("questions", [])[:2]
    shuffle_options({"questions": questions})
    return questions


# --------------------------------------------------------------------------- #
# Screen-capture extraction
# --------------------------------------------------------------------------- #

OBSERVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["observations"],
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["activity", "evidence", "confidence"],
                "properties": {
                    "activity": {
                        "type": "string",
                        "description": (
                            "One line for the work log, in the user's voice: "
                            "'Priced a solicitors' PI claim', 'Read the Polars "
                            "docs on lazy frames'. Task and subject only."
                        ),
                    },
                    "evidence": {
                        "type": "string",
                        "description": (
                            "What on screen suggested it, described generically — "
                            "'a spreadsheet of claim development factors', 'library "
                            "documentation in a browser'. Never quote screen content."
                        ),
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                },
            },
        }
    },
}

OBSERVE_SYSTEM = """You are watching a few screenshots from someone's working \
session to write their work log for them. You name the *kind of work*, nothing else.

You are looking at a real person's actual screen, which will contain other people's \
personal information — client and claimant names, case and policy numbers, medical \
details, financial figures, email addresses, message contents. None of that belongs \
in a work log.

Absolute rules:
- Never reproduce, quote, paraphrase or allude to any name, identifier, number, \
address, email, or medical or financial detail visible on screen. Not in `activity`, \
not in `evidence`, not anywhere.
- Write at the level of the task and its subject matter: "priced a professional \
indemnity claim", not "priced the claim for [name]". "Reviewed a reinsurance treaty", \
not "reviewed the [company] treaty". Naming a public library, product, standard or \
public company is fine — naming a private individual or a client is not.
- If a frame shows something personal and nothing about the task, return no \
observation for it. Silence is always the safe output.

What makes a good observation:
- Merge frames that show one continuous task into a single observation. Three frames \
of the same spreadsheet is one line, not three.
- Prefer few, specific, high-quality lines over many vague ones. Two is usually right \
for a five-minute window; more than four almost never is.
- Skip anything that isn't work: personal browsing, email triage, chat, this app \
itself, a screensaver, an empty desktop.
- Be honest with `confidence`. "low" is the correct answer for a glimpse of an \
unfamiliar tool. The user is asked to confirm each line, and a wrong guess with high \
confidence costs them more than an honest low one.
- If nothing work-related is visible, return an empty list. That is a normal and \
frequent outcome — never invent activity to fill the space."""


def extract_activities(role: str, frames: list[tuple[str, str]]) -> list[dict]:
    """Turn a handful of screen frames into candidate log lines.

    `frames` are (media_type, base64) pairs held in memory only — nothing is
    written to disk here or by the caller.
    """
    if not frames:
        return []
    prompt = (
        f"This person describes their work as: {role or 'not specified'}\n\n"
        f"Here are {len(frames)} screenshots taken a few minutes apart during one "
        "working session, in order. What were they working on?"
    )
    result = _generate(
        OBSERVE_SYSTEM,
        prompt,
        OBSERVE_SCHEMA,
        max_tokens=2000,
        images=frames,
        model=VISION_MODEL,
        # Refusal fallbacks are configured per model; the small extraction model
        # isn't a valid fallback source, and a refusal here just means "return
        # nothing", which is already the desired behaviour.
        use_fallbacks=False,
    )
    return result.get("observations", [])


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
