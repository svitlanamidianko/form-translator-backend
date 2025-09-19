from flask import jsonify, request
from . import api
import datetime
import os

# Test mode - set to True to use mock responses instead of OpenAI
TEST_MODE = False

if not TEST_MODE:
    from openai import OpenAI

# Initialize OpenAI client - will be created when needed
client = None

def get_openai_client():
    """Get OpenAI client, creating it if needed"""
    global client
    if TEST_MODE:
        return None  # No client needed in test mode
    
    if client is None:
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        
        # Simple, clean OpenAI client initialization
        try:
            from openai import OpenAI
            # Use the most basic initialization possible
            client = OpenAI(api_key=api_key)
            
        except Exception as e:
            raise ValueError(f"Failed to initialize OpenAI client: {str(e)}")
    
    return client

def get_mock_translation(source_form, target_form, input_text):
    """Generate a mock translation for testing"""
    mock_translations = {
        ('formal', 'poetic'): f"In the realm of verse, thy words become: '{input_text}' transforms into lyrical beauty.",
        ('casual', 'technical'): f"Technical analysis indicates: The informal input '{input_text}' requires systematic processing.",
        ('direct', 'metaphorical'): f"Like a river flowing toward the sea, '{input_text}' becomes a journey of meaning.",
        ('emotional', 'abstract'): f"The philosophical essence of '{input_text}' transcends mere feeling into conceptual reality."
    }
    
    # Use specific mock if available, otherwise create a generic one
    key = (source_form, target_form)
    if key in mock_translations:
        return mock_translations[key]
    else:
        return f"[{target_form.upper()} STYLE] {input_text} [Transformed from {source_form} to {target_form}]"

# Form type definitions - these define the different "forms" or styles we can translate between
FORM_TYPES = {


    'self-hate': 'Self-Hate - Self-deprecating or self-critical expression',
    'engineer': 'Engineer - Technical, precise, problem-solving focused',
    'art': 'Art - Creative, aesthetic, artistic expression',
    'technology': 'Technology - Tech-focused, digital, innovative language',
    'russia\'s frame': 'Russia\'s Frame - Russian cultural or political perspective',
    'ukraine\'s frame': 'Ukraine\'s Frame - Ukrainian cultural or political perspective',
    'jungian': 'Jungian - Carl Jung\'s analytical psychology perspective',
    'freudian': 'Freudian - Psychoanalytic, unconscious-focused perspective',
    'woman\'s': 'Woman\'s - Female perspective or experience',
    'man\'s': 'Man\'s - Male perspective or experience',
    'marginalized voice': 'Marginalized Voice - Expression of being diminished for important inquiries',
    'dismissive response': 'Dismissive Response - Invalidating and deflecting communication style',
    'eli5': 'ELI5 - Explain Like I\'m 5, simple and easy to understand',
    'undergrad': 'Undergraduate - Student-level, learning-focused perspective',
    'tech bro founder': 'Tech Bro Founder - Silicon Valley entrepreneur style',
    'corporate': 'Corporate - Business, professional, institutional language',
    'poetic': 'Poetic - Artistic, metaphorical, lyrical expression',
    'analytical': 'Analytical - Data-driven, logical, systematic approach',
    'silly': 'Silly - Playful, humorous, lighthearted expression',
    'people pleaser': 'People Pleaser - Accommodating, conflict-avoiding communication',
    'boundary queen': 'Boundary Queen - Assertive, limit-setting communication style',
    'non-dualist': 'Non-Dualist - Unity-focused, transcendent perspective',
    'logic': 'Logic - Rational, reasoned, systematic thinking',
    'jhana': 'Jhana - Deep meditative absorption states in Buddhist practice',
    'vipassana': 'Vipassana - Mindfulness, insight meditation perspective',
    'christian': 'Christian - Christian religious or spiritual viewpoint',
    'hinduism': 'Hinduism - Hindu religious or philosophical perspective',
    'woo woo': 'Woo Woo - Spiritual, mystical, new-age expression',
    'science': 'Science - Scientific, empirical, evidence-based approach',
    'stem': 'STEM - Science, Technology, Engineering, Mathematics focused',
    'mystical': 'Mystical - Spiritual, transcendent, and esoteric experiences',
    'jhanas + algorithm': 'Jhanas + Algorithm - Meditative states combined with systematic approaches',
    'affirmations': 'Affirmations - Positive self-talk and empowering statements',
    'collective unconscious': 'Collective Unconscious - Shared universal psychic material and archetypes',
   'robot': 'Robot - Mechanical, artificial, non-emotional communication',
    'nature': 'Nature - Natural, organic, earth-connected expression',
    'stream': 'Stream - Flowing, continuous, stream-of-consciousness style',
    'love': 'Love - Love-focused, affectionate, caring expression', 
    'formal': 'Formal - Professional, structured, and scholarly language',
    'poetic': 'Poetic - Artistic, metaphorical, lyrical expression',
    'yoga teacher': 'Yoga Teacher - Mindful, wellness-focused, spiritual guidance style'
}

@api.route('/test', methods=['GET'])
def test_route():
    return jsonify({
        "message": "Test route working helooo",
        "service": "API Routes Module",
        "timestamp": datetime.datetime.now().isoformat()
    }) 

@api.route('/custom', methods=['GET'])
def custom_route():
    return jsonify({
        "message": "hiiiiii",
        "service": "API Routes Module",
        "timestamp": datetime.datetime.now().isoformat()
    }) 

@api.route('/translate', methods=['POST'])
def translate_form():
    """
    Translate text between different forms/styles using OpenAI
    
    Expected request body:
    {
        "sourceForm": "formal",
        "targetForm": "poetic", 
        "inputText": "Hello, how are you today?"
    }
    """
    try:
        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Extract parameters
        source_form = data.get('sourceForm')
        target_form = data.get('targetForm')
        input_text = data.get('inputText')
        
        # Validate required parameters
        if not all([source_form, target_form, input_text]):
            return jsonify({
                "error": "Missing required parameters. Need: sourceForm, targetForm, inputText"
            }), 400
        
        # Validate form types
        if source_form not in FORM_TYPES:
            return jsonify({
                "error": f"Invalid sourceForm '{source_form}'. Valid options: {list(FORM_TYPES.keys())}"
            }), 400
        
        if target_form not in FORM_TYPES:
            return jsonify({
                "error": f"Invalid targetForm '{target_form}'. Valid options: {list(FORM_TYPES.keys())}"
            }), 400
        
        # Check if source and target are the same
        if source_form == target_form:
            return jsonify({
                "translatedText": input_text,
                "message": "Source and target forms are identical",
                "timestamp": datetime.datetime.now().isoformat()
            })
        
        # Create the prompt for OpenAI
        source_description = FORM_TYPES[source_form]
        target_description = FORM_TYPES[target_form]
        
        prompt = f"""You are a master translator of communication forms and styles. Your task is to transform text from one form of expression to another while preserving the core meaning and intent.

Transform the following text:
- FROM: {source_description}
- TO: {target_description}

Original text: "{input_text}"

Instructions:
1. Preserve the core meaning and intent of the original text
2. Adapt the style, tone, and expression to match the target form
3. Be creative and authentic to the target form while staying true to the original meaning
4. Return only the transformed text, no explanations or additional commentary

Transformed text:"""

        # Make OpenAI API call or use mock response
        if TEST_MODE:
            translated_text = get_mock_translation(source_form, target_form, input_text)
        else:
            openai_client = get_openai_client()
            try:
                # Try new OpenAI client format
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
            except AttributeError:
                # Fallback to older OpenAI format if the client is the module itself
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
        
        return jsonify({
            "translatedText": translated_text,
            "sourceForm": source_form,
            "targetForm": target_form,
            "originalText": input_text,
            "timestamp": datetime.datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            "error": f"Translation failed: {str(e)}",
            "timestamp": datetime.datetime.now().isoformat()
        }), 500

@api.route('/forms', methods=['GET'])
def get_available_forms():
    """Get list of available form types for translation"""
    return jsonify({
        "forms": FORM_TYPES,
        "count": len(FORM_TYPES),
        "timestamp": datetime.datetime.now().isoformat()
    })

@api.route('/createuserentry', methods=['POST'])
def create_user_entry():
    data = request.get_json()
    field_value = data.get('entry')
    
    #if not data:
    #     return jsonify({"error": "No data provided"}), 400

    return jsonify({
        "message": "here is yr" + field_value,
        "service": "API Routes Module",
        "timestamp": datetime.datetime.now().isoformat()
    })

