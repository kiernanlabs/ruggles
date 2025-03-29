import os
import cloudinary
import cloudinary.uploader
import cloudinary.api
from datetime import datetime
import streamlit as st
from io import BytesIO

# Configure Cloudinary
@st.cache_resource
def init_cloudinary():
    try:
        # Try to get credentials from Streamlit secrets
        cloud_name = st.secrets["CLOUDINARY_CLOUD_NAME"]
        api_key = st.secrets["CLOUDINARY_API_KEY"]
        api_secret = st.secrets["CLOUDINARY_API_SECRET"]
    except:
        # Fall back to environment variables
        cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME')
        api_key = os.getenv('CLOUDINARY_API_KEY')
        api_secret = os.getenv('CLOUDINARY_API_SECRET')
    
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret
    )

def upload_image(image_file, folder="artwork"):
    """
    Upload an image to Cloudinary and return the URL and public_id
    Can handle both file objects and bytes
    """
    init_cloudinary()
    
    try:
        # If image_file is bytes, create a BytesIO object
        if isinstance(image_file, bytes):
            image_file = BytesIO(image_file)
        
        # Upload the image
        upload_result = cloudinary.uploader.upload(
            image_file,
            folder=folder,
            resource_type="auto"  # Automatically detect if it's an image or video
        )
        
        return {
            "url": upload_result["secure_url"],
            "public_id": upload_result["public_id"],
            "format": upload_result["format"],
            "width": upload_result["width"],
            "height": upload_result["height"],
            "created_at": datetime.now().isoformat()
        }
    except Exception as e:
        st.error(f"Error uploading image: {str(e)}")
        return None

def delete_image(public_id):
    """
    Delete an image from Cloudinary
    """
    init_cloudinary()
    
    try:
        result = cloudinary.uploader.destroy(public_id)
        return result
    except Exception as e:
        st.error(f"Error deleting image: {str(e)}")
        return None

def get_image_url(public_id, transformation=None):
    """
    Get the URL for an image, optionally with transformations
    """
    init_cloudinary()
    
    try:
        if transformation:
            return cloudinary.CloudinaryImage(public_id).build_url(**transformation)
        return cloudinary.CloudinaryImage(public_id).build_url()
    except Exception as e:
        st.error(f"Error getting image URL: {str(e)}")
        return None 