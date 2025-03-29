import streamlit as st
from openai import OpenAI
from utils.image_handler import upload_image
from utils.db import insert_artwork, get_all_artworks
import os
from datetime import datetime
from dotenv import load_dotenv
import base64
from io import BytesIO

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

    # Display the uploaded image if available
    if uploaded_file:
        st.image(uploaded_file, caption="Uploaded Artwork", width=400)

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

    # Analyze Artwork button (disabled until all fields are filled)
    if st.button("Analyze Artwork", disabled=not (uploaded_file and question and artist_name)):
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
                messages = [
                    {
                        "role": "system",
                        "content": "You are an expert art analyst. Analyze the artwork and answer the user's question thoughtfully and professionally."
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": f"Here's an artwork by {artist_name}. {question}"
                            },
                            {
                                "type": "input_image",
                                "image_url": f"data:image/{file_extension};base64,{base64_image}"
                            }
                        ]
                    }
                ]

                # Generate an answer using the OpenAI API
                response = client.responses.create(
                    model="gpt-4o",
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": f"Here's an artwork by {artist_name}. {question}"
                                },
                                {
                                    "type": "input_image",
                                    "image_url": f"data:image/{file_extension};base64,{base64_image}"
                                }
                            ]
                        }
                    ]
                )

                # Display the response
                response_text = response.output_text
                st.markdown(response_text)

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
                st.image(artwork['image_url'], caption=artwork['title'], use_container_width=True)
                st.write("**Question:**", artwork['question'])
                st.write("**Analysis:**", artwork['gpt_response'])
