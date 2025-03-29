-- Create artworks table
CREATE TABLE IF NOT EXISTS artworks (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    image_url TEXT NOT NULL,
    image_public_id TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    question TEXT,
    gpt_response TEXT,
    created_at_utc TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index on created_at for faster sorting
CREATE INDEX IF NOT EXISTS idx_artworks_created_at ON artworks(created_at);

-- Create index on artist_name for faster searching
CREATE INDEX IF NOT EXISTS idx_artworks_artist_name ON artworks(artist_name); 