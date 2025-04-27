"""
Image Evaluation Script

This script retrieves all artwork images from the database, evaluates them using 
an AI model, and outputs the results to a CSV file.

Configuration options allow for adjusting the AI model and prompt used for evaluation.
"""

import os
import csv
import time
import json
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client, Client
from datetime import datetime
import cloudinary
import cloudinary.uploader
import cloudinary.api
from io import BytesIO

# Load environment variables
load_dotenv()

# Custom implementation of Cloudinary functions without Streamlit dependencies
def init_cloudinary():
    """Initialize Cloudinary without using Streamlit cache"""
    cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME')
    api_key = os.getenv('CLOUDINARY_API_KEY')
    api_secret = os.getenv('CLOUDINARY_API_SECRET')
    
    if not cloud_name or not api_key or not api_secret:
        print("Error: Missing Cloudinary credentials. Please set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET in your .env file.")
        return None
    
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret
    )

def get_image_url(public_id, transformation=None):
    """Get the URL for an image, optionally with transformations"""
    init_cloudinary()
    
    try:
        if transformation:
            return cloudinary.CloudinaryImage(public_id).build_url(**transformation)
        return cloudinary.CloudinaryImage(public_id).build_url()
    except Exception as e:
        print(f"Error getting image URL: {str(e)}")
        return None

# Custom implementation of database functions to avoid Streamlit dependencies
def init_supabase() -> Client:
    """Initialize Supabase client without using Streamlit cache"""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        print("Error: Missing Supabase credentials. Please set SUPABASE_URL and SUPABASE_KEY in your .env file.")
        return None
    
    return create_client(supabase_url, supabase_key)

def get_all_artworks():
    """Get all artworks with their evaluations without using Streamlit"""
    supabase = init_supabase()
    if supabase:
        try:
            result = supabase.table("artworks").select("*").order('created_at', desc=True).execute()
            
            # Transform the data to include structured evaluation
            if result and result.data:
                for artwork in result.data:
                    artwork['evaluation_data'] = {
                        'proportion_and_structure': {
                            'score': artwork.pop('proportion_score', 0),
                            'rationale': artwork.pop('proportion_rationale', ''),
                            'improvement_tips': artwork.pop('proportion_tips', [])
                        },
                        'line_quality': {
                            'score': artwork.pop('line_quality_score', 0),
                            'rationale': artwork.pop('line_quality_rationale', ''),
                            'improvement_tips': artwork.pop('line_quality_tips', [])
                        }
                    }
                    
                    # Add new evaluation criteria if they exist in the database
                    if 'value_light_score' in artwork:
                        artwork['evaluation_data']['value_and_light'] = {
                            'score': artwork.pop('value_light_score', 0),
                            'rationale': artwork.pop('value_light_rationale', ''),
                            'improvement_tips': artwork.pop('value_light_tips', [])
                        }
                    
                    if 'detail_texture_score' in artwork:
                        artwork['evaluation_data']['detail_and_texture'] = {
                            'score': artwork.pop('detail_texture_score', 0),
                            'rationale': artwork.pop('detail_texture_rationale', ''),
                            'improvement_tips': artwork.pop('detail_texture_tips', [])
                        }
                    
                    if 'composition_perspective_score' in artwork:
                        artwork['evaluation_data']['composition_and_perspective'] = {
                            'score': artwork.pop('composition_perspective_score', 0),
                            'rationale': artwork.pop('composition_perspective_rationale', ''),
                            'improvement_tips': artwork.pop('composition_perspective_tips', [])
                        }
                    
                    if 'form_volume_score' in artwork:
                        artwork['evaluation_data']['form_and_volume'] = {
                            'score': artwork.pop('form_volume_score', 0),
                            'rationale': artwork.pop('form_volume_rationale', ''),
                            'improvement_tips': artwork.pop('form_volume_tips', [])
                        }
                    
                    if 'mood_expression_score' in artwork:
                        artwork['evaluation_data']['mood_and_expression'] = {
                            'score': artwork.pop('mood_expression_score', 0),
                            'rationale': artwork.pop('mood_expression_rationale', ''),
                            'improvement_tips': artwork.pop('mood_expression_tips', [])
                        }
                    
                    if 'overall_realism_score' in artwork:
                        artwork['evaluation_data']['overall_realism'] = {
                            'score': artwork.pop('overall_realism_score', 0),
                            'rationale': artwork.pop('overall_realism_rationale', ''),
                            'improvement_tips': artwork.pop('overall_realism_tips', [])
                        }
            
            return result
        except Exception as e:
            print(f"Error querying data: {str(e)}")
            return None

class ArtworkEvaluator:
    def __init__(self, model_name="gpt-4.1-2025-04-14", csv_output_path=None, sketch_type="full realism", limit=5):
        """
        Initialize the evaluator with configurable model and output path.
        
        Args:
            model_name (str): The OpenAI model to use for image evaluation
            csv_output_path (str): File path for the CSV output (if None, a default path will be generated)
            sketch_type (str): Type of evaluation ("quick sketch" or "full realism")
            limit (int): Maximum number of artworks to evaluate (default: 5)
        """
        self.model_name = model_name
        
        # Create reports directory if it doesn't exist
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        os.makedirs(reports_dir, exist_ok=True)
        
        # Generate default output path with timestamp and model name if not provided
        if csv_output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Clean up model name for filename (remove dots and special chars)
            clean_model_name = model_name.replace('.', '-').replace('/', '_')
            self.csv_output_path = os.path.join(reports_dir, f"evaluation_{clean_model_name}_{timestamp}.csv")
        else:
            # If path is provided but doesn't include directory, put it in reports folder
            if os.path.dirname(csv_output_path) == '':
                self.csv_output_path = os.path.join(reports_dir, csv_output_path)
            else:
                self.csv_output_path = csv_output_path
                
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.evaluation_prompt = self._get_default_prompt()
        self.sketch_type = sketch_type
        self.limit = limit
        
        # Initialize OpenAI client
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("OpenAI API key not found. Please set OPENAI_API_KEY in your .env file.")
        self.client = OpenAI(api_key=openai_api_key)
        
        # Initialize Cloudinary
        init_cloudinary()
        
        # Initialize Supabase
        init_supabase()
    
    def _get_default_prompt(self):
        """Return the default evaluation prompt."""
        return """You are an expert art critic and instructor. Evaluate the provided sketch using the following criteria, scoring each one on a scale of 1 to 20 (1 = Poor, 20 = Excellent). For each category, include:
A 1–3 sentence rationale explaining the score.
A set of 1–3 actionable tips for how the artist could improve the submitted artwork specifically in each area.

Also, please create a creative title for this artwork based on what you see.
If you recognize a character, object, or location, please include that in the title.

Evaluation Criteria:
Proportion & Structure – Are the relative sizes and shapes of elements accurate and well-constructed?
Line Quality – Are the lines confident, controlled, and varied to define form, contour, or texture effectively?
Value & Light – Is there effective use of shading and light to create realistic depth, contrast, and form?
Detail & Texture – Are the textures believable and appropriate for the subject? Is the level of detail well-judged?
Composition & Perspective – Is the placement of elements balanced? Is perspective applied accurately?
Form & Volume – Does the drawing feel three-dimensional? Are forms convincingly modeled through shading or structure?
Mood & Expression – Does the image evoke a mood, emotion, or atmosphere, even subtly?
Overall Realism – How realistic is the overall sketch in terms of visual believability and execution?
        """
    
    def set_evaluation_prompt(self, new_prompt):
        """Update the evaluation prompt."""
        self.evaluation_prompt = new_prompt
    
    def set_model(self, model_name):
        """Update the model name."""
        self.model_name = model_name
        print(f"Model updated to: {model_name}")
        
    def set_sketch_type(self, sketch_type):
        """Update the sketch type (quick sketch or full realism)."""
        if sketch_type not in ["quick sketch", "full realism"]:
            print(f"Warning: Invalid sketch type '{sketch_type}'. Using 'full realism' instead.")
            self.sketch_type = "full realism"
        else:
            self.sketch_type = sketch_type
            print(f"Sketch type updated to: {sketch_type}")
    
    def get_all_images(self):
        """Retrieve all artworks from the database."""
        print("Retrieving all artworks from the database...")
        artworks = get_all_artworks()
        
        if not artworks or not artworks.data:
            print("No artworks found in the database.")
            return []
        
        print(f"Retrieved {len(artworks.data)} artworks.")
        return artworks.data
    
    def evaluate_image(self, artwork):
        """
        Evaluate a single image using the configured AI model and prompt.
        
        Args:
            artwork (dict): Artwork data from the database
        
        Returns:
            dict: Evaluation results
        """
        image_url = artwork.get('image_url')
        if not image_url:
            public_id = artwork.get('image_public_id')
            if public_id:
                image_url = get_image_url(public_id)
            
        if not image_url:
            print(f"Error: No image URL found for artwork ID {artwork.get('id', 'Unknown')}")
            return None
        
        print(f"Evaluating image: {artwork.get('title', 'Untitled')} (ID: {artwork.get('id', 'Unknown')})")
        
        # Prepare the system prompt based on sketch type
        system_prompt = "You are an expert art critic and instructor. Evaluate the provided sketch using the following criteria, scoring each one on a scale of 1 to 20 (1 = Poor, 20 = Excellent). For each category, include:"
        system_prompt += """
A 1–3 sentence rationale explaining the score.
A set of 1–3 actionable tips for how the artist could improve the submitted artwork specifically in each area.

Also, please create a creative title for this artwork based on what you see.
If you recognize a character, object, or location, please include that in the title.

Evaluation Criteria:
Proportion & Structure – Are the relative sizes and shapes of elements accurate and well-constructed?
Line Quality – Are the lines confident, controlled, and varied to define form, contour, or texture effectively?"""

        # Add additional criteria for full realism mode
        if self.sketch_type == "full realism":
            system_prompt += """
Value & Light – Is there effective use of shading and light to create realistic depth, contrast, and form?
Detail & Texture – Are the textures believable and appropriate for the subject? Is the level of detail well-judged?
Composition & Perspective – Is the placement of elements balanced? Is perspective applied accurately?"""
            
        # Add form and volume, mood and expression for both modes
        system_prompt += """
Form & Volume – Does the drawing feel three-dimensional? Are forms convincingly modeled through shading or structure?
Mood & Expression – Does the image evoke a mood, emotion, or atmosphere, even subtly?"""
            
        # Add overall realism for full realism mode only
        if self.sketch_type == "full realism":
            system_prompt += """
Overall Realism – How realistic is the overall sketch in terms of visual believability and execution?"""

        # Create JSON schema based on sketch type
        schema = {
            "type": "object",
            "properties": {
                "generated_title": {
                    "type": "string",
                    "description": "A creative title for the artwork"
                },
                "proportion_and_structure": {
                    "type": "object",
                    "properties": {
                        "score": {
                            "type": "integer",
                            "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                        },
                        "rationale": {
                            "type": "string",
                            "description": "1-3 sentence explanation for the score"
                        },
                        "improvement_tips": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "1-3 actionable tips for improvement"
                        }
                    },
                    "required": ["score", "rationale", "improvement_tips"],
                    "additionalProperties": False
                },
                "line_quality": {
                    "type": "object",
                    "properties": {
                        "score": {
                            "type": "integer",
                            "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                        },
                        "rationale": {
                            "type": "string",
                            "description": "1-3 sentence explanation for the score"
                        },
                        "improvement_tips": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "1-3 actionable tips for improvement"
                        }
                    },
                    "required": ["score", "rationale", "improvement_tips"],
                    "additionalProperties": False
                },
                "form_and_volume": {
                    "type": "object",
                    "properties": {
                        "score": {
                            "type": "integer",
                            "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                        },
                        "rationale": {
                            "type": "string",
                            "description": "1-3 sentence explanation for the score"
                        },
                        "improvement_tips": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "1-3 actionable tips for improvement"
                        }
                    },
                    "required": ["score", "rationale", "improvement_tips"],
                    "additionalProperties": False
                },
                "mood_and_expression": {
                    "type": "object",
                    "properties": {
                        "score": {
                            "type": "integer",
                            "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                        },
                        "rationale": {
                            "type": "string",
                            "description": "1-3 sentence explanation for the score"
                        },
                        "improvement_tips": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "1-3 actionable tips for improvement"
                        }
                    },
                    "required": ["score", "rationale", "improvement_tips"],
                    "additionalProperties": False
                }
            },
            "required": ["generated_title", "proportion_and_structure", "line_quality", "form_and_volume", "mood_and_expression"],
            "additionalProperties": False
        }
        
        # Add additional schema properties for full realism mode
        if self.sketch_type == "full realism":
            schema["properties"]["value_and_light"] = {
                "type": "object",
                "properties": {
                    "score": {
                        "type": "integer",
                        "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                    },
                    "rationale": {
                        "type": "string",
                        "description": "1-3 sentence explanation for the score"
                    },
                    "improvement_tips": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "1-3 actionable tips for improvement"
                    }
                },
                "required": ["score", "rationale", "improvement_tips"],
                "additionalProperties": False
            }
            
            schema["properties"]["detail_and_texture"] = {
                "type": "object",
                "properties": {
                    "score": {
                        "type": "integer",
                        "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                    },
                    "rationale": {
                        "type": "string",
                        "description": "1-3 sentence explanation for the score"
                    },
                    "improvement_tips": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "1-3 actionable tips for improvement"
                    }
                },
                "required": ["score", "rationale", "improvement_tips"],
                "additionalProperties": False
            }
            
            schema["properties"]["composition_and_perspective"] = {
                "type": "object",
                "properties": {
                    "score": {
                        "type": "integer",
                        "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                    },
                    "rationale": {
                        "type": "string",
                        "description": "1-3 sentence explanation for the score"
                    },
                    "improvement_tips": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "1-3 actionable tips for improvement"
                    }
                },
                "required": ["score", "rationale", "improvement_tips"],
                "additionalProperties": False
            }
            
            schema["properties"]["overall_realism"] = {
                "type": "object",
                "properties": {
                    "score": {
                        "type": "integer",
                        "description": "Score between 1 and 20, where 1 is poor and 20 is excellent"
                    },
                    "rationale": {
                        "type": "string",
                        "description": "1-3 sentence explanation for the score"
                    },
                    "improvement_tips": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "1-3 actionable tips for improvement"
                    }
                },
                "required": ["score", "rationale", "improvement_tips"],
                "additionalProperties": False
            }
            
            # Update required properties for full realism
            schema["required"] = ["generated_title", "proportion_and_structure", "line_quality", "value_and_light", 
                                "detail_and_texture", "composition_and_perspective", 
                                "form_and_volume", "mood_and_expression", "overall_realism"]
        
        try:
            # Use OpenAI's vision capabilities to evaluate the image with structured response
            response = self.client.responses.create(
                model=self.model_name,
                input=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": f"Here's an artwork by {artwork.get('artist_name', 'Unknown artist')}."
                            },
                            {
                                "type": "input_image",
                                "image_url": image_url
                            }
                        ]
                    }
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "artwork_evaluation",
                        "schema": schema,
                        "strict": True
                    }
                }
            )
            
            try:
                # Parse JSON response
                evaluation_data = json.loads(response.output_text)
                return evaluation_data
            except (json.JSONDecodeError, AttributeError) as e:
                print(f"Error: Failed to parse JSON response for artwork ID {artwork.get('id', 'Unknown')}: {str(e)}")
                return None
                
        except Exception as e:
            print(f"Error evaluating image (ID: {artwork.get('id', 'Unknown')}): {str(e)}")
            return None
    
    def evaluate_all_images(self):
        """
        Evaluate all images and return the results, limited by self.limit.
        
        Returns:
            list: List of evaluation results that include both new evaluations and existing data
        """
        artworks = self.get_all_images()
        results = []
        
        # Limit the number of artworks to evaluate
        if self.limit > 0 and len(artworks) > self.limit:
            print(f"Limiting evaluations to {self.limit} artworks (out of {len(artworks)} total)")
            artworks = artworks[:self.limit]
        else:
            print(f"Evaluating all {len(artworks)} artworks")
        
        for i, artwork in enumerate(artworks):
            print(f"Processing artwork {i+1} of {len(artworks)}...")
            
            # Basic artwork metadata
            artwork_data = {
                "id": artwork.get("id"),
                "title": artwork.get("title", "Untitled"),
                "artist_name": artwork.get("artist_name", "Unknown"),
                "created_at": artwork.get("created_at", ""),
                "image_url": artwork.get("image_url", ""),
                "sketch_type": artwork.get("sketch_type", "full realism"),
                "evaluation_data": artwork.get("evaluation_data", {})
            }
            
            # Evaluate the image
            new_evaluation = self.evaluate_image(artwork)
            
            if new_evaluation:
                # Combine artwork metadata with evaluation
                result = {
                    **artwork_data,
                    "new_evaluation": new_evaluation
                }
                results.append(result)
            
            # Sleep briefly to avoid API rate limits
            time.sleep(1)
        
        return results
    
    def save_to_csv(self, results):
        """
        Save evaluation results to a CSV file, including existing scores from the database.
        If the original file path is inaccessible, will try to save to a uniquely named file.
        
        Args:
            results (list): List of evaluation results
        """
        if not results:
            print("No results to save.")
            return
        
        # Convert nested JSON structure to flat format for CSV
        flattened_results = []
        
        for result in results:
            # Start with basic metadata
            flat_result = {
                "id": result.get("id"),
                "title": result.get("title"),
                "artist_name": result.get("artist_name"),
                "created_at": result.get("created_at"),
                "image_url": result.get("image_url"),
                "sketch_type": result.get("sketch_type", self.sketch_type)
            }
            
            # Get the new evaluation data
            new_evaluation = result.get("new_evaluation", {})
            flat_result["generated_title"] = new_evaluation.get("generated_title", "")
            
            # Get existing evaluation data
            existing_evaluation = result.get("evaluation_data", {})
            
            # Criteria fields to process
            criteria_fields = ["proportion_and_structure", "line_quality", "form_and_volume", "mood_and_expression"]
            
            # Add full realism criteria if applicable
            if self.sketch_type == "full realism" or flat_result["sketch_type"] == "full realism":
                criteria_fields.extend(["value_and_light", "detail_and_texture", "composition_and_perspective", "overall_realism"])
            
            # Calculate average scores for both existing and new evaluations
            existing_scores = []
            new_scores = []
            
            # Process each criteria
            for criteria in criteria_fields:
                # Process new evaluation data
                if criteria in new_evaluation:
                    # New scores
                    new_score = new_evaluation[criteria].get("score")
                    flat_result[f"new_{criteria}_score"] = new_score
                    flat_result[f"new_{criteria}_rationale"] = new_evaluation[criteria].get("rationale", "")
                    
                    # Join improvement tips into a single string
                    tips = new_evaluation[criteria].get("improvement_tips", [])
                    flat_result[f"new_{criteria}_tips"] = "; ".join(tips) if tips else ""
                    
                    if new_score:
                        new_scores.append(new_score)
                
                # Process existing evaluation data
                if criteria in existing_evaluation:
                    # Existing scores
                    existing_score = existing_evaluation[criteria].get("score")
                    flat_result[f"existing_{criteria}_score"] = existing_score
                    
                    if existing_score:
                        existing_scores.append(existing_score)
                    
                    # Calculate difference between new and existing scores if both exist
                    if new_score and existing_score:
                        flat_result[f"{criteria}_score_diff"] = new_score - existing_score
            
            # Calculate average scores
            if existing_scores:
                existing_avg_score = sum(existing_scores) / len(existing_scores)
                flat_result["existing_average_raw_score"] = existing_avg_score
                
                # Calculate curved score for existing average
                if existing_avg_score >= 18:
                    existing_curved_score = 10
                else:
                    existing_curved_score = max(0, existing_avg_score - 8)
                flat_result["existing_average_curved_score"] = existing_curved_score
            
            if new_scores:
                new_avg_score = sum(new_scores) / len(new_scores)
                flat_result["new_average_raw_score"] = new_avg_score
                
                # Calculate curved score for new average
                if new_avg_score >= 18:
                    new_curved_score = 10
                else:
                    new_curved_score = max(0, new_avg_score - 8)
                flat_result["new_average_curved_score"] = new_curved_score
                
                # Calculate difference in average scores if both exist
                if existing_scores:
                    flat_result["average_score_diff"] = new_avg_score - existing_avg_score
                    flat_result["average_curved_score_diff"] = new_curved_score - existing_curved_score
            
            flattened_results.append(flat_result)
        
        # Convert to DataFrame and save to CSV
        df = pd.DataFrame(flattened_results)
        
        # Reorder columns for better readability
        column_order = [
            # Metadata
            "id", "title", "artist_name", "created_at", "sketch_type", "generated_title", "image_url",
            
            # Average scores
            "existing_average_raw_score", "new_average_raw_score", "average_score_diff",
            "existing_average_curved_score", "new_average_curved_score", "average_curved_score_diff"
        ]
        
        # Add criteria columns in pairs (existing, new, diff) for easier comparison
        for criteria in criteria_fields:
            column_order.extend([
                f"existing_{criteria}_score", f"new_{criteria}_score", f"{criteria}_score_diff",
                f"new_{criteria}_rationale", f"new_{criteria}_tips"
            ])
        
        # Ensure all columns exist before reordering
        available_columns = [col for col in column_order if col in df.columns]
        remaining_columns = [col for col in df.columns if col not in column_order]
        ordered_columns = available_columns + remaining_columns
        
        # Reorder dataframe
        df = df[ordered_columns]
        
        # Try to save to the original filename first
        try:
            df.to_csv(self.csv_output_path, index=False)
            print(f"Results saved to {self.csv_output_path}")
            
        except PermissionError:
            # If permission error (file might be open in another program), create a unique filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_base, file_ext = os.path.splitext(self.csv_output_path)
            new_filename = f"{file_base}_{timestamp}{file_ext}"
            
            try:
                df.to_csv(new_filename, index=False)
                print(f"Original file was inaccessible. Results saved to {new_filename} instead.")
            except Exception as e:
                print(f"Error saving CSV: {str(e)}")
                
                # Last resort: save to user's home directory
                home_dir = os.path.expanduser("~")
                last_resort_file = os.path.join(home_dir, f"ruggles_evaluation_{timestamp}.csv")
                
                try:
                    df.to_csv(last_resort_file, index=False)
                    print(f"Results saved to {last_resort_file}")
                except Exception as e2:
                    print(f"Failed to save results: {str(e2)}")
        
        except Exception as e:
            print(f"Error saving CSV: {str(e)}")
    
    def run_evaluation(self):
        """Run the full evaluation process and save results."""
        print(f"Starting evaluation using model: {self.model_name}")
        results = self.evaluate_all_images()
        self.save_to_csv(results)
        print("Evaluation complete!")
        return results


if __name__ == "__main__":
    # Command line interface
    import argparse
    
    parser = argparse.ArgumentParser(description="Evaluate artwork images using AI.")
    parser.add_argument("--model", default="gpt-4.1-2025-04-14", help="OpenAI model to use")
    parser.add_argument("--output", help="Output CSV file path (if not specified, a file will be created in the reports directory with timestamp and model name)")
    parser.add_argument("--prompt-file", help="Path to a text file containing a custom evaluation prompt")
    parser.add_argument("--sketch-type", default="full realism", choices=["quick sketch", "full realism"], 
                       help="Type of evaluation to perform")
    parser.add_argument("--limit", type=int, default=5, 
                       help="Maximum number of artworks to evaluate (default: 5, use 0 for no limit)")
    
    args = parser.parse_args()
    
    # Create evaluator with command line options
    evaluator = ArtworkEvaluator(
        model_name=args.model, 
        csv_output_path=args.output, 
        sketch_type=args.sketch_type,
        limit=args.limit
    )
    
    # Load custom prompt if provided
    if args.prompt_file:
        try:
            with open(args.prompt_file, 'r') as f:
                custom_prompt = f.read()
            evaluator.set_evaluation_prompt(custom_prompt)
            print(f"Loaded custom prompt from {args.prompt_file}")
        except Exception as e:
            print(f"Error loading prompt file: {str(e)}")
    
    print(f"Evaluating artwork images using {args.model} with {args.sketch_type} mode")
    # Run the evaluation
    evaluator.run_evaluation()
