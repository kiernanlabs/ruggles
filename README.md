# 🎨 Artwork Analysis App

A Streamlit application that uses GPT-4 Vision to analyze artwork and answer questions about it. The app allows users to upload artwork images, ask questions, and get AI-powered analysis while storing the results in a database.

## Features

- **Image Upload**: Support for PNG, JPG, and JPEG formats
- **AI Analysis**: Uses GPT-4 to analyze artwork and answer questions
- **Database Storage**: Stores artwork metadata, questions, and AI responses
- **History View**: Displays previous analyses with expandable details
- **Cloud Storage**: Images are stored securely in Cloudinary
- **Real-time Updates**: Streams AI responses as they're generated

## Setup

1. Clone the repository:
```bash
git clone https://github.com/kiernanlabs/ruggles.git
cd ruggles
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables in `.env`:
```
OPENAI_API_KEY=your_openai_key
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_cloudinary_key
CLOUDINARY_API_SECRET=your_cloudinary_secret
```

4. Set up the database in Supabase:
```sql
create table artworks (
    id uuid default uuid_generate_v4() primary key,
    title text not null,
    description text,
    image_url text not null,
    image_public_id text not null,
    artist_name text not null,
    created_at timestamp with time zone default timezone('utc'::text, now()),
    question text not null,
    gpt_response text not null,
    tags text[]
);
```

## Running the App

```bash
streamlit run streamlit_app.py
```

## Usage

1. Upload an artwork image
2. Enter the artist's name
3. Ask a question about the artwork
4. Click "Analyze Artwork" to get AI-powered analysis
5. View previous analyses in the expandable sections below

## Deployment

The app is configured for deployment on Streamlit Community Cloud. Add the following secrets in your Streamlit deployment settings:

```toml
OPENAI_API_KEY = "your-openai-key"
SUPABASE_URL = "your-supabase-url"
SUPABASE_KEY = "your-supabase-key"
CLOUDINARY_CLOUD_NAME = "your-cloud-name"
CLOUDINARY_API_KEY = "your-cloudinary-key"
CLOUDINARY_API_SECRET = "your-cloudinary-secret"
```

## Technologies Used

- Streamlit
- OpenAI GPT-4 Vision
- Supabase (PostgreSQL)
- Cloudinary
- Python 3.11+

## License

Apache-2.0 license
