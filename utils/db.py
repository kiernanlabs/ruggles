import os
import uuid
from decimal import Decimal
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

TABLE_NAME = "ruggles_artworks_prod"
GSI_NAME = "by_created_at"
ENTITY_TYPE = "artwork"

# Maps the flat DDB columns back to the nested evaluation_data shape the UI expects.
# (pretty_key, flat_prefix)
_EVAL_GROUPS = [
    ("proportion_and_structure", "proportion"),
    ("line_quality", "line_quality"),
    ("value_and_light", "value_light"),
    ("detail_and_texture", "detail_texture"),
    ("composition_and_perspective", "composition_perspective"),
    ("form_and_volume", "form_volume"),
    ("mood_and_expression", "mood_expression"),
    ("overall_realism", "overall_realism"),
]


@st.cache_resource
def init_dynamodb():
    try:
        region = st.secrets["AWS_REGION"]
        access_key = st.secrets["AWS_ACCESS_KEY_ID"]
        secret_key = st.secrets["AWS_SECRET_ACCESS_KEY"]
    except Exception:
        region = os.getenv("AWS_REGION", "us-east-1")
        access_key = os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

    return boto3.resource(
        "dynamodb",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    ).Table(TABLE_NAME)


def _to_native(value):
    # boto3 returns Decimal for DDB Number; Streamlit charts prefer int/float.
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_to_native(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_native(v) for k, v in value.items()}
    return value


def _structure_evaluation(item: dict) -> dict:
    eval_data = {}
    for pretty, flat in _EVAL_GROUPS:
        score_key = f"{flat}_score"
        if score_key in item:
            eval_data[pretty] = {
                "score": item.pop(score_key, 0),
                "rationale": item.pop(f"{flat}_rationale", ""),
                "improvement_tips": item.pop(f"{flat}_tips", []),
            }
    item["evaluation_data"] = eval_data
    return item


def insert_artwork(artwork_data: dict):
    table = init_dynamodb()
    try:
        evaluation_data = artwork_data.pop("evaluation_data", {})
        gpt_response = artwork_data.get("gpt_response", "")

        sketch_type = artwork_data.get("sketch_type", "full realism")
        if sketch_type not in ("quick sketch", "full realism"):
            sketch_type = "full realism"

        st.write(f"Debug - Inserting with sketch type: {sketch_type}")

        now = datetime.now(timezone.utc)
        item = {
            "id": str(uuid.uuid4()),
            "entity_type": ENTITY_TYPE,
            "title": artwork_data.get("title", ""),
            "description": artwork_data.get("description", ""),
            "image_url": artwork_data.get("image_url", ""),
            "image_public_id": artwork_data.get("image_public_id", ""),
            "artist_name": artwork_data.get("artist_name", ""),
            "created_at": artwork_data.get("created_at", now.isoformat()),
            "artwork_date": artwork_data.get("artwork_date", now.strftime("%Y-%m-%d")),
            "sketch_type": sketch_type,
            "question": artwork_data.get("question", ""),
            "gpt_response": gpt_response,
            "proportion_score": evaluation_data.get("proportion_and_structure", {}).get("score", 0),
            "proportion_rationale": evaluation_data.get("proportion_and_structure", {}).get("rationale", ""),
            "proportion_tips": evaluation_data.get("proportion_and_structure", {}).get("improvement_tips", []),
            "line_quality_score": evaluation_data.get("line_quality", {}).get("score", 0),
            "line_quality_rationale": evaluation_data.get("line_quality", {}).get("rationale", ""),
            "line_quality_tips": evaluation_data.get("line_quality", {}).get("improvement_tips", []),
            "form_volume_score": evaluation_data.get("form_and_volume", {}).get("score", 0),
            "form_volume_rationale": evaluation_data.get("form_and_volume", {}).get("rationale", ""),
            "form_volume_tips": evaluation_data.get("form_and_volume", {}).get("improvement_tips", []),
            "mood_expression_score": evaluation_data.get("mood_and_expression", {}).get("score", 0),
            "mood_expression_rationale": evaluation_data.get("mood_and_expression", {}).get("rationale", ""),
            "mood_expression_tips": evaluation_data.get("mood_and_expression", {}).get("improvement_tips", []),
            "evaluation_version": "v1",
        }

        if sketch_type == "full realism":
            item.update({
                "value_light_score": evaluation_data.get("value_and_light", {}).get("score", 0),
                "value_light_rationale": evaluation_data.get("value_and_light", {}).get("rationale", ""),
                "value_light_tips": evaluation_data.get("value_and_light", {}).get("improvement_tips", []),
                "detail_texture_score": evaluation_data.get("detail_and_texture", {}).get("score", 0),
                "detail_texture_rationale": evaluation_data.get("detail_and_texture", {}).get("rationale", ""),
                "detail_texture_tips": evaluation_data.get("detail_and_texture", {}).get("improvement_tips", []),
                "composition_perspective_score": evaluation_data.get("composition_and_perspective", {}).get("score", 0),
                "composition_perspective_rationale": evaluation_data.get("composition_and_perspective", {}).get("rationale", ""),
                "composition_perspective_tips": evaluation_data.get("composition_and_perspective", {}).get("improvement_tips", []),
                "overall_realism_score": evaluation_data.get("overall_realism", {}).get("score", 0),
                "overall_realism_rationale": evaluation_data.get("overall_realism", {}).get("rationale", ""),
                "overall_realism_tips": evaluation_data.get("overall_realism", {}).get("improvement_tips", []),
            })

        table.put_item(Item=item)
        return item
    except Exception as e:
        st.error(f"Error inserting data: {str(e)}")
        return None


def get_all_artworks():
    """Return a list of all artworks, newest first, with nested evaluation_data."""
    table = init_dynamodb()
    try:
        items = []
        kwargs = {
            "IndexName": GSI_NAME,
            "KeyConditionExpression": Key("entity_type").eq(ENTITY_TYPE),
            "ScanIndexForward": False,
        }
        while True:
            resp = table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return [_structure_evaluation(_to_native(i)) for i in items]
    except Exception as e:
        st.error(f"Error querying data: {str(e)}")
        return []
