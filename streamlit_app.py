import streamlit as st
from openai import OpenAI
from utils.image_handler import upload_image
from utils.db import insert_artwork, get_all_artworks
import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables (for local development)
load_dotenv()

# Show title and description
st.title("🎨 Artwork Analysis")
st.write(
    "Upload an artwork image and ask questions about it – GPT will analyze the image and answer your questions!"
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

    # Let the user upload an image
    uploaded_file = st.file_uploader(
        "Upload an artwork image", 
        type=["png", "jpg", "jpeg"],
        help="Upload an image of artwork to analyze"
    )

    # Get artist name
    artist_name = st.text_input(
        "Artist Name",
        placeholder="Enter the artist's name",
        disabled=not uploaded_file
    )

    # Ask the user for a question about the image
    question = st.text_area(
        "Ask a question about the artwork!",
        placeholder="What style is this artwork in? What emotions does it convey?",
        disabled=not uploaded_file,
    )

    if uploaded_file and question and artist_name:
        # Display the uploaded image
        st.image(uploaded_file, caption="Uploaded Artwork", use_column_width=True)

        if st.button("Analyze Artwork"):
            with st.spinner("Analyzing artwork and generating response..."):
                # Upload image to Cloudinary
                image_data = upload_image(uploaded_file)
                
                if image_data:
                    # Prepare the prompt for GPT
                    messages = [
                        {
                            "role": "system",
                            "content": "You are an expert art analyst. Analyze the artwork and answer the user's question thoughtfully and professionally."
                        },
                        {
                            "role": "user",
                            "content": f"Here's an artwork by {artist_name}. {question}"
                        }
                    ]

                    # Generate an answer using the OpenAI API
                    stream = client.chat.completions.create(
                        model="gpt-4-vision-preview",
                        messages=messages,
                        max_tokens=500,
                        stream=True,
                    )

                    # Stream the response
                    response_text = ""
                    response_container = st.empty()
                    for chunk in stream:
                        if chunk.choices[0].delta.content:
                            response_text += chunk.choices[0].delta.content
                            response_container.markdown(response_text)

                    # Store the data in the database
                    artwork_data = {
                        "title": uploaded_file.name,
                        "description": question,
                        "image_url": image_data["url"],
                        "image_public_id": image_data["public_id"],
                        "artist_name": artist_name,
                        "created_at": datetime.now().isoformat(),
                        "question": question,
                        "gpt_response": response_text
                    }
                    
                    result = insert_artwork(artwork_data)
                    if result:
                        st.success("Analysis saved successfully!")

    # Display previous analyses
    st.subheader("Previous Analyses")
    artworks = get_all_artworks()
    if artworks and artworks.data:
        for artwork in artworks.data:
            with st.expander(f"Artwork by {artwork['artist_name']} - {artwork['created_at']}"):
                st.image(artwork['image_url'], caption=artwork['title'])
                st.write("**Question:**", artwork['question'])
                st.write("**Analysis:**", artwork['gpt_response'])
