-- Add artwork_date field to artworks table
ALTER TABLE artworks
ADD COLUMN artwork_date DATE DEFAULT CURRENT_DATE; 