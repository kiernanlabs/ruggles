-- Add sketch_type field to artworks table
ALTER TABLE artworks
ADD COLUMN sketch_type VARCHAR(20) DEFAULT 'full realism'; 