from flask import jsonify, request
from . import api
import datetime
import os
import sys
import threading
import time
from werkzeug.exceptions import RequestEntityTooLarge
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sheets_service import get_sheets_service, initialize_sheets_service, normalize_form_name, clean_form_name

# All OpenAI calls go through openai_service (model + settings come from env vars)
from openai_service import chat_completion, describe_openai_error, parse_json_object, edit_image, is_service_side_error
import forms_catalog
import image_service

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
# (tests and the local fake server set FT_SKIP_SHEETS_INIT=1, so no real Google login happens)
if os.environ.get('FT_SKIP_SHEETS_INIT', '').strip().lower() in ('1', 'true', 'yes'):
    print("⏭️ Skipping Google Sheets initialization (FT_SKIP_SHEETS_INIT is set)")
else:
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


def get_form_rows(raise_errors: bool = False):
    """
    Get ALL rows of the "forms" tab (including visitor-added and hidden forms), in sheet order.
    Each row is a dict: form_id, form_name, ukrainian_name, description, group, user_submitted, row_number
    (see the top of sheets_service.py for what each sheet column means)
    
    Args:
        raise_errors: raise the real error instead of returning None when the sheet can't be read
        
    Returns:
        list of row dicts, or None if the sheet could not be read
    """
    try:
        # Wait for sheets service to be initialized
        if not wait_for_sheets_initialization():
            reason = _sheets_initialization_status.get('error') or "initialization timed out"
            raise RuntimeError(f"Google Sheets is not available ({reason})")
            
        sheets_service = get_sheets_service()
        if not sheets_service:
            raise RuntimeError("Google Sheets service not available")
        
        rows = sheets_service.get_all_forms("forms", include_user_submitted=True, raise_errors=True)
        print(f"✅ Loaded {len(rows)} form rows from Google Sheet")
        return rows
        
    except Exception as e:
        print(f"❌ Error loading forms from sheet: {str(e)}")
        if raise_errors:
            raise
        return None


def run_in_background(target, *args):
    """Run a function in a background thread (tests swap this for a direct call)"""
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


# Visitor-added ("custom") forms are written to the sheet in a background thread, so the
# translation response never waits for Google Sheets or for the group-picking AI call.
# _pending_custom_forms remembers names that are being added right now, so two quick requests
# for "Gen Z" and "gen z " can't both add a row (like a set + a lock in any Python script).
_custom_forms_lock = threading.Lock()
_pending_custom_forms = set()

# Very long "names" are usually pasted text, not form names - we still translate, but don't save them
MAX_CUSTOM_FORM_NAME_LENGTH = 80


def queue_custom_form(form_name: str) -> bool:
    """
    Save a visitor's custom form to the sheet in the background (with an automatic semantic group).
    
    Returns:
        bool: True if a background job was started
    """
    name = clean_form_name(form_name)
    key = normalize_form_name(name)
    
    if not key or forms_catalog.is_reserved_form_name(name):
        print(f"⏭️ Not saving reserved/empty form name '{form_name}'")
        return False
    if len(name) > MAX_CUSTOM_FORM_NAME_LENGTH:
        print(f"⏭️ Not saving a custom form name longer than {MAX_CUSTOM_FORM_NAME_LENGTH} characters")
        return False
    
    with _custom_forms_lock:
        if key in _pending_custom_forms:
            print(f"⏳ Custom form '{name}' is already being added")
            return False
        _pending_custom_forms.add(key)
    
    try:
        run_in_background(_add_custom_form_in_background, name, key)
    except Exception as e:
        print(f"⚠️ Could not start background job for custom form '{name}': {str(e)}")
        with _custom_forms_lock:
            _pending_custom_forms.discard(key)
        return False
    return True
            

def _add_custom_form_in_background(name: str, key: str):
    """Background job: pick a group (fail soft -> "") and append id | name | "" | "" | group | TRUE"""
    try:
        sheets_service = get_sheets_service()
        rows = sheets_service.get_all_forms("forms", include_user_submitted=True, raise_errors=True)
        
        # Maybe it was added in the meantime (another request, or by hand)
        if forms_catalog.find_form(rows, name):
            print(f"✅ Custom form '{name}' already exists, nothing to add")
            return
        
        # One cheap AI call - returns "" on any error, "hidden" for nasty names
        group = forms_catalog.pick_semantic_group(name, forms_catalog.distinct_groups(rows))
        
        if sheets_service.add_custom_form(form_name=name, group=group):
            print(f"✅ Added custom form '{name}' (group: '{group}')")
        else:
            print(f"⚠️ Failed to add custom form '{name}'")
    except Exception as e:
        print(f"⚠️ Error adding custom form '{name}' in background: {str(e)}")
    finally:
        with _custom_forms_lock:
            _pending_custom_forms.discard(key)
        

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



# Answer budgets for the reasoning model (they include hidden reasoning tokens, so keep some headroom)
TRANSLATION_MAX_COMPLETION_TOKENS = 3000
DETECTION_MAX_COMPLETION_TOKENS = 2000

# How many target forms /detect-form suggests for the right-hand side
MAX_SUGGESTED_FORMS = 3


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


@api.route('/translate', methods=['POST'])
def translate_form():

    try:
        # Get request data (silent=True: broken JSON -> None -> a clear 400 below)
        data = request.get_json(silent=True)
        print(f"🔍 DEBUG: Received request data: {data}")
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Extract parameters
        source_form = data.get('sourceForm')
        target_form = data.get('targetForm')
        # Accept both 'sourceText' and 'inputText' for backward compatibility
        source_text = data.get('sourceText') or data.get('inputText')
        
        # Validate required parameters
        if not all([source_form, target_form, source_text]):
            return jsonify({
                "error": "Missing required parameters. Need: sourceForm, targetForm, sourceText (or inputText)"
            }), 400
        
        # DEBUG: Log the translation request parameters
        print(f"🔍 DEBUG: Translation request - sourceForm: '{source_form}', targetForm: '{target_form}'")
        print(f"🔍 DEBUG: Translation request - sourceText: '{source_text[:50]}...' (truncated)")
        
        # Get all forms from the sheet (including visitor-added and hidden ones)
        form_rows = get_form_rows()
        
        # Find the forms, ignoring letter case and extra spaces ("Gen Z " == "gen z")
        source_row = forms_catalog.find_form(form_rows or [], source_form)
        target_row = forms_catalog.find_form(form_rows or [], target_form)
            
        # Handle custom forms - they are saved to the sheet in the background (with an automatic group).
        # If the sheet couldn't be read at all we skip this, so we never create duplicates.
        if form_rows is not None:
            if source_row is None:
                print(f"🔍 DEBUG: Source form '{source_form}' not found in existing forms, saving it as a custom form")
                queue_custom_form(source_form)
            if target_row is None:
                print(f"🔍 DEBUG: Target form '{target_form}' not found in existing forms, saving it as a custom form")
                queue_custom_form(target_form)
        
        # Check if source and target are the same
        if normalize_form_name(source_form) == normalize_form_name(target_form):
            return jsonify({
                "translatedText": source_text,
                "message": "Source and target forms are identical",
                "timestamp": get_current_timestamp()
            })
        
        # Get the prompt template from Google Sheets
        prompt_template = get_prompt_from_sheet()
        
        # Get form descriptions (her English style description from column D when there is one)
        source_description = forms_catalog.prompt_description(source_form, source_row)
        target_description = forms_catalog.prompt_description(target_form, target_row)
        
        # Format the prompt with the actual values including form names
        prompt = prompt_template.format(
            source_form=source_form,
            source_description=source_description,
            target_form=target_form,
            target_description=target_description,
            source_text=source_text
        )

        # Make OpenAI API call (model comes from the OPENAI_MODEL env var)
        translated_text = chat_completion(
            [
                {"role": "system", "content": "You are an expert in transforming text between different forms of expression while preserving meaning."},
                {"role": "user", "content": prompt}
            ],
            max_completion_tokens=TRANSLATION_MAX_COMPLETION_TOKENS
        )
        
        # Remove unwanted quotation marks from the beginning and end
        # This handles both single and double quotes that OpenAI sometimes adds
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
        # describe_openai_error gives the real reason, e.g. "OpenAI API error 404: The model ... does not exist"
        reason = describe_openai_error(e)
        print(f"❌ Translation failed: {reason}")
        return jsonify({
            "error": f"Translation failed: {reason}",
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
    - alternativeForms: Other forms the text might be using
    - suggestedForms: Up to 3 forms from the sheet that would pair well as translation targets
      (e.g. a religious text -> other religions), never the detected form
    """
    try:
        # Get request data
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        text = data.get('text')
        if not text or not isinstance(text, str):
            return jsonify({"error": "text is required"}), 400
        
        # Validate that text is not empty after trimming
        text = text.strip()
        if not text:
            return jsonify({"error": "text cannot be empty"}), 400
        
        # Get all forms from the database (raises with the real reason if the sheet can't be read)
        form_rows = get_form_rows(raise_errors=True)
        
        # The forms the website shows (de-duplicated, hidden ones left out, visitor-added ones included)
        visible_forms, _groups = forms_catalog.build_forms_payload(form_rows)
        
        if not visible_forms:
            return jsonify({
                "error": "No forms available in database for detection"
            }), 500
        
        # Build the detection prompt
        forms_list = []
        for form_name in visible_forms.keys():
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
8. Suggest the {MAX_SUGGESTED_FORMS} forms from the list above that would pair best with this text as translation targets: forms that speak about the same things in a different ontology, so the translation is meaningful and surprising. For example, a religious or spiritual text pairs with other religions and spiritual traditions; a scientific text pairs with other sciences, philosophies or ways of knowing. Use exact names from the list and never the detected form.

Respond in this exact JSON format:
{{
    "detectedForm": "form_name_here",
    "reasoning": "Explain why this form was detected - focus on the ontological framework, not the input text (keep to exactly two sentences)",
    "isCustomForm": true/false,
    "alternativeForms": ["form1", "form2"] (if multiple ontological frameworks detected),
    "suggestedForms": ["form1", "form2", "form3"] (exactly as named in the list)
}}

If no clear ontological framework is detected, use "neutral" and explain why."""

        # Make OpenAI API call for form detection (model comes from the OPENAI_MODEL env var)
        detection_result = chat_completion(
            [
                {"role": "system", "content": "You are a Form Detection expert who understands that different forms are ontological frameworks for expressing meaning. You identify which conceptual system a text uses. Always respond with valid JSON."},
                {"role": "user", "content": detection_prompt}
            ],
            max_completion_tokens=DETECTION_MAX_COMPLETION_TOKENS
        )
        
        # DEBUG: Log the raw AI response
        print(f"🔍 DEBUG: Raw AI detection response: {detection_result}")
        
        # Parse the JSON response (also handles ```json ... ``` fences and extra words around it)
        try:
            detection_data = parse_json_object(detection_result)
            print(f"🔍 DEBUG: Parsed detection data: {detection_data}")
        except ValueError as e:
            print(f"🔍 DEBUG: JSON parsing failed: {e}")
            # If JSON parsing fails, create a fallback response
            detection_data = {
                "detectedForm": "neutral",
                "reasoning": "Unable to parse AI response",
                "isCustomForm": True,
                "alternativeForms": [],
                "suggestedForms": []
            }
        
        # Validate and clean the response
        detected_form = clean_form_name(detection_data.get('detectedForm') or 'neutral') or 'neutral'
        reasoning = detection_data.get('reasoning') or 'No reasoning provided'
        alternative_forms = detection_data.get('alternativeForms') or []
        if not isinstance(alternative_forms, list):
            alternative_forms = []
        
        # DEBUG: Log the detected form before processing
        print(f"🔍 DEBUG: Detected form before processing: '{detected_form}'")
        print(f"🔍 DEBUG: Available form types: {list(visible_forms.keys())}")
        
        # Check if the detected form is one of the forms the website lists (ignoring case / extra spaces)
        visible_by_key = {normalize_form_name(name): name for name in visible_forms}
        if normalize_form_name(detected_form) in visible_by_key:
            is_custom_form = False
            # Use the exact name from the sheet, so the website can select it in the dropdown
            detected_form = visible_by_key[normalize_form_name(detected_form)]
            print(f"🔍 DEBUG: Form '{detected_form}' exists in database, not custom")
        else:
            is_custom_form = True
            print(f"🔍 DEBUG: Form '{detected_form}' not in database, is custom")
        
        # Suggested targets: only forms the website lists (exact sheet spelling), never the detected form, no repeats
        suggested_forms = []
        raw_suggestions = detection_data.get('suggestedForms')
        for name in raw_suggestions if isinstance(raw_suggestions, list) else []:
            key = normalize_form_name(name) if isinstance(name, str) else ''
            sheet_name = visible_by_key.get(key)
            if not sheet_name or key == normalize_form_name(detected_form) or sheet_name in suggested_forms:
                continue
            suggested_forms.append(sheet_name)
            if len(suggested_forms) == MAX_SUGGESTED_FORMS:
                break
        
        # If it's a new custom form, add it to the database (in the background; never "neutral", never twice)
        if forms_catalog.is_reserved_form_name(detected_form):
            print(f"🔍 DEBUG: Skipping database addition for '{detected_form}' form")
        elif is_custom_form and not forms_catalog.find_form(form_rows, detected_form):
            print(f"🔍 DEBUG: Adding custom form '{detected_form}' to database")
            queue_custom_form(detected_form)
        else:
            print(f"🔍 DEBUG: Skipping database addition for existing form '{detected_form}'")
        
        # DEBUG: Log the final response
        print(f"🔍 DEBUG: Final response - detectedForm: '{detected_form}', isCustomForm: {is_custom_form}")
        
        return jsonify({
            "detectedForm": detected_form,
            "reasoning": reasoning,
            "isCustomForm": is_custom_form,
            "alternativeForms": alternative_forms,
            "suggestedForms": suggested_forms
        })
        
    except Exception as e:
        reason = describe_openai_error(e)
        print(f"❌ Form detection failed: {reason}")
        return jsonify({
            "error": f"Form detection failed: {reason}"
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
    """
    Get the forms for the website's dropdowns, in sheet order, with their semantic groups.
    - duplicates (ignoring case / extra spaces) are shown once
    - forms with "hidden" in column E are left out
    - visitor-added forms are included (userSubmitted: true)
    
    Returns:
    {
        "forms": {"<name>": {"description": str, "category": str|None, "userSubmitted": bool, "order": int}},
        "groups": ["<group>", ...],   # in order of first appearance in the sheet
        "count": int, "source": "Google Sheet", "timestamp": str
    }
    """
    try:
        form_rows = get_form_rows(raise_errors=True)
        form_types, groups = forms_catalog.build_forms_payload(form_rows)
        print(f"✅ /forms: {len(form_types)} forms in {len(groups)} groups")
        return jsonify({
            "forms": form_types,
            "groups": groups,
            "count": len(form_types),
            "source": "Google Sheet",
            "timestamp": get_current_timestamp()
        })
    except Exception as e:
        return jsonify({
            "error": f"Failed to load forms from Google Sheet: {str(e)}",
            "timestamp": get_current_timestamp()
        }), 500


# Photo "translation" route

# Form names for photos can be custom, but not essays (they go into two AI prompts)
MAX_IMAGE_FORM_NAME_LENGTH = 200


def _count_images_converted_today(day) -> int:
    """
    How many photo translations the interest_registered tab already has for this UTC day.
    The rate limiter starts from this number after a restart (Fly stops idle machines, which wipes memory).
    """
    if not wait_for_sheets_initialization():
        raise RuntimeError("Google Sheets is not available")
    sheets_service = get_sheets_service()
    timestamps = sheets_service.get_interest_timestamps("image_converted")
    return image_service.count_on_utc_day(timestamps, day)


def _rate_limited_response(client_id: str, decision):
    print(f"🚦 Image rate limit for {client_id}: {decision.message}")
    return jsonify({
        "error": decision.message,
        "retryAfterSeconds": decision.retry_after_seconds
    }), 429


def _record_image_interest():
    """Count a successful photo translation in the interest_registered tab (best-effort)"""
    try:
        sheets_service = get_sheets_service()
        if sheets_service:
            sheets_service.add_interest_record("image_converted", get_current_timestamp())
    except Exception as e:
        print(f"⚠️ Could not record image_converted interest: {str(e)}")


def _is_moderation_block(error: Exception) -> bool:
    """True when OpenAI refused the image because of its safety system"""
    text = describe_openai_error(error).lower()
    code = str(getattr(error, 'code', '') or '').lower()
    return getattr(error, 'status_code', None) == 400 and (
        'moderation' in code or 'safety' in text or 'moderation' in text
    )


@api.route('/translate-image', methods=['POST'])
def translate_image():
    """
    "Translate" a photo into a form (like text translation, but for pictures)
    
    Expected request: multipart/form-data with
        image:      the photo (JPEG / PNG / WEBP, max 10 MB)
        targetForm: the form to translate into (can be a custom form name)
        sourceForm: optional, just for context
    
    Returns:
    {
        "imageBase64": "<base64 jpeg>", "mimeType": "image/jpeg",
        "targetForm": "<name>", "timestamp": "<iso>"
    }
    Errors: 400 bad input, 413 too large, 429 rate limited (+ retryAfterSeconds), 500 with the real reason.
    Nothing is stored - the photo only lives in memory while this request runs.
    """
    client_id = None
    slot_taken = False
    try:
        # Reading request.files can raise RequestEntityTooLarge (Flask's MAX_CONTENT_LENGTH)
        try:
            image_file = request.files.get('image')
            target_form = clean_form_name(request.form.get('targetForm'))
            source_form = clean_form_name(request.form.get('sourceForm'))
        except RequestEntityTooLarge:
            return jsonify({"error": "Image is too large. The maximum size is 10 MB."}), 413
        
        if image_file is None:
            return jsonify({"error": "image is required (multipart/form-data field 'image')"}), 400
        if not target_form:
            return jsonify({"error": "targetForm is required"}), 400
        if forms_catalog.is_reserved_form_name(target_form):
            return jsonify({"error": f"'{target_form}' can't be used as a target form"}), 400
        if len(target_form) > MAX_IMAGE_FORM_NAME_LENGTH or len(source_form) > MAX_IMAGE_FORM_NAME_LENGTH:
            return jsonify({"error": f"Form names can be at most {MAX_IMAGE_FORM_NAME_LENGTH} characters long"}), 400
        
        # Cost protection, part 1: is this visitor (or the whole site) already over the limit?
        # We only LOOK here (nothing is counted yet), BEFORE touching the photo,
        # so a blocked visitor can't make the server do any heavy work.
        client_id = image_service.get_client_ip(request.headers, request.remote_addr)
        image_service.image_rate_limiter.seed_daily_count(_count_images_converted_today)
        decision = image_service.image_rate_limiter.check(client_id)
        if not decision.allowed:
            return _rate_limited_response(client_id, decision)
        
        # Read at most 10 MB + 1 byte, so we can tell "too big" without loading a huge file
        image_bytes = image_file.read(image_service.MAX_IMAGE_BYTES + 1)
        if len(image_bytes) > image_service.MAX_IMAGE_BYTES:
            return jsonify({"error": "Image is too large. The maximum size is 10 MB."}), 413
        if not image_bytes:
            return jsonify({"error": "The uploaded image is empty"}), 400
        
        # Check what the file REALLY is (first bytes), not what its name says
        mime_type = image_service.detect_image_mime(image_bytes)
        if mime_type is None:
            return jsonify({"error": "Unsupported file type. Please upload a JPEG, PNG or WEBP image."}), 400
        
        # Decode, fix rotation, shrink, re-encode (raises InvalidImageError for broken files)
        try:
            prepared = image_service.prepare_image(image_bytes, mime_type)
        except image_service.InvalidImageError as e:
            return jsonify({"error": str(e)}), 400
        
        # Cost protection, part 2: count this photo translation (per-visitor hourly + site-wide daily limit)
        decision = image_service.image_rate_limiter.try_acquire(client_id)
        if not decision.allowed:
            return _rate_limited_response(client_id, decision)
        slot_taken = True
        
        # Use her English description of the form (column D) when the form is in the sheet
        target_row = forms_catalog.find_form(get_form_rows() or [], target_form)
        style_description = target_row.get('description', '') if target_row else ''
        
        size = image_service.choose_output_size(prepared.width, prepared.height)
        prompt = image_service.build_image_prompt(target_form, style_description, source_form)
        print(f"🖼️ Translating photo ({prepared.width}x{prepared.height} -> {size}) into '{target_form}' for {client_id}")
        
        try:
            image_base64 = edit_image(prepared.jpeg_bytes, prompt, size)
        except Exception as e:
            # If OpenAI failed on its side (down, overloaded, our settings), the try doesn't count.
            # A refused photo (e.g. safety block) DOES count: OpenAI still did work for it,
            # so retrying can't bypass the limits.
            if is_service_side_error(e):
                image_service.image_rate_limiter.release(client_id)
            slot_taken = False
            reason = describe_openai_error(e)
            print(f"❌ Image translation failed: {reason}")
            if _is_moderation_block(e):
                return jsonify({"error": f"This photo couldn't be translated because of the AI's safety rules: {reason}"}), 400
            return jsonify({"error": f"Image translation failed: {reason}"}), 500
        
        # Count it in the interest tab without making the visitor wait
        run_in_background(_record_image_interest)
        
        return jsonify({
            "imageBase64": image_base64,
            "mimeType": "image/jpeg",
            "targetForm": target_form,
            "timestamp": get_current_timestamp()
        })
        
    except Exception as e:
        if slot_taken and client_id:
            image_service.image_rate_limiter.release(client_id)
        reason = describe_openai_error(e)
        print(f"❌ Image translation failed: {reason}")
        return jsonify({
            "error": f"Image translation failed: {reason}",
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

