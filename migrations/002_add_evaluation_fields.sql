-- Add evaluation fields to artworks table
ALTER TABLE artworks
ADD COLUMN proportion_score INTEGER DEFAULT 0,
ADD COLUMN proportion_rationale TEXT DEFAULT '',
ADD COLUMN proportion_tips TEXT[] DEFAULT '{}',
ADD COLUMN line_quality_score INTEGER DEFAULT 0,
ADD COLUMN line_quality_rationale TEXT DEFAULT '',
ADD COLUMN line_quality_tips TEXT[] DEFAULT '{}',
ADD COLUMN evaluation_version TEXT DEFAULT 'v0';

-- Update existing records to have standard evaluation message
UPDATE artworks
SET description = 'Standard evaluation v0',
    question = 'Standard evaluation v0'
WHERE description IS NULL OR question IS NULL; 