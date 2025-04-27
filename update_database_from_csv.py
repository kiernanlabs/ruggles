import os
import csv
import json
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

def init_supabase() -> Client:
    """Initialize Supabase client with credentials from environment variables"""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        print("Error: Missing Supabase credentials. Please set SUPABASE_URL and SUPABASE_KEY in .env file.")
        return None
    
    return create_client(supabase_url, supabase_key)

def update_artwork_from_csv(csv_path="evaluation_results.csv"):
    """
    Update artwork records in Supabase with data from evaluation_results.csv
    
    This specifically updates:
    - title to generated_title
    - description to "o3 re-eval"
    - All _score, _rationale, and _tips columns with new values from CSV
    """
    # Initialize Supabase client
    supabase = init_supabase()
    if not supabase:
        return
    
    # Read CSV file
    updated_count = 0
    try:
        with open(csv_path, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            
            for row in reader:
                # Extract UUID for lookup
                artwork_id = row.get('id')
                if not artwork_id:
                    print(f"Warning: Row missing ID, skipping")
                    continue
                
                # Create update dict with the specified fields
                update_data = {
                    "title": row.get('generated_title', ''),
                    "description": "o3 re-eval",
                    # Keep these fields the same
                    # "image_url": row.get('image_url'),
                    # "image_public_id": (not in CSV),
                    # "artist_name": row.get('artist_name'),
                    # "created_at": row.get('created_at'),
                    # "question": (not in CSV),
                    # "gpt_response": (not in CSV),
                }
                
                # Update the scores, rationales, and tips for each criterion
                
                # Proportion and structure
                if 'new_proportion_and_structure_score' in row:
                    update_data['proportion_score'] = int(float(row['new_proportion_and_structure_score']))
                    update_data['proportion_rationale'] = row.get('new_proportion_and_structure_rationale', '')
                    # Convert string list to actual list if needed
                    tips = row.get('new_proportion_and_structure_tips', '')
                    update_data['proportion_tips'] = parse_tips(tips)
                
                # Line quality
                if 'new_line_quality_score' in row:
                    update_data['line_quality_score'] = int(float(row['new_line_quality_score']))
                    update_data['line_quality_rationale'] = row.get('new_line_quality_rationale', '')
                    tips = row.get('new_line_quality_tips', '')
                    update_data['line_quality_tips'] = parse_tips(tips)
                
                # Form and volume
                if 'new_form_and_volume_score' in row:
                    update_data['form_volume_score'] = int(float(row['new_form_and_volume_score']))
                    update_data['form_volume_rationale'] = row.get('new_form_and_volume_rationale', '')
                    tips = row.get('new_form_and_volume_tips', '')
                    update_data['form_volume_tips'] = parse_tips(tips)
                
                # Mood and expression
                if 'new_mood_and_expression_score' in row:
                    update_data['mood_expression_score'] = int(float(row['new_mood_and_expression_score']))
                    update_data['mood_expression_rationale'] = row.get('new_mood_and_expression_rationale', '')
                    tips = row.get('new_mood_and_expression_tips', '')
                    update_data['mood_expression_tips'] = parse_tips(tips)
                
                # Additional criteria for full realism evaluations
                
                # Value and light
                if 'new_value_and_light_score' in row:
                    update_data['value_light_score'] = int(float(row['new_value_and_light_score']))
                    update_data['value_light_rationale'] = row.get('new_value_and_light_rationale', '')
                    tips = row.get('new_value_and_light_tips', '')
                    update_data['value_light_tips'] = parse_tips(tips)
                
                # Detail and texture
                if 'new_detail_and_texture_score' in row:
                    update_data['detail_texture_score'] = int(float(row['new_detail_and_texture_score']))
                    update_data['detail_texture_rationale'] = row.get('new_detail_and_texture_rationale', '')
                    tips = row.get('new_detail_and_texture_tips', '')
                    update_data['detail_texture_tips'] = parse_tips(tips)
                
                # Composition and perspective
                if 'new_composition_and_perspective_score' in row:
                    update_data['composition_perspective_score'] = int(float(row['new_composition_and_perspective_score']))
                    update_data['composition_perspective_rationale'] = row.get('new_composition_and_perspective_rationale', '')
                    tips = row.get('new_composition_and_perspective_tips', '')
                    update_data['composition_perspective_tips'] = parse_tips(tips)
                
                # Overall realism
                if 'new_overall_realism_score' in row:
                    update_data['overall_realism_score'] = int(float(row['new_overall_realism_score']))
                    update_data['overall_realism_rationale'] = row.get('new_overall_realism_rationale', '')
                    tips = row.get('new_overall_realism_tips', '')
                    update_data['overall_realism_tips'] = parse_tips(tips)
                
                # Update the database record
                try:
                    result = supabase.table("artworks").update(update_data).eq("id", artwork_id).execute()
                    
                    if result and hasattr(result, 'data') and len(result.data) > 0:
                        updated_count += 1
                        print(f"Updated artwork: {artwork_id} - '{row.get('generated_title', '')}'")
                    else:
                        print(f"Failed to update artwork: {artwork_id} - No record found or no changes made")
                        
                except Exception as e:
                    print(f"Error updating artwork {artwork_id}: {str(e)}")
    
    except Exception as e:
        print(f"Error processing CSV file: {str(e)}")
        return
    
    print(f"\nSummary: Updated {updated_count} artwork records in the database.")

def parse_tips(tips_string):
    """
    Parse the tips string from CSV into a proper list
    The tips in the CSV are formatted like "Tip1.; Tip2.; Tip3."
    """
    if not tips_string:
        return []
    
    # Split by semicolons and clean up each tip
    tips = [tip.strip() for tip in tips_string.split(';')]
    # Remove empty tips and trailing periods
    tips = [tip[:-1] if tip.endswith('.') else tip for tip in tips if tip]
    
    return tips

if __name__ == "__main__":
    print("Starting database update from evaluation results CSV...")
    update_artwork_from_csv()
    print("Database update completed.")
