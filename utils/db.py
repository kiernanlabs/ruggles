import os
from supabase import create_client, Client
import streamlit as st
from datetime import datetime

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
    - evaluation_data: dict containing:
        - proportion_and_structure: dict with score, rationale, improvement_tips
        - line_quality: dict with score, rationale, improvement_tips
    """
    supabase = init_supabase()
    if supabase:
        try:
            # Extract evaluation data
            evaluation_data = artwork_data.pop('evaluation_data', {})
            
            # Save the raw gpt_response before removing it
            gpt_response = artwork_data.get('gpt_response', '')
            
            # Prepare the data for insertion
            data = {
                "title": artwork_data.get('title', ''),
                "description": artwork_data.get('description', ''),
                "image_url": artwork_data.get('image_url', ''),
                "image_public_id": artwork_data.get('image_public_id', ''),
                "artist_name": artwork_data.get('artist_name', ''),
                "created_at": artwork_data.get('created_at', datetime.now().isoformat()),
                "question": artwork_data.get('question', ''),
                "gpt_response": gpt_response,
                'proportion_score': evaluation_data.get('proportion_and_structure', {}).get('score', 0),
                'proportion_rationale': evaluation_data.get('proportion_and_structure', {}).get('rationale', ''),
                'proportion_tips': evaluation_data.get('proportion_and_structure', {}).get('improvement_tips', []),
                'line_quality_score': evaluation_data.get('line_quality', {}).get('score', 0),
                'line_quality_rationale': evaluation_data.get('line_quality', {}).get('rationale', ''),
                'line_quality_tips': evaluation_data.get('line_quality', {}).get('improvement_tips', []),
                'evaluation_version': 'v0'
            }
            
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