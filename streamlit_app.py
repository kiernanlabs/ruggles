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
import pandas as pd

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
Line Quality – Are the lines confident, controlled, and varied to define form, contour, or texture effectively?
Value & Light – Is there effective use of shading and light to create realistic depth, contrast, and form?
Detail & Texture – Are the textures believable and appropriate for the subject? Is the level of detail well-judged?
Composition & Perspective – Is the placement of elements balanced? Is perspective applied accurately?
Form & Volume – Does the drawing feel three-dimensional? Are forms convincingly modeled through shading or structure?
Mood & Expression – Does the image evoke a mood, emotion, or atmosphere, even subtly?
Overall Realism – How realistic is the overall sketch in terms of visual believability and execution?"""

                # Generate an answer using the OpenAI API
                response = client.responses.create(
                    model="gpt-4o-mini",
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
                                    },
                                    "value_and_light": {
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
                                    "detail_and_texture": {
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
                                    "composition_and_perspective": {
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
                                    "form_and_volume": {
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
                                    "mood_and_expression": {
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
                                    "overall_realism": {
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
                                "required": ["proportion_and_structure", "line_quality", "value_and_light", "detail_and_texture", "composition_and_perspective", "form_and_volume", "mood_and_expression", "overall_realism"],
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
                    
                    # Create a table for evaluation results
                    results_data = []
                    
                    # Add proportion and structure data
                    ps_data = evaluation_data['proportion_and_structure']
                    results_data.append({
                        "Criteria": "Proportion & Structure",
                        "Score": f"{ps_data['score']}/20",
                        "Rationale": ps_data['rationale']
                    })
                    
                    # Add line quality data
                    lq_data = evaluation_data['line_quality']
                    results_data.append({
                        "Criteria": "Line Quality",
                        "Score": f"{lq_data['score']}/20",
                        "Rationale": lq_data['rationale']
                    })
                    
                    # Add value and light data
                    vl_data = evaluation_data['value_and_light']
                    results_data.append({
                        "Criteria": "Value & Light",
                        "Score": f"{vl_data['score']}/20",
                        "Rationale": vl_data['rationale']
                    })
                    
                    # Add detail and texture data
                    dt_data = evaluation_data['detail_and_texture']
                    results_data.append({
                        "Criteria": "Detail & Texture",
                        "Score": f"{dt_data['score']}/20",
                        "Rationale": dt_data['rationale']
                    })
                    
                    # Add composition and perspective data
                    cp_data = evaluation_data['composition_and_perspective']
                    results_data.append({
                        "Criteria": "Composition & Perspective",
                        "Score": f"{cp_data['score']}/20",
                        "Rationale": cp_data['rationale']
                    })
                    
                    # Add form and volume data
                    fv_data = evaluation_data['form_and_volume']
                    results_data.append({
                        "Criteria": "Form & Volume",
                        "Score": f"{fv_data['score']}/20",
                        "Rationale": fv_data['rationale']
                    })
                    
                    # Add mood and expression data
                    me_data = evaluation_data['mood_and_expression']
                    results_data.append({
                        "Criteria": "Mood & Expression",
                        "Score": f"{me_data['score']}/20",
                        "Rationale": me_data['rationale']
                    })
                    
                    # Add overall realism data
                    or_data = evaluation_data['overall_realism']
                    results_data.append({
                        "Criteria": "Overall Realism",
                        "Score": f"{or_data['score']}/20",
                        "Rationale": or_data['rationale']
                    })
                    
                    # Calculate average score
                    total_score = (ps_data['score'] + lq_data['score'] + vl_data['score'] + dt_data['score'] + 
                                   cp_data['score'] + fv_data['score'] + me_data['score'] + or_data['score'])
                    avg_score = total_score / 8
                    
                    # Add average score row
                    results_data.append({
                        "Criteria": "Average Score",
                        "Score": f"{avg_score:.1f}/20",
                        "Rationale": "Average of all criteria scores"
                    })
                    
                    # Convert to DataFrame
                    df = pd.DataFrame(results_data)
                    
                    # Use pandas styling to generate a styled HTML table
                    styled_df = df.style.set_properties(**{
                        'text-align': 'left',
                        'border': '1px solid #ddd',
                        'padding': '8px'
                    }).set_table_styles([
                        {'selector': 'th', 'props': [('background-color', '#f2f2f2'), 
                                                    ('border', '1px solid #ddd'),
                                                    ('padding', '8px'),
                                                    ('text-align', 'left')]},
                        {'selector': 'tr:hover', 'props': [('background-color', '#f9f9f9')]}
                    ]).hide(axis="index")
                    
                    # Display the styled table
                    st.write("""
                    <style>
                    .styled-table {
                        width: 100%;
                        border-collapse: collapse;
                        margin: 25px 0;
                        font-size: 0.9em;
                        table-layout: fixed;
                    }
                    .styled-table thead tr {
                        background-color: #f2f2f2;
                        text-align: left;
                    }
                    .styled-table th,
                    .styled-table td {
                        padding: 12px 15px;
                        border: 1px solid #ddd;
                    }
                    .styled-table th:nth-child(1),
                    .styled-table td:nth-child(1) {
                        width: 100px;
                    }
                    .styled-table th:nth-child(2),
                    .styled-table td:nth-child(2) {
                        width: 50px;
                    }
                    .styled-table th:nth-child(3),
                    .styled-table td:nth-child(3) {
                        width: auto;
                    }
                    </style>
                    """, unsafe_allow_html=True)
                    st.write(styled_df.to_html(classes='styled-table'), unsafe_allow_html=True)
                    
                    # Display improvement tips without nested expanders
                    st.markdown("### Improvement Tips")
                    
                    # Use columns for the improvement tips
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.markdown("**Proportion & Structure:**")
                        for tip in ps_data['improvement_tips']:
                            st.markdown(f"- {tip}")
                            
                        st.markdown("**Value & Light:**")
                        for tip in vl_data['improvement_tips']:
                            st.markdown(f"- {tip}")
                            
                        st.markdown("**Composition & Perspective:**")
                        for tip in cp_data['improvement_tips']:
                            st.markdown(f"- {tip}")
                            
                        st.markdown("**Mood & Expression:**")
                        for tip in me_data['improvement_tips']:
                            st.markdown(f"- {tip}")
                    
                    with col2:
                        st.markdown("**Line Quality:**")
                        for tip in lq_data['improvement_tips']:
                            st.markdown(f"- {tip}")
                            
                        st.markdown("**Detail & Texture:**")
                        for tip in dt_data['improvement_tips']:
                            st.markdown(f"- {tip}")
                            
                        st.markdown("**Form & Volume:**")
                        for tip in fv_data['improvement_tips']:
                            st.markdown(f"- {tip}")
                            
                        st.markdown("**Overall Realism:**")
                        for tip in or_data['improvement_tips']:
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
            # Calculate average score for expander header if evaluation data exists
            avg_score_text = ""
            if 'evaluation_data' in artwork:
                evaluation_data = artwork['evaluation_data']
                
                # Calculate average score (handling both old and new format)
                scores = []
                if 'proportion_and_structure' in evaluation_data:
                    scores.append(evaluation_data['proportion_and_structure']['score'])
                if 'line_quality' in evaluation_data:
                    scores.append(evaluation_data['line_quality']['score'])
                if 'value_and_light' in evaluation_data:
                    scores.append(evaluation_data['value_and_light']['score'])
                if 'detail_and_texture' in evaluation_data:
                    scores.append(evaluation_data['detail_and_texture']['score'])
                if 'composition_and_perspective' in evaluation_data:
                    scores.append(evaluation_data['composition_and_perspective']['score'])
                if 'form_and_volume' in evaluation_data:
                    scores.append(evaluation_data['form_and_volume']['score'])
                if 'mood_and_expression' in evaluation_data:
                    scores.append(evaluation_data['mood_and_expression']['score'])
                if 'overall_realism' in evaluation_data:
                    scores.append(evaluation_data['overall_realism']['score'])
                
                if scores:
                    avg_score = sum(scores) / len(scores)
                    avg_score_text = f" - Avg Score: {avg_score:.1f}/20"
                
            # Format the date to show only YYYY-MM-DD
            created_date = artwork['created_at'].split('T')[0] if 'T' in artwork['created_at'] else artwork['created_at']
                
            with st.expander(f"Artwork by {artwork['artist_name']} - {created_date}{avg_score_text}"):
                st.image(artwork['image_url'], caption=artwork['title'], use_container_width=True)
                
                # Display evaluation data if available
                if 'evaluation_data' in artwork:
                    evaluation_data = artwork['evaluation_data']
                    
                    # Create a table for evaluation results
                    results_data = []
                    
                    # Add proportion and structure data
                    ps_data = evaluation_data['proportion_and_structure']
                    results_data.append({
                        "Criteria": "Proportion & Structure",
                        "Score": f"{ps_data['score']}/20",
                        "Rationale": ps_data['rationale']
                    })
                    
                    # Add line quality data
                    lq_data = evaluation_data['line_quality']
                    results_data.append({
                        "Criteria": "Line Quality",
                        "Score": f"{lq_data['score']}/20",
                        "Rationale": lq_data['rationale']
                    })
                    
                    # Add additional criteria if they exist in the evaluation data
                    if 'value_and_light' in evaluation_data:
                        vl_data = evaluation_data['value_and_light']
                        results_data.append({
                            "Criteria": "Value & Light",
                            "Score": f"{vl_data['score']}/20",
                            "Rationale": vl_data['rationale']
                        })
                    
                    if 'detail_and_texture' in evaluation_data:
                        dt_data = evaluation_data['detail_and_texture']
                        results_data.append({
                            "Criteria": "Detail & Texture",
                            "Score": f"{dt_data['score']}/20",
                            "Rationale": dt_data['rationale']
                        })
                    
                    if 'composition_and_perspective' in evaluation_data:
                        cp_data = evaluation_data['composition_and_perspective']
                        results_data.append({
                            "Criteria": "Composition & Perspective",
                            "Score": f"{cp_data['score']}/20",
                            "Rationale": cp_data['rationale']
                        })
                    
                    if 'form_and_volume' in evaluation_data:
                        fv_data = evaluation_data['form_and_volume']
                        results_data.append({
                            "Criteria": "Form & Volume",
                            "Score": f"{fv_data['score']}/20",
                            "Rationale": fv_data['rationale']
                        })
                    
                    if 'mood_and_expression' in evaluation_data:
                        me_data = evaluation_data['mood_and_expression']
                        results_data.append({
                            "Criteria": "Mood & Expression",
                            "Score": f"{me_data['score']}/20",
                            "Rationale": me_data['rationale']
                        })
                    
                    if 'overall_realism' in evaluation_data:
                        or_data = evaluation_data['overall_realism']
                        results_data.append({
                            "Criteria": "Overall Realism",
                            "Score": f"{or_data['score']}/20",
                            "Rationale": or_data['rationale']
                        })
                    
                    # Add average score row
                    results_data.append({
                        "Criteria": "Average Score",
                        "Score": f"{avg_score:.1f}/20",
                        "Rationale": "Average of all criteria scores"
                    })
                    
                    # Convert to DataFrame
                    df = pd.DataFrame(results_data)
                    
                    # Use pandas styling to generate a styled HTML table
                    styled_df = df.style.set_properties(**{
                        'text-align': 'left',
                        'border': '1px solid #ddd',
                        'padding': '8px'
                    }).set_table_styles([
                        {'selector': 'th', 'props': [('background-color', '#f2f2f2'), 
                                                    ('border', '1px solid #ddd'),
                                                    ('padding', '8px'),
                                                    ('text-align', 'left')]},
                        {'selector': 'tr:hover', 'props': [('background-color', '#f9f9f9')]}
                    ]).hide(axis="index")
                    
                    # Display the styled table
                    st.write("""
                    <style>
                    .styled-table {
                        width: 100%;
                        border-collapse: collapse;
                        margin: 25px 0;
                        font-size: 0.9em;
                        table-layout: fixed;
                    }
                    .styled-table thead tr {
                        background-color: #f2f2f2;
                        text-align: left;
                    }
                    .styled-table th,
                    .styled-table td {
                        padding: 12px 15px;
                        border: 1px solid #ddd;
                    }
                    .styled-table th:nth-child(1),
                    .styled-table td:nth-child(1) {
                        width: 100px;
                    }
                    .styled-table th:nth-child(2),
                    .styled-table td:nth-child(2) {
                        width: 50px;
                    }
                    .styled-table th:nth-child(3),
                    .styled-table td:nth-child(3) {
                        width: auto;
                    }
                    </style>
                    """, unsafe_allow_html=True)
                    st.write(styled_df.to_html(classes='styled-table'), unsafe_allow_html=True)
                    
                    # Display improvement tips without nested expanders
                    st.markdown("### Improvement Tips")
                    
                    # Use columns for the improvement tips
                    col1, col2 = st.columns(2)
                    
                    # Left column
                    with col1:
                        st.markdown("**Proportion & Structure:**")
                        for tip in ps_data['improvement_tips']:
                            st.markdown(f"- {tip}")
                            
                        if 'value_and_light' in evaluation_data:
                            st.markdown("**Value & Light:**")
                            for tip in evaluation_data['value_and_light']['improvement_tips']:
                                st.markdown(f"- {tip}")
                                
                        if 'composition_and_perspective' in evaluation_data:
                            st.markdown("**Composition & Perspective:**")
                            for tip in evaluation_data['composition_and_perspective']['improvement_tips']:
                                st.markdown(f"- {tip}")
                                
                        if 'mood_and_expression' in evaluation_data:
                            st.markdown("**Mood & Expression:**")
                            for tip in evaluation_data['mood_and_expression']['improvement_tips']:
                                st.markdown(f"- {tip}")
                    
                    # Right column            
                    with col2:
                        st.markdown("**Line Quality:**")
                        for tip in lq_data['improvement_tips']:
                            st.markdown(f"- {tip}")
                            
                        if 'detail_and_texture' in evaluation_data:
                            st.markdown("**Detail & Texture:**")
                            for tip in evaluation_data['detail_and_texture']['improvement_tips']:
                                st.markdown(f"- {tip}")
                                
                        if 'form_and_volume' in evaluation_data:
                            st.markdown("**Form & Volume:**")
                            for tip in evaluation_data['form_and_volume']['improvement_tips']:
                                st.markdown(f"- {tip}")
                                
                        if 'overall_realism' in evaluation_data:
                            st.markdown("**Overall Realism:**")
                            for tip in evaluation_data['overall_realism']['improvement_tips']:
                                st.markdown(f"- {tip}")
                else:
                    st.write("**Analysis:**", artwork['gpt_response'])
