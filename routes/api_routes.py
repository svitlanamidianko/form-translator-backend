from flask import jsonify, request
from . import api
import datetime
import os
import sys
import threading
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sheets_service import get_sheets_service, initialize_sheets_service

import openai

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
            # Use the older openai.api_key method which is more stable
            openai.api_key = api_key
            client = openai
            print("✅ OpenAI client initialized successfully with legacy method")
        except Exception as e:
            # Fallback to new method if legacy doesn't work
            try:
                from openai import OpenAI
                # Create a minimal client without any optional parameters
                client = OpenAI(api_key=api_key)
                print("✅ OpenAI client initialized successfully with new method")
            except Exception as new_e:
                raise ValueError(f"Failed to initialize OpenAI client: {str(new_e)}")
    
    return client

# Google Sheet configuration
SHEET_ID = "1sEnmusmz4X_18emilcsLFIn48nwM6qInLgQKN2rXC5M"

# Global state for background initialization
_sheets_initialization_status = {
    'initialized': False,
    'in_progress': False,
    'error': None,
    'start_time': None
}

def initialize_global_sheets_service():
    """Initialize the global sheets service with our sheet ID"""
    try:
        sheets_service = initialize_sheets_service(SHEET_ID)
        print(f"✅ Global sheets service initialized with sheet ID: {SHEET_ID}")
        return True
    except Exception as e:
        print(f"❌ Failed to initialize global sheets service: {str(e)}")
        return False

def _background_initialize_sheets():
    """Background thread function to initialize sheets service"""
    global _sheets_initialization_status
    
    _sheets_initialization_status['in_progress'] = True
    _sheets_initialization_status['start_time'] = time.time()
    
    try:
        print("🔄 Starting background Google Sheets initialization...")
        success = initialize_global_sheets_service()
        
        if success:
            _sheets_initialization_status['initialized'] = True
            elapsed = time.time() - _sheets_initialization_status['start_time']
            print(f"✅ Background sheets initialization completed in {elapsed:.2f}s")
        else:
            _sheets_initialization_status['error'] = "Initialization failed"
            print("❌ Background sheets initialization failed")
            
    except Exception as e:
        _sheets_initialization_status['error'] = str(e)
        print(f"❌ Background sheets initialization error: {str(e)}")
    finally:
        _sheets_initialization_status['in_progress'] = False

def start_background_sheets_initialization():
    """Start the background sheets initialization thread"""
    if not _sheets_initialization_status['in_progress'] and not _sheets_initialization_status['initialized']:
        thread = threading.Thread(target=_background_initialize_sheets, daemon=True)
        thread.start()
        print("🚀 Started background Google Sheets initialization thread")

# Start background initialization immediately
start_background_sheets_initialization()

def get_current_timestamp():
    """Utility function to get current timestamp in ISO format"""
    return datetime.datetime.now().isoformat()

def wait_for_sheets_initialization(max_wait_seconds=10):
    """
    Wait for sheets initialization to complete, with timeout
    
    Args:
        max_wait_seconds: Maximum time to wait for initialization
        
    Returns:
        bool: True if initialized successfully, False if timeout or error
    """
    global _sheets_initialization_status
    
    if _sheets_initialization_status['initialized']:
        return True
    
    if _sheets_initialization_status['error']:
        return False
    
    # Wait for initialization to complete
    start_wait = time.time()
    while time.time() - start_wait < max_wait_seconds:
        if _sheets_initialization_status['initialized']:
            return True
        if _sheets_initialization_status['error']:
            return False
        time.sleep(0.1)  # Check every 100ms
    
    print(f"⚠️ Sheets initialization timeout after {max_wait_seconds}s")
    return False


def get_form_types(include_user_submitted: bool = False):
    """
    Get form types from Google Sheet
    Returns a dictionary in the format: {form_name: form_description}
    
    Args:
        include_user_submitted: Whether to include user-submitted forms (default: False)
    """
    try:
        # Wait for sheets service to be initialized
        if not wait_for_sheets_initialization():
            print("❌ Sheets service initialization failed or timed out, using fallback")
            return {}
            
        sheets_service = get_sheets_service()
        if not sheets_service:
            print("❌ Sheets service not available, using fallback")
            return {}
        
        # Get forms using the updated method
        forms = sheets_service.get_all_forms("forms", include_user_submitted=include_user_submitted)
        
        if not forms:
            print("❌ No forms found in sheet")
            return {}
        
        # Convert to dictionary format: {form_name: form_description}
        form_types = {}
        
        for form in forms:
            form_name = form.get('form_name', '').strip()
            form_description = form.get('form_description', '').strip()
            
            # Include form if it has a name (description is optional)
            if form_name:
                if form_description:
                    # Create the full description when both name and description exist
                    full_description = f"{form_name} - {form_description}"
                else:
                    # Use just the form name when no description is provided
                    full_description = form_name
                
                form_types[form_name] = full_description
        
        print(f"✅ Loaded {len(form_types)} form types from Google Sheet (include_user_submitted: {include_user_submitted})")
        return form_types
        
    except Exception as e:
        print(f"❌ Error loading form types from sheet: {str(e)}")
        return {}

def get_form_types_with_categories(include_user_submitted: bool = False):
    """
    Get form types from Google Sheet with categories included
    Returns a dictionary in the format: {form_name: {description: str, category: str}}
    
    Args:
        include_user_submitted: Whether to include user-submitted forms (default: False)
    """
    try:
        # Wait for sheets service to be initialized
        if not wait_for_sheets_initialization():
            print("❌ Sheets service initialization failed or timed out, using fallback")
            return {}
            
        sheets_service = get_sheets_service()
        if not sheets_service:
            print("❌ Sheets service not available, using fallback")
            return {}
        
        # Get forms using the updated method
        forms = sheets_service.get_all_forms("forms", include_user_submitted=include_user_submitted)
        
        if not forms:
            print("❌ No forms found in sheet")
            return {}
        
        # Convert to dictionary format: {form_name: {description: str, category: str}}
        form_types = {}
        
        for form in forms:
            form_name = form.get('form_name', '').strip()
            form_description = form.get('form_description', '').strip()
            category = form.get('category', '').strip()
            
            # Include form if it has a name (description is optional)
            if form_name:
                if form_description:
                    # Create the full description when both name and description exist
                    full_description = f"{form_name} - {form_description}"
                else:
                    # Use just the form name when no description is provided
                    full_description = form_name
                
                form_types[form_name] = {
                    "description": full_description,
                    "category": category if category else None
                }
        
        print(f"✅ Loaded {len(form_types)} form types with categories from Google Sheet (include_user_submitted: {include_user_submitted})")
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
        # Wait for sheets service to be initialized
        if not wait_for_sheets_initialization():
            raise ValueError("Sheets service initialization failed or timed out. Cannot retrieve prompt from database.")
            
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
        print(f"🔍 DEBUG: Received request data: {data}")
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Extract parameters
        source_form = data.get('sourceForm')
        target_form = data.get('targetForm')
        # Accept both 'sourceText' and 'inputText' for backward compatibility
        source_text = data.get('sourceText') or data.get('inputText')
        source_form_description = data.get('sourceFormDescription', '')
        target_form_description = data.get('targetFormDescription', '')
        
        # DEBUG: Log the translation request parameters
        print(f"🔍 DEBUG: Translation request - sourceForm: '{source_form}', targetForm: '{target_form}'")
        print(f"🔍 DEBUG: Translation request - sourceText: '{source_text[:50]}...' (truncated)")
        
        # Validate required parameters
        if not all([source_form, target_form, source_text]):
            return jsonify({
                "error": "Missing required parameters. Need: sourceForm, targetForm, sourceText (or inputText)"
            }), 400
        
        # Get current form types from sheet (including user-submitted forms for validation)
        form_types = get_form_types(include_user_submitted=True)
        
        # Handle custom forms - add them to the sheet if they don't exist
        sheets_service = get_sheets_service()
        
        # Check and add source form if it's custom
        if source_form not in form_types:
            print(f"🔍 DEBUG: Source form '{source_form}' not found in existing forms")
            print(f"🔍 DEBUG: Source form description provided: '{source_form_description}'")
            
            # This is a custom form, add it to the sheet with empty description and category
            success = sheets_service.add_custom_form(
                form_name=source_form,
                form_description="",  # Empty description for custom forms
                category=""  # Empty category for custom forms
            )
            if success:
                print(f"✅ Added custom source form: {source_form}")
                # Update form_types to include the new form (just the name, no description)
                form_types[source_form] = source_form
            else:
                print(f"⚠️ Failed to add custom source form: {source_form}")
                error_msg = f"Failed to create custom sourceForm '{source_form}'. Please try again."
                return jsonify({"error": error_msg}), 500
        
        # Check and add target form if it's custom
        if target_form not in form_types:
            print(f"🔍 DEBUG: Target form '{target_form}' not found in existing forms")
            print(f"🔍 DEBUG: Target form description provided: '{target_form_description}'")
            
            # This is a custom form, add it to the sheet with empty description and category
            success = sheets_service.add_custom_form(
                form_name=target_form,
                form_description="",  # Empty description for custom forms
                category=""  # Empty category for custom forms
            )
            if success:
                print(f"✅ Added custom target form: {target_form}")
                # Update form_types to include the new form (just the name, no description)
                form_types[target_form] = target_form
            else:
                print(f"⚠️ Failed to add custom target form: {target_form}")
                error_msg = f"Failed to create custom targetForm '{target_form}'. Please try again."
                return jsonify({"error": error_msg}), 500
        
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
        
        # Handle both legacy and new OpenAI client methods
        if hasattr(openai_client, 'chat') and hasattr(openai_client.chat, 'completions'):
            # New OpenAI client method
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are an expert in transforming text between different forms of expression while preserving meaning."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.7
            )
        else:
            # Legacy OpenAI method
            response = openai_client.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are an expert in transforming text between different forms of expression while preserving meaning."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.7
            )
        translated_text = response.choices[0].message.content.strip()
        
        # Remove unwanted quotation marks from the beginning and end
        # This handles both single and double quotes that OpenAI sometimes adds
        def clean_quotes(text):
            """Remove wrapping quotes while preserving quotes that are part of the content"""
            text = text.strip()
            
            # Remove double quotes that wrap the entire text
            if text.startswith('"') and text.endswith('"') and len(text) > 2:
                # Check if removing these quotes leaves a valid sentence
                inner_text = text[1:-1].strip()
                if inner_text:  # Only remove if there's content inside
                    text = inner_text
            
            # Remove single quotes that wrap the entire text
            elif text.startswith("'") and text.endswith("'") and len(text) > 2:
                inner_text = text[1:-1].strip()
                if inner_text:
                    text = inner_text
            
            return text
        
        translated_text = clean_quotes(translated_text)
        
        # Log the translation to history database
        try:
            sheets_service = get_sheets_service()
            if sheets_service:
                # DEBUG: Log what we're about to store in history
                print(f"🔍 DEBUG: Logging to history - sourceForm: '{source_form}', targetForm: '{target_form}'")
                print(f"🔍 DEBUG: Logging to history - sourceText: '{source_text[:30]}...', targetText: '{translated_text[:30]}...'")
                
                success = sheets_service.add_translation_to_history(
                    source_form=source_form,
                    source_text=source_text,
                    target_form=target_form,
                    target_text=translated_text
                )
                if success:
                    print(f"✅ Translation logged to history database")
                else:
                    print(f"⚠️ Failed to log translation to history database")
            else:
                print(f"⚠️ Sheets service not available, translation not logged to history")
        except Exception as e:
            print(f"⚠️ Error logging translation to history: {str(e)}")
            # Continue with the response even if history logging fails
        
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

@api.route('/detect-form', methods=['POST'])
def detect_form():
    """
    Detect the form of input text using AI analysis
    
    Expected request body:
    {
        "text": "Text to analyze for form detection"
    }
    
    Returns:
    JSON response with detected form information including:
    - detectedForm: The detected form name
    - confidence: Confidence level (high/medium/low)
    - reasoning: Explanation of why this form was detected
    - isCustomForm: Whether this is a custom form not in the database
    - availableForms: List of forms that were considered
    """
    try:
        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        text = data.get('text')
        if not text:
            return jsonify({"error": "text is required"}), 400
        
        # Validate that text is not empty after trimming
        text = text.strip()
        if not text:
            return jsonify({"error": "text cannot be empty"}), 400
        
        # Get all available forms from the database (including user-submitted ones for detection)
        form_types = get_form_types(include_user_submitted=True)
        
        if not form_types:
            return jsonify({
                "error": "No forms available in database for detection"
            }), 500
        
        # Build the detection prompt
        forms_list = []
        for form_name, form_description in form_types.items():
            forms_list.append(f"- {form_name}")
        
        forms_text = "\n".join(forms_list)
        
        detection_prompt = f"""You are a Form Detection expert who understands that language is a wrapper around meaning. Different forms (ontologies) like STEM, woo-woo, policy, UX, poetry, etc. can express the same underlying meaning using different conceptual frameworks, vocabularies, and ways of thinking.

Your task is to identify which ontological framework/form the text is using to express its meaning.

Available forms in our database:
{forms_text}

Analyze this text to detect its form:

Text to analyze: "{text}"

PHILOSOPHY OF FORM DETECTION:
- Each form has its own concepts, idioms, vocabulary, and way of thinking
- Forms are different "ontologies" - different ways of understanding and expressing reality
- The same meaning can be wrapped in different forms for different audiences
- Look for the underlying conceptual framework, not just style or tone

DETECTION INSTRUCTIONS:
1. Identify which ontological framework/conceptual system the text is using
2. Look for form-specific vocabulary, concepts, metaphors, and ways of reasoning
3. Consider the audience and purpose the text seems designed for
4. Match to existing forms first, but suggest new forms if the text uses a distinct ontology
5. Some text might blend multiple forms or have no clear ontological framework
6. Keep your reasoning to exactly two sentences - be concise and clear
7. Do NOT quote or reference the input text in your reasoning - explain the form directly. Start with "This form..." or "The [form name] ontology..." instead of "The text..."

Respond in this exact JSON format:
{{
    "detectedForm": "form_name_here",
    "reasoning": "Explain why this form was detected - focus on the ontological framework, not the input text (keep to exactly two sentences)",
    "isCustomForm": true/false,
    "alternativeForms": ["form1", "form2"] (if multiple ontological frameworks detected)
}}

If no clear ontological framework is detected, use "neutral" and explain why."""

        # Make OpenAI API call for form detection
        openai_client = get_openai_client()
        
        # Handle both legacy and new OpenAI client methods
        if hasattr(openai_client, 'chat') and hasattr(openai_client.chat, 'completions'):
            # New OpenAI client method
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a Form Detection expert who understands that different forms are ontological frameworks for expressing meaning. You identify which conceptual system a text uses. Always respond with valid JSON."},
                    {"role": "user", "content": detection_prompt}
                ],
                max_tokens=500,
                temperature=0.3
            )
        else:
            # Legacy OpenAI method
            response = openai_client.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a Form Detection expert who understands that different forms are ontological frameworks for expressing meaning. You identify which conceptual system a text uses. Always respond with valid JSON."},
                    {"role": "user", "content": detection_prompt}
                ],
                max_tokens=500,
                temperature=0.3
            )
        
        detection_result = response.choices[0].message.content.strip()
        
        # DEBUG: Log the raw AI response
        print(f"🔍 DEBUG: Raw AI detection response: {detection_result}")
        
        # Parse the JSON response
        import json
        try:
            detection_data = json.loads(detection_result)
            print(f"🔍 DEBUG: Parsed detection data: {detection_data}")
        except json.JSONDecodeError as e:
            print(f"🔍 DEBUG: JSON parsing failed: {e}")
            # If JSON parsing fails, create a fallback response
            detection_data = {
                "detectedForm": "neutral",
                "reasoning": "Unable to parse AI response",
                "isCustomForm": True,
                "alternativeForms": []
            }
        
        # Validate and clean the response
        detected_form = detection_data.get('detectedForm', 'neutral')
        reasoning = detection_data.get('reasoning', 'No reasoning provided')
        is_custom_form = detection_data.get('isCustomForm', True)
        alternative_forms = detection_data.get('alternativeForms', [])
        
        # DEBUG: Log the detected form before processing
        print(f"🔍 DEBUG: Detected form before processing: '{detected_form}'")
        print(f"🔍 DEBUG: Available form types: {list(form_types.keys())}")
        
        # Check if the detected form exists in our database
        if detected_form in form_types:
            is_custom_form = False
            print(f"🔍 DEBUG: Form '{detected_form}' exists in database, not custom")
        else:
            is_custom_form = True
            print(f"🔍 DEBUG: Form '{detected_form}' not in database, is custom")
        
        # If it's a custom form, add it to the database
        if is_custom_form and detected_form != "neutral":
            print(f"🔍 DEBUG: Adding custom form '{detected_form}' to database")
            try:
                sheets_service = get_sheets_service()
                if sheets_service:
                    success = sheets_service.add_custom_form(
                        form_name=detected_form,
                        form_description=f"Auto-detected form: {reasoning}",
                        category="auto-detected"
                    )
                    if success:
                        print(f"✅ Added auto-detected custom form: {detected_form}")
                    else:
                        print(f"❌ Failed to add auto-detected custom form: {detected_form}")
                else:
                    print(f"❌ Sheets service not available")
            except Exception as e:
                print(f"⚠️ Failed to add auto-detected form to database: {str(e)}")
        elif detected_form == "neutral":
            print(f"🔍 DEBUG: Skipping database addition for 'neutral' form")
        else:
            print(f"🔍 DEBUG: Skipping database addition for existing form '{detected_form}'")
        
        # DEBUG: Log the final response
        print(f"🔍 DEBUG: Final response - detectedForm: '{detected_form}', isCustomForm: {is_custom_form}")
        
        return jsonify({
            "detectedForm": detected_form,
            "reasoning": reasoning,
            "isCustomForm": is_custom_form,
            "alternativeForms": alternative_forms
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Form detection failed: {str(e)}"
        }), 500


@api.route('/status', methods=['GET'])
def get_initialization_status():
    """Get the current initialization status of the sheets service"""
    global _sheets_initialization_status
    
    status_info = {
        "sheets_initialized": _sheets_initialization_status['initialized'],
        "sheets_in_progress": _sheets_initialization_status['in_progress'],
        "sheets_error": _sheets_initialization_status['error'],
        "timestamp": get_current_timestamp()
    }
    
    if _sheets_initialization_status['start_time']:
        elapsed = time.time() - _sheets_initialization_status['start_time']
        status_info["initialization_elapsed_seconds"] = round(elapsed, 2)
    
    return jsonify(status_info)

@api.route('/forms', methods=['GET'])
def get_available_forms():
    """Get list of available form types for translation from Google Sheet (excludes user-submitted forms)"""
    try:
        # Only return non-user-submitted forms for the UI dropdown
        form_types = get_form_types_with_categories(include_user_submitted=False)
        return jsonify({
            "forms": form_types,
            "count": len(form_types),
            "source": "Google Sheet",
            "note": "User-submitted custom forms are excluded from this list",
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
    """Get all forms from Google Sheets with optional filtering"""
    try:
        # Get query parameter for including user-submitted forms
        include_user_submitted = request.args.get('include_user_submitted', 'false').lower() == 'true'
        
        sheets_service = get_sheets_service()
        forms = sheets_service.get_all_forms("forms", include_user_submitted=include_user_submitted)
        
        return jsonify({
            "forms": forms,
            "count": len(forms),
            "include_user_submitted": include_user_submitted,
            "timestamp": get_current_timestamp()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to retrieve forms: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500


@api.route('/history', methods=['GET'])
def get_history():
    """
    Get translation history from Google Sheets, sorted by star count first (highest to lowest), then by newest first
    
    Returns:
        JSON response containing history data with:
        - id: unique identifier
        - stars_count: number of stars for this translation
        - source_form: the original form type
        - source_form_id: ID of the source form
        - source_text: original text (source translation)
        - target_form: the target form type
        - target_form_id: ID of the target form  
        - target_text: translated text (target translation)
        - datetime: when the translation was created
    """
    try:
        sheets_service = get_sheets_service()
        if not sheets_service:
            return jsonify({
                "error": "Sheets service not available",
                "timestamp": get_current_timestamp()
            }), 500
        
        # Get history data from the "history" sheet
        history_data = sheets_service.get_history_data("history")
        
        return jsonify({
            "history": history_data,
            "count": len(history_data),
            "source": "Google Sheet - history tab",
            "sorted_by": "star count descending (highest first), then datetime descending (newest first)",
            "timestamp": get_current_timestamp()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to retrieve history: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500


# Star Management Routes

@api.route('/star/<translation_id>', methods=['GET'])
def get_star_count(translation_id):
    """
    Get star count for a specific translation
    
    Args:
        translation_id: The ID of the translation to get star count for
        
    Returns:
        JSON response with translation_id and total_stars count
    """
    try:
        sheets_service = get_sheets_service()
        if not sheets_service:
            return jsonify({
                "error": "Sheets service not available",
                "timestamp": get_current_timestamp()
            }), 500
        
        # Get star count from sheets service
        star_count = sheets_service.get_star_count(translation_id)
        
        return jsonify({
            "translationId": translation_id,
            "totalStars": star_count,
            "timestamp": get_current_timestamp()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to get star count: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500


@api.route('/star', methods=['POST'])
def update_star():
    """
    Update star count for a translation (star or unstar)
    
    Expected request body:
    {
        "translationId": "some_id",
        "action": "star" | "unstar"
    }
    
    Returns:
        JSON response with updated star count
    """
    try:
        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        translation_id = data.get('translationId')
        action = data.get('action')
        
        # Validate required parameters
        if not translation_id:
            return jsonify({"error": "translationId is required"}), 400
        
        if action not in ['star', 'unstar']:
            return jsonify({"error": "action must be 'star' or 'unstar'"}), 400
        
        sheets_service = get_sheets_service()
        if not sheets_service:
            return jsonify({
                "error": "Sheets service not available",
                "timestamp": get_current_timestamp()
            }), 500
        
        # Update star count based on action
        if action == 'star':
            new_count = sheets_service.increment_star_count(translation_id)
        else:  # unstar
            new_count = sheets_service.decrement_star_count(translation_id)
        
        return jsonify({
            "translationId": translation_id,
            "action": action,
            "totalStars": new_count,
            "timestamp": get_current_timestamp()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to update star count: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500


# Interest Tracking Routes

@api.route('/interest', methods=['POST'])
def track_interest():
    """
    Track user interest by creating individual records for each click
    
    Expected request body:
    {
        "what": "images" | "websites" | any content type,
        "timestamp": "2025-01-15T10:30:45.123Z"  // optional
    }
    
    Returns:
        JSON response with success status and record details
    """
    try:
        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        what = data.get('what')
        timestamp = data.get('timestamp', get_current_timestamp())
        
        # Validate required parameters
        if not what:
            return jsonify({"error": "what is required"}), 400
        
        # Validate that what is not empty after trimming
        what = what.strip()
        if not what:
            return jsonify({"error": "what cannot be empty"}), 400
        
        sheets_service = get_sheets_service()
        if not sheets_service:
            return jsonify({
                "error": "Sheets service not available",
                "timestamp": get_current_timestamp()
            }), 500
        
        # Add the interest record (creates a new row for each click)
        record_id = sheets_service.add_interest_record(what, timestamp)
        
        return jsonify({
            "success": True,
            "message": "Interest recorded successfully",
            "recordId": record_id,
            "what": what,
            "timestamp": timestamp
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to record interest: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500


# Feedback Routes

@api.route('/feedback', methods=['POST'])
def submit_feedback():
    """
    Submit feedback to the Google Sheets feedback sub-sheet
    
    Expected request body:
    {
        "text": "User feedback text here"
    }
    
    Returns:
        JSON response with success status and feedback details
    """
    try:
        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        feedback_text = data.get('text')
        
        # Validate required parameters
        if not feedback_text:
            return jsonify({"error": "text is required"}), 400
        
        # Validate that text is not empty after trimming
        feedback_text = feedback_text.strip()
        if not feedback_text:
            return jsonify({"error": "text cannot be empty"}), 400
        
        sheets_service = get_sheets_service()
        if not sheets_service:
            return jsonify({
                "error": "Sheets service not available",
                "timestamp": get_current_timestamp()
            }), 500
        
        # Add feedback to the Google Sheet
        success = sheets_service.add_feedback(feedback_text)
        
        if success:
            return jsonify({
                "success": True,
                "message": "Feedback submitted successfully",
                "feedback": {
                    "text": feedback_text,
                    "timestamp": get_current_timestamp()
                }
            })
        else:
            return jsonify({
                "error": "Failed to submit feedback to database",
                "timestamp": get_current_timestamp()
            }), 500
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to submit feedback: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500

