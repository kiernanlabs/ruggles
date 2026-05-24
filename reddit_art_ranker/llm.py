"""LLM client + group-ranking prompt."""

import json
import os
import random

from dotenv import load_dotenv
from openai import OpenAI

from .config import LLM_MODEL

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
both, never neither."""


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment.")
    return OpenAI(api_key=api_key)


def rank_group(image_urls: list, model: str = LLM_MODEL, shuffle: bool = True) -> dict:
    """Ask the LLM to rank a group of images best-to-worst.

    Returns {'order': [original_index, ...], 'rationale': str, 'labels': [...]}
    where order is in terms of the ORIGINAL input position (so callers can
    map back to piece_ids).
    """
    n = len(image_urls)
    indices = list(range(n))
    if shuffle:
        random.shuffle(indices)

    labels = [chr(ord("A") + i) for i in range(n)]
    schema = {
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

    content = [
        {
            "type": "input_text",
            "text": f"Here are {n} watercolor pieces labeled {', '.join(labels)}. "
            "Rank them best to worst.",
        }
    ]
    for label, shuffled_idx in zip(labels, indices):
        content.append({"type": "input_text", "text": f"Piece {label}:"})
        content.append({"type": "input_image", "image_url": image_urls[shuffled_idx]})

    response = _client().responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "group_ranking",
                "schema": schema,
                "strict": True,
            }
        },
    )

    parsed = json.loads(response.output_text)
    label_to_original = {label: indices[i] for i, label in enumerate(labels)}

    not_art_labels = parsed.get("not_art", [])
    ranking_items = parsed["ranking"]

    # Validate: every label must appear exactly once across not_art + ranking
    covered = list(not_art_labels) + [item["label"] for item in ranking_items]
    if sorted(covered) != sorted(labels):
        raise ValueError(
            f"LLM response label coverage invalid: got {sorted(covered)}, expected {sorted(labels)}"
        )

    order = [label_to_original[item["label"]] for item in ranking_items]
    per_piece_rationales = [
        {"original_index": label_to_original[item["label"]], "rationale": item["rationale"]}
        for item in ranking_items
    ]
    not_art_indices = [label_to_original[lbl] for lbl in not_art_labels]
    return {
        "order": order,
        "not_art_indices": not_art_indices,
        "rationale": parsed.get("overall_rationale", ""),
        "per_piece_rationales": per_piece_rationales,
        "original_to_label": {indices[i]: labels[i] for i in range(n)},
    }
