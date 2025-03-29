import streamlit as st
from openai import OpenAI
from utils.image_handler import upload_image
from utils.db import insert_artwork, get_all_artworks
import os
from datetime import datetime
from dotenv import load_dotenv
import base64
from io import BytesIO
import json

# Load environment variables (for local development)
load_dotenv()

# Show title and description
st.title("🎨 Artwork Analysis")
st.write(
    "Upload an artwork image for evaluation!"
)

# Get OpenAI API key from Streamlit secrets or environment
try:
    openai_api_key = st.secrets["OPENAI_API_KEY"]
except:
    openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    st.error("OpenAI API key not found. Please set OPENAI_API_KEY in your Streamlit secrets or .env file.")
else:
    # Create an OpenAI client
    client = OpenAI(api_key=openai_api_key)

    # Get artist name first
    artist_name = st.text_input(
        "Artist Name",
        placeholder="Enter the artist's name"
    )

    # Let the user upload an image
    uploaded_file = st.file_uploader(
        "Upload an artwork image", 
        type=["png", "jpg", "jpeg"],
        help="Upload an image of artwork to analyze"
    )

    # Display the uploaded image if available
    if uploaded_file:
        st.image(uploaded_file, caption="Uploaded Artwork", use_container_width=True)

    # Analyze Artwork button (disabled until all fields are filled)
    if st.button("Analyze Artwork", disabled=not (uploaded_file and artist_name)):
        with st.spinner("Analyzing artwork and generating response..."):
            # Read the file once
            image_bytes = uploaded_file.read()
            
            # Upload image to Cloudinary using the bytes
            image_data = upload_image(image_bytes)
            
            if image_data:
                # Convert the bytes to base64
                base64_image = base64.b64encode(image_bytes).decode('utf-8')
                
                # Get the file extension
                file_extension = uploaded_file.type.split('/')[-1]
                
                # Prepare the prompt for GPT
                system_prompt = """You are an expert art critic and instructor. Evaluate the provided sketch using the following criteria, scoring each one on a scale of 1 to 20 (1 = Poor, 20 = Excellent). For each category, include:
A 1–3 sentence rationale explaining the score.
A set of 1–3 actionable tips for how the artist could improve in that specific area.

Evaluation Criteria:
Proportion & Structure – Are the relative sizes and shapes of elements accurate and well-constructed?
Line Quality – Are the lines confident, controlled, and varied to define form, contour, or texture effectively?"""

                # Generate an answer using the OpenAI API
                response = client.responses.create(
                    model="gpt-4o",
                    input=[
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": f"Here's an artwork by {artist_name}."
                                },
                                {
                                    "type": "input_image",
                                    "image_url": f"data:image/{file_extension};base64,{base64_image}"
                                }
                            ]
                        }
                    ],
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "artwork_evaluation",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "proportion_and_structure": {
                                        "type": "object",
                                        "properties": {
                                            "score": {
                                                "type": "integer",
                                                "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                                            },
                                            "rationale": {
                                                "type": "string",
                                                "description": "1-3 sentence explanation for the score"
                                            },
                                            "improvement_tips": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                },
                                                "description": "1-3 actionable tips for improvement"
                                            }
                                        },
                                        "required": ["score", "rationale", "improvement_tips"],
                                        "additionalProperties": False
                                    },
                                    "line_quality": {
                                        "type": "object",
                                        "properties": {
                                            "score": {
                                                "type": "integer",
                                                "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                                            },
                                            "rationale": {
                                                "type": "string",
                                                "description": "1-3 sentence explanation for the score"
                                            },
                                            "improvement_tips": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                },
                                                "description": "1-3 actionable tips for improvement"
                                            }
                                        },
                                        "required": ["score", "rationale", "improvement_tips"],
                                        "additionalProperties": False
                                    }
                                },
                                "required": ["proportion_and_structure", "line_quality"],
                                "additionalProperties": False
                            },
                            "strict": True
                        }
                    }
                )

                # Parse the response
                try:
                    evaluation_data = json.loads(response.output_text)
                    
                    # Display the evaluation results
                    st.subheader("Artwork Evaluation")
                    
                    # Display Proportion & Structure
                    st.markdown("### Proportion & Structure")
                    st.markdown(f"**Score:** {evaluation_data['proportion_and_structure']['score']}/20")
                    st.markdown(f"**Rationale:** {evaluation_data['proportion_and_structure']['rationale']}")
                    st.markdown("**Improvement Tips:**")
                    for tip in evaluation_data['proportion_and_structure']['improvement_tips']:
                        st.markdown(f"- {tip}")
                    
                    # Display Line Quality
                    st.markdown("### Line Quality")
                    st.markdown(f"**Score:** {evaluation_data['line_quality']['score']}/20")
                    st.markdown(f"**Rationale:** {evaluation_data['line_quality']['rationale']}")
                    st.markdown("**Improvement Tips:**")
                    for tip in evaluation_data['line_quality']['improvement_tips']:
                        st.markdown(f"- {tip}")
                    
                    # Store the data in the database
                    artwork_data = {
                        "title": uploaded_file.name,
                        "description": "Standard evaluation v0",
                        "image_url": image_data["url"],
                        "image_public_id": image_data["public_id"],
                        "artist_name": artist_name,
                        "created_at": datetime.now().isoformat(),
                        "question": "Standard evaluation v0",
                        "gpt_response": response.output_text,
                        "evaluation_data": evaluation_data
                    }
                    
                    result = insert_artwork(artwork_data)
                    if result:
                        st.success("Analysis saved successfully!")
                except json.JSONDecodeError:
                    st.error("Error parsing the evaluation response. Please try again.")
                    st.markdown(response.output_text)

    # Display previous analyses
    st.subheader("Previous Analyses")
    artworks = get_all_artworks()
    if artworks and artworks.data:
        for artwork in artworks.data:
            with st.expander(f"Artwork by {artwork['artist_name']} - {artwork['created_at']}"):
                st.image(artwork['image_url'], caption=artwork['title'], use_container_width=True)
                
                # Display evaluation data if available
                if 'evaluation_data' in artwork:
                    evaluation_data = artwork['evaluation_data']
                    
                    st.markdown("### Proportion & Structure")
                    st.markdown(f"**Score:** {evaluation_data['proportion_and_structure']['score']}/20")
                    st.markdown(f"**Rationale:** {evaluation_data['proportion_and_structure']['rationale']}")
                    st.markdown("**Improvement Tips:**")
                    for tip in evaluation_data['proportion_and_structure']['improvement_tips']:
                        st.markdown(f"- {tip}")
                    
                    st.markdown("### Line Quality")
                    st.markdown(f"**Score:** {evaluation_data['line_quality']['score']}/20")
                    st.markdown(f"**Rationale:** {evaluation_data['line_quality']['rationale']}")
                    st.markdown("**Improvement Tips:**")
                    for tip in evaluation_data['line_quality']['improvement_tips']:
                        st.markdown(f"- {tip}")
                else:
                    st.write("**Analysis:**", artwork['gpt_response'])
