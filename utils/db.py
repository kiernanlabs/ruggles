import os
from supabase import create_client, Client
import streamlit as st

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
    Insert artwork metadata into the database
    artwork_data should include:
    - title
    - description
    - image_url
    - image_public_id
    - artist_name
    - created_at
    - tags (optional)
    """
    return insert_data("artworks", artwork_data)

def get_artwork_by_id(artwork_id: str):
    """
    Get artwork by ID
    """
    return query_data("artworks", {"id": artwork_id})

def get_all_artworks():
    """
    Get all artworks
    """
    return query_data("artworks")

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