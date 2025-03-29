-- Add new evaluation criteria fields to artworks table
ALTER TABLE artworks
ADD COLUMN value_light_score INTEGER DEFAULT 0,
ADD COLUMN value_light_rationale TEXT DEFAULT '',
ADD COLUMN value_light_tips TEXT[] DEFAULT '{}',

ADD COLUMN detail_texture_score INTEGER DEFAULT 0,
ADD COLUMN detail_texture_rationale TEXT DEFAULT '',
ADD COLUMN detail_texture_tips TEXT[] DEFAULT '{}',

ADD COLUMN composition_perspective_score INTEGER DEFAULT 0,
ADD COLUMN composition_perspective_rationale TEXT DEFAULT '',
ADD COLUMN composition_perspective_tips TEXT[] DEFAULT '{}',

ADD COLUMN form_volume_score INTEGER DEFAULT 0,
ADD COLUMN form_volume_rationale TEXT DEFAULT '',
ADD COLUMN form_volume_tips TEXT[] DEFAULT '{}',

ADD COLUMN mood_expression_score INTEGER DEFAULT 0,
ADD COLUMN mood_expression_rationale TEXT DEFAULT '',
ADD COLUMN mood_expression_tips TEXT[] DEFAULT '{}',

ADD COLUMN overall_realism_score INTEGER DEFAULT 0,
ADD COLUMN overall_realism_rationale TEXT DEFAULT '',
ADD COLUMN overall_realism_tips TEXT[] DEFAULT '{}';

-- Update the evaluation_version for existing records
UPDATE artworks
SET evaluation_version = 'v1'
WHERE evaluation_version = 'v0'; 