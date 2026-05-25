"""LLM client + group-ranking prompt.

Talks to OpenRouter (https://openrouter.ai) using the OpenAI Python SDK
pointed at OpenRouter's OpenAI-compatible endpoint. Any vision-capable
chat-completions model on OpenRouter is usable by setting LLM_MODEL or
passing --model on the CLI.

Set OPENROUTER_API_KEY in .env. Falls back to OPENAI_API_KEY only as a
courtesy during migration (and only if OPENROUTER_API_KEY is missing).
"""

import json
import os
import random

from dotenv import load_dotenv
from openai import OpenAI

from .config import LLM_MODEL, OPENROUTER_BASE_URL

load_dotenv()

SYSTEM_PROMPT = """You are a juror in an art show, evaluating watercolor pieces.

You will be shown several submissions (labeled A, B, C, ...). For each one,
first decide whether it is actually an original piece of artwork. Non-art
submissions include: photos of supplies or palettes, contest announcements,
PSAs, memes, screenshots, store-bought items, before/after photos used purely
to ask a question, or reference photos shown without a finished painting.
List the labels of any non-art submissions in the `not_art` field.

Then rank the remaining (genuine artwork) submissions from best to worst.
Use your own judgment, informed broadly by typical jury criteria — technique,
composition, use of light and color, mood, originality, and overall execution
— but do not score each criterion separately. Output a single overall ranking
of the art submissions.

For each ranked piece, include a one-sentence rationale that names a concrete
reason for its placement (e.g. "loose wash control and confident negative
space" or "muddy mid-tones and uncertain edges"). Be specific — these
rationales are used to debug whether the ranking reflects real visual
properties.

If you cannot meaningfully distinguish two pieces, still produce a strict
ordering — break ties on overall impression.

Judge the artwork itself, not the documentation of it. Ignore any text,
watermarks, signatures, or labels in the images. Do not penalize a piece for
the photograph's quality — lighting of the snapshot, glare, perspective skew,
low resolution, cluttered background, or whether the piece is shown flat vs.
held up — these are properties of the photo, not the painting. Evaluate the
painting.

Every label must appear in exactly one of `not_art` or `ranking` — never
both, never neither.

Return ONLY valid JSON matching the requested schema. Do not wrap it in
markdown code fences."""

# Retries when the model returns malformed JSON or fails label-coverage
# validation. Some models on OpenRouter don't strictly enforce json_schema;
# the retry catches those drifts.
MAX_PARSE_RETRIES = 2

# Cap output length. OpenRouter pre-reserves credit for max_tokens, and
# without this it defaults to the model's full context (e.g. 65k for gpt-5),
# which can fail with 402. But too low and chatty/verbose models truncate
# mid-JSON and fail the parse-validate retry. 10k accommodates models that
# pad rationales heavily (Kimi, Qwen) without blowing the pre-reservation.
MAX_OUTPUT_TOKENS = 10000


def _client() -> OpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not set in environment. Get a key at "
            "https://openrouter.ai/keys and add it to .env. "
            "(An OpenAI API key won't work here — OpenRouter uses its own auth.)"
        )
    headers = {
        # Per OpenRouter conventions — appears on their public model usage page.
        "HTTP-Referer": os.getenv("OPENROUTER_REFERER", "https://github.com/local/ruggles"),
        "X-Title": os.getenv("OPENROUTER_APP_TITLE", "reddit-art-ranker"),
    }
    return OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, default_headers=headers)


def _build_schema(labels: list) -> dict:
    return {
        "type": "object",
        "properties": {
            "not_art": {
                "type": "array",
                "items": {"type": "string", "enum": labels},
                "description": "Labels of submissions that are not original artwork (supply photos, PSAs, contest posts, memes, reference-only images, etc). Empty array if all submissions are art.",
            },
            "ranking": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "enum": labels},
                        "rationale": {
                            "type": "string",
                            "description": "One sentence naming a concrete reason for this piece's placement (specific technique observation, not generic praise).",
                        },
                    },
                    "required": ["label", "rationale"],
                    "additionalProperties": False,
                },
                "description": "Pieces NOT in not_art, ordered best (first) to worst (last). Each label used exactly once.",
            },
            "overall_rationale": {
                "type": "string",
                "description": "1-2 sentences summarizing the basis for the overall ranking.",
            },
        },
        "required": ["not_art", "ranking", "overall_rationale"],
        "additionalProperties": False,
    }


def _build_message_content(labels: list, image_urls: list, n: int) -> list:
    """Chat Completions API content blocks: text + image_url alternating."""
    content = [
        {
            "type": "text",
            "text": (f"Here are {n} watercolor submissions labeled "
                     f"{', '.join(labels)}. Categorize each as art or non-art, "
                     "then rank the art pieces best to worst."),
        }
    ]
    for label, url in zip(labels, image_urls):
        content.append({"type": "text", "text": f"Piece {label}:"})
        content.append({"type": "image_url", "image_url": {"url": url}})
    return content


def _parse_and_validate(raw: str, labels: list) -> dict:
    """Parse JSON output and validate label coverage. Raises ValueError on drift."""
    # Some models leak markdown fences despite the prompt instruction.
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    parsed = json.loads(raw)

    not_art_labels = parsed.get("not_art", [])
    ranking_items = parsed.get("ranking", [])
    covered = list(not_art_labels) + [item["label"] for item in ranking_items]
    if sorted(covered) != sorted(labels):
        raise ValueError(
            f"LLM response label coverage invalid: got {sorted(covered)}, "
            f"expected {sorted(labels)}"
        )
    return parsed


def rank_group(image_urls: list, model: str = LLM_MODEL, shuffle: bool = True) -> dict:
    """Ask the LLM to rank a group of images best-to-worst, flagging non-art.

    Returns {
        'order': [original_index, ...],          # art-only, best -> worst
        'not_art_indices': [original_index, ...],
        'rationale': str,                        # overall
        'per_piece_rationales': [{'original_index': i, 'rationale': str}, ...],
        'original_to_label': {orig_idx: label}   # for debugging
    }
    """
    n = len(image_urls)
    indices = list(range(n))
    if shuffle:
        random.shuffle(indices)

    labels = [chr(ord("A") + i) for i in range(n)]
    shuffled_urls = [image_urls[idx] for idx in indices]
    schema = _build_schema(labels)
    content = _build_message_content(labels, shuffled_urls, n)
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "group_ranking",
            "schema": schema,
            "strict": True,
        },
    }

    client = _client()
    last_err = None
    last_usage = None
    last_finish_reason = None
    for attempt in range(1 + MAX_PARSE_RETRIES):
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            response_format=response_format,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        raw = response.choices[0].message.content or ""
        last_finish_reason = response.choices[0].finish_reason
        if response.usage:
            last_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        try:
            parsed = _parse_and_validate(raw, labels)
            break
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_err = e
            if attempt == MAX_PARSE_RETRIES:
                hint = ""
                if last_finish_reason == "length":
                    hint = (f" (finish_reason=length — model hit max_tokens="
                            f"{MAX_OUTPUT_TOKENS}; raise it.)")
                raise RuntimeError(
                    f"LLM returned invalid output after {1 + MAX_PARSE_RETRIES} "
                    f"attempts. Last error: {e}.{hint} Last raw: {raw[:400]}"
                ) from e
            # else: retry

    label_to_original = {label: indices[i] for i, label in enumerate(labels)}
    ranking_items = parsed["ranking"]
    order = [label_to_original[item["label"]] for item in ranking_items]
    per_piece_rationales = [
        {"original_index": label_to_original[item["label"]],
         "rationale": item["rationale"]}
        for item in ranking_items
    ]
    not_art_indices = [label_to_original[lbl] for lbl in parsed.get("not_art", [])]
    return {
        "order": order,
        "not_art_indices": not_art_indices,
        "rationale": parsed.get("overall_rationale", ""),
        "per_piece_rationales": per_piece_rationales,
        "original_to_label": {indices[i]: labels[i] for i in range(n)},
        "usage": last_usage,
        "finish_reason": last_finish_reason,
    }
