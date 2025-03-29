import os
from supabase import create_client, Client
import streamlit as st
from datetime import datetime
import json
from dotenv import load_dotenv

# Load environment variables (for local development)
load_dotenv()

# Initialize Supabase client
@st.cache_resource
def init_supabase() -> Client:
    try:
        # Try to get credentials from Streamlit secrets
        supabase_url = st.secrets["SUPABASE_URL"]
        supabase_key = st.secrets["SUPABASE_KEY"]
    except:
        # Fall back to environment variables
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        st.error("Missing Supabase credentials. Please set SUPABASE_URL and SUPABASE_KEY in your Streamlit secrets or environment variables.")
        return None
    
    return create_client(supabase_url, supabase_key)

# Example function to insert data
def insert_data(table_name: str, data: dict):
    supabase = init_supabase()
    if supabase:
        try:
            return supabase.table(table_name).insert(data).execute()
        except Exception as e:
            st.error(f"Error inserting data: {str(e)}")
            return None

# Example function to query data
def query_data(table_name: str, query: dict = None):
    supabase = init_supabase()
    if supabase:
        try:
            if query:
                return supabase.table(table_name).select("*").match(query).execute()
            return supabase.table(table_name).select("*").execute()
        except Exception as e:
            st.error(f"Error querying data: {str(e)}")
            return None

# Artwork-specific functions
def insert_artwork(artwork_data: dict):
    """
    Insert artwork metadata and evaluation into the database
    artwork_data should include:
    - title: str
    - description: str
    - image_url: str
    - image_public_id: str
    - artist_name: str
    - created_at: str (ISO format)
    - sketch_type: str ("quick sketch" or "full realism")
    - evaluation_data: dict containing:
        - proportion_and_structure: dict with score, rationale, improvement_tips
        - line_quality: dict with score, rationale, improvement_tips
        - value_and_light: dict with score, rationale, improvement_tips (only for full realism)
        - detail_and_texture: dict with score, rationale, improvement_tips (only for full realism)
        - composition_and_perspective: dict with score, rationale, improvement_tips (only for full realism)
        - form_and_volume: dict with score, rationale, improvement_tips
        - mood_and_expression: dict with score, rationale, improvement_tips
        - overall_realism: dict with score, rationale, improvement_tips (only for full realism)
    """
    supabase = init_supabase()
    if supabase:
        try:
            # Extract evaluation data
            evaluation_data = artwork_data.pop('evaluation_data', {})
            
            # Save the raw gpt_response before removing it
            gpt_response = artwork_data.get('gpt_response', '')
            
            # Extract sketch type, ensuring it has a valid value
            sketch_type = artwork_data.get('sketch_type', 'full realism')
            # Validate sketch type value
            if sketch_type not in ['quick sketch', 'full realism']:
                sketch_type = 'full realism'  # Default if invalid value
                
            st.write(f"Debug - Inserting with sketch type: {sketch_type}")
            
            # Prepare the data for insertion
            data = {
                "title": artwork_data.get('title', ''),
                "description": artwork_data.get('description', ''),
                "image_url": artwork_data.get('image_url', ''),
                "image_public_id": artwork_data.get('image_public_id', ''),
                "artist_name": artwork_data.get('artist_name', ''),
                "created_at": artwork_data.get('created_at', datetime.now().isoformat()),
                "artwork_date": artwork_data.get('artwork_date', datetime.now().strftime('%Y-%m-%d')),
                "sketch_type": sketch_type,  # Use validated sketch type
                "question": artwork_data.get('question', ''),
                "gpt_response": gpt_response,
                
                # Original evaluation criteria
                'proportion_score': evaluation_data.get('proportion_and_structure', {}).get('score', 0),
                'proportion_rationale': evaluation_data.get('proportion_and_structure', {}).get('rationale', ''),
                'proportion_tips': evaluation_data.get('proportion_and_structure', {}).get('improvement_tips', []),
                'line_quality_score': evaluation_data.get('line_quality', {}).get('score', 0),
                'line_quality_rationale': evaluation_data.get('line_quality', {}).get('rationale', ''),
                'line_quality_tips': evaluation_data.get('line_quality', {}).get('improvement_tips', [])
            }
            
            # Only add full realism criteria values if this is a full realism evaluation
            if sketch_type == 'full realism':
                data.update({
                    # New evaluation criteria (only for full realism)
                    'value_light_score': evaluation_data.get('value_and_light', {}).get('score', 0),
                    'value_light_rationale': evaluation_data.get('value_and_light', {}).get('rationale', ''),
                    'value_light_tips': evaluation_data.get('value_and_light', {}).get('improvement_tips', []),
                    
                    'detail_texture_score': evaluation_data.get('detail_and_texture', {}).get('score', 0),
                    'detail_texture_rationale': evaluation_data.get('detail_and_texture', {}).get('rationale', ''),
                    'detail_texture_tips': evaluation_data.get('detail_and_texture', {}).get('improvement_tips', []),
                    
                    'composition_perspective_score': evaluation_data.get('composition_and_perspective', {}).get('score', 0),
                    'composition_perspective_rationale': evaluation_data.get('composition_and_perspective', {}).get('rationale', ''),
                    'composition_perspective_tips': evaluation_data.get('composition_and_perspective', {}).get('improvement_tips', []),
                    
                    'overall_realism_score': evaluation_data.get('overall_realism', {}).get('score', 0),
                    'overall_realism_rationale': evaluation_data.get('overall_realism', {}).get('rationale', ''),
                    'overall_realism_tips': evaluation_data.get('overall_realism', {}).get('improvement_tips', []),
                })
            
            # These common fields are always included for both quick sketch and full realism
            data.update({
                'form_volume_score': evaluation_data.get('form_and_volume', {}).get('score', 0),
                'form_volume_rationale': evaluation_data.get('form_and_volume', {}).get('rationale', ''),
                'form_volume_tips': evaluation_data.get('form_and_volume', {}).get('improvement_tips', []),
                
                'mood_expression_score': evaluation_data.get('mood_and_expression', {}).get('score', 0),
                'mood_expression_rationale': evaluation_data.get('mood_and_expression', {}).get('rationale', ''),
                'mood_expression_tips': evaluation_data.get('mood_and_expression', {}).get('improvement_tips', []),
                
                'evaluation_version': 'v1'
            })
            
            return supabase.table("artworks").insert(data).execute()
        except Exception as e:
            st.error(f"Error inserting data: {str(e)}")
            return None

def get_artwork_by_id(artwork_id: str):
    """
    Get artwork by ID with its evaluation
    """
    supabase = init_supabase()
    if supabase:
        try:
            result = supabase.table("artworks").select("*").eq('id', artwork_id).execute()
            
            # Transform the data to include structured evaluation
            if result and result.data:
                artwork = result.data[0]
                artwork['evaluation_data'] = {
                    'proportion_and_structure': {
                        'score': artwork.pop('proportion_score', 0),
                        'rationale': artwork.pop('proportion_rationale', ''),
                        'improvement_tips': artwork.pop('proportion_tips', [])
                    },
                    'line_quality': {
                        'score': artwork.pop('line_quality_score', 0),
                        'rationale': artwork.pop('line_quality_rationale', ''),
                        'improvement_tips': artwork.pop('line_quality_tips', [])
                    }
                }
                
                # Add new evaluation criteria if they exist in the database
                if 'value_light_score' in artwork:
                    artwork['evaluation_data']['value_and_light'] = {
                        'score': artwork.pop('value_light_score', 0),
                        'rationale': artwork.pop('value_light_rationale', ''),
                        'improvement_tips': artwork.pop('value_light_tips', [])
                    }
                
                if 'detail_texture_score' in artwork:
                    artwork['evaluation_data']['detail_and_texture'] = {
                        'score': artwork.pop('detail_texture_score', 0),
                        'rationale': artwork.pop('detail_texture_rationale', ''),
                        'improvement_tips': artwork.pop('detail_texture_tips', [])
                    }
                
                if 'composition_perspective_score' in artwork:
                    artwork['evaluation_data']['composition_and_perspective'] = {
                        'score': artwork.pop('composition_perspective_score', 0),
                        'rationale': artwork.pop('composition_perspective_rationale', ''),
                        'improvement_tips': artwork.pop('composition_perspective_tips', [])
                    }
                
                if 'form_volume_score' in artwork:
                    artwork['evaluation_data']['form_and_volume'] = {
                        'score': artwork.pop('form_volume_score', 0),
                        'rationale': artwork.pop('form_volume_rationale', ''),
                        'improvement_tips': artwork.pop('form_volume_tips', [])
                    }
                
                if 'mood_expression_score' in artwork:
                    artwork['evaluation_data']['mood_and_expression'] = {
                        'score': artwork.pop('mood_expression_score', 0),
                        'rationale': artwork.pop('mood_expression_rationale', ''),
                        'improvement_tips': artwork.pop('mood_expression_tips', [])
                    }
                
                if 'overall_realism_score' in artwork:
                    artwork['evaluation_data']['overall_realism'] = {
                        'score': artwork.pop('overall_realism_score', 0),
                        'rationale': artwork.pop('overall_realism_rationale', ''),
                        'improvement_tips': artwork.pop('overall_realism_tips', [])
                    }
                
            return result
        except Exception as e:
            st.error(f"Error querying data: {str(e)}")
            return None

def get_all_artworks():
    """
    Get all artworks with their evaluations
    """
    supabase = init_supabase()
    if supabase:
        try:
            result = supabase.table("artworks").select("*").order('created_at', desc=True).execute()
            
            # Transform the data to include structured evaluation
            if result and result.data:
                for artwork in result.data:
                    artwork['evaluation_data'] = {
                        'proportion_and_structure': {
                            'score': artwork.pop('proportion_score', 0),
                            'rationale': artwork.pop('proportion_rationale', ''),
                            'improvement_tips': artwork.pop('proportion_tips', [])
                        },
                        'line_quality': {
                            'score': artwork.pop('line_quality_score', 0),
                            'rationale': artwork.pop('line_quality_rationale', ''),
                            'improvement_tips': artwork.pop('line_quality_tips', [])
                        }
                    }
                    
                    # Add new evaluation criteria if they exist in the database
                    if 'value_light_score' in artwork:
                        artwork['evaluation_data']['value_and_light'] = {
                            'score': artwork.pop('value_light_score', 0),
                            'rationale': artwork.pop('value_light_rationale', ''),
                            'improvement_tips': artwork.pop('value_light_tips', [])
                        }
                    
                    if 'detail_texture_score' in artwork:
                        artwork['evaluation_data']['detail_and_texture'] = {
                            'score': artwork.pop('detail_texture_score', 0),
                            'rationale': artwork.pop('detail_texture_rationale', ''),
                            'improvement_tips': artwork.pop('detail_texture_tips', [])
                        }
                    
                    if 'composition_perspective_score' in artwork:
                        artwork['evaluation_data']['composition_and_perspective'] = {
                            'score': artwork.pop('composition_perspective_score', 0),
                            'rationale': artwork.pop('composition_perspective_rationale', ''),
                            'improvement_tips': artwork.pop('composition_perspective_tips', [])
                        }
                    
                    if 'form_volume_score' in artwork:
                        artwork['evaluation_data']['form_and_volume'] = {
                            'score': artwork.pop('form_volume_score', 0),
                            'rationale': artwork.pop('form_volume_rationale', ''),
                            'improvement_tips': artwork.pop('form_volume_tips', [])
                        }
                    
                    if 'mood_expression_score' in artwork:
                        artwork['evaluation_data']['mood_and_expression'] = {
                            'score': artwork.pop('mood_expression_score', 0),
                            'rationale': artwork.pop('mood_expression_rationale', ''),
                            'improvement_tips': artwork.pop('mood_expression_tips', [])
                        }
                    
                    if 'overall_realism_score' in artwork:
                        artwork['evaluation_data']['overall_realism'] = {
                            'score': artwork.pop('overall_realism_score', 0),
                            'rationale': artwork.pop('overall_realism_rationale', ''),
                            'improvement_tips': artwork.pop('overall_realism_tips', [])
                        }
            
            return result
        except Exception as e:
            st.error(f"Error querying data: {str(e)}")
            return None

def search_artworks(query: str):
    """
    Search artworks by title or description
    """
    supabase = init_supabase()
    if supabase:
        try:
            return supabase.table("artworks").select("*").or_(
                f"title.ilike.%{query}%,description.ilike.%{query}%"
            ).execute()
        except Exception as e:
            st.error(f"Error searching artworks: {str(e)}")
            return None 