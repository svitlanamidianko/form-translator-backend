from flask import jsonify, request
from . import api
import datetime
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sheets_service import get_sheets_service, initialize_sheets_service

from openai import OpenAI

# Initialize OpenAI client - will be created when needed
client = None

def get_openai_client():
    """Get OpenAI client, creating it if needed"""
    global client
    
    if client is None:
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        
        try:
            client = OpenAI(api_key=api_key)
        except Exception as e:
            raise ValueError(f"Failed to initialize OpenAI client: {str(e)}")
    
    return client

# Google Sheet configuration
SHEET_ID = "1sEnmusmz4X_18emilcsLFIn48nwM6qInLgQKN2rXC5M"

# Initialize sheets service on module load
def initialize_global_sheets_service():
    """Initialize the global sheets service with our sheet ID"""
    try:
        sheets_service = initialize_sheets_service(SHEET_ID)
        print(f"✅ Global sheets service initialized with sheet ID: {SHEET_ID}")
        return True
    except Exception as e:
        print(f"❌ Failed to initialize global sheets service: {str(e)}")
        return False

# Initialize on module load
initialize_global_sheets_service()

def get_current_timestamp():
    """Utility function to get current timestamp in ISO format"""
    return datetime.datetime.now().isoformat()


def get_form_types():
    """
    Get form types from Google Sheet
    Returns a dictionary in the format: {form_name: form_description}
    """
    try:
        sheets_service = get_sheets_service()
        if not sheets_service:
            print("❌ Sheets service not available, using fallback")
            return {}
        
        # Use the correct sheet name "forms"
        range_name = "forms!A:C"
        result = sheets_service.service.spreadsheets().values().get(
            spreadsheetId=sheets_service.sheet_id,
            range=range_name
        ).execute()
        
        values = result.get('values', [])
        
        if not values:
            print("❌ No data found in sheet")
            return {}
        
        # Convert sheet data to dictionary format: {form_name: form_description}
        form_types = {}
        
        # Skip header row and process data
        for row in values[1:]:  # Skip header row
            if len(row) >= 3:  # Make sure we have at least 3 columns (ID, Form Name, Description)
                form_name = row[1].strip() if len(row) > 1 else ""
                form_description = row[2].strip() if len(row) > 2 else ""
                
                if form_name and form_description:
                    # Create the full description in the same format as before
                    full_description = f"{form_name} - {form_description}"
                    form_types[form_name] = full_description
        
        print(f"✅ Loaded {len(form_types)} form types from Google Sheet")
        return form_types
        
    except Exception as e:
        print(f"❌ Error loading form types from sheet: {str(e)}")
        return {}

def get_prompt_from_sheet(prompt_id: str = "1") -> str:
    """
    Get the prompt from the third sheet of Google Sheets
    
    Args:
        prompt_id: ID of the prompt to retrieve (default: "1")
        
    Returns:
        str: The prompt text from the sheet
        
    Raises:
        ValueError: If prompt is not found in the sheet
    """
    try:
        sheets_service = get_sheets_service()
        if not sheets_service:
            raise ValueError("Sheets service not available. Cannot retrieve prompt from database.")
        
        # Try to get prompt from 'prompt' sheet
        prompt = sheets_service.get_prompt_from_sheet("prompt", prompt_id)
        
        if prompt:
            print(f"✅ Successfully retrieved prompt with ID '{prompt_id}' from Google Sheets")
            return prompt
        else:
            raise ValueError(f"Prompt with ID '{prompt_id}' not found in 'prompt' sheet. Please add the prompt to your Google Sheets database.")
            
    except Exception as e:
        raise ValueError(f"Failed to retrieve prompt from Google Sheets database: {str(e)}")



@api.route('/translate', methods=['POST'])
def translate_form():

    try:
        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Extract parameters
        source_form = data.get('sourceForm')
        target_form = data.get('targetForm')
        source_text = data.get('sourceText')
        
        # Validate required parameters
        if not all([source_form, target_form, source_text]):
            return jsonify({
                "error": "Missing required parameters. Need: sourceForm, targetForm, sourceText"
            }), 400
        
        # Get current form types from sheet
        form_types = get_form_types()
        
        # Validate form types
        if source_form not in form_types:
            return jsonify({
                "error": f"Invalid sourceForm '{source_form}'. Valid options: {list(form_types.keys())}"
            }), 400
        
        if target_form not in form_types:
            return jsonify({
                "error": f"Invalid targetForm '{target_form}'. Valid options: {list(form_types.keys())}"
            }), 400
        
        # Check if source and target are the same
        if source_form == target_form:
            return jsonify({
                "translatedText": source_text,
                "message": "Source and target forms are identical",
                "timestamp": get_current_timestamp()
            })
        
        # Get the prompt template from Google Sheets
        prompt_template = get_prompt_from_sheet()
        
        # Get form descriptions
        source_description = form_types[source_form]
        target_description = form_types[target_form]
        
        # Format the prompt with the actual values including form names
        prompt = prompt_template.format(
            source_form=source_form,
            source_description=source_description,
            target_form=target_form,
            target_description=target_description,
            source_text=source_text
        )

        # Make OpenAI API call
        openai_client = get_openai_client()
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert in transforming text between different forms of expression while preserving meaning."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        translated_text = response.choices[0].message.content.strip()
        
        return jsonify({
            "translatedText": translated_text,
            "sourceForm": source_form,
            "targetForm": target_form,
            "sourceText": source_text,
            "timestamp": get_current_timestamp()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Translation failed: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500

@api.route('/forms', methods=['GET'])
def get_available_forms():
    """Get list of available form types for translation from Google Sheet"""
    try:
        form_types = get_form_types()
        return jsonify({
            "forms": form_types,
            "count": len(form_types),
            "source": "Google Sheet",
            "timestamp": get_current_timestamp()
        })
    except Exception as e:
        return jsonify({
            "error": f"Failed to load forms from Google Sheet: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500


# Google Sheets Integration Routes

@api.route('/sheets/init', methods=['POST'])
def initialize_sheets():
    """
    Initialize Google Sheets integration with a sheet ID
    
    Expected request body:
    {
        "sheet_id": "your_google_sheet_id_here"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        sheet_id = data.get('sheet_id')
        if not sheet_id:
            return jsonify({"error": "sheet_id is required"}), 400
        
        # Initialize the sheets service
        sheets_service = initialize_sheets_service(sheet_id)
        
        # Create headers if needed
        sheets_service.create_headers_if_needed()
        
        return jsonify({
            "message": "Google Sheets integration initialized successfully",
            "sheet_id": sheet_id,
            "timestamp": get_current_timestamp()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to initialize Google Sheets: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500


@api.route('/forms/list', methods=['GET'])
def list_forms():
    """Get all forms from Google Sheets"""
    try:
        sheets_service = get_sheets_service()
        forms = sheets_service.get_all_forms()
        
        return jsonify({
            "forms": forms,
            "count": len(forms),
            "timestamp": get_current_timestamp()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to retrieve forms: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500




