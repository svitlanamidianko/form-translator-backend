from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import datetime
from dotenv import load_dotenv
from routes import api  # Import the api blueprint

load_dotenv()  # Load environment variables from .env file

app = Flask(__name__)
CORS(app)  

# Register the blueprint without the /api/v1 prefix
app.register_blueprint(api)

# Configuration
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production') 
    DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'
    FLASK_ENV = os.environ.get('FLASK_ENV', 'development')

app.config.from_object(Config)

# Routes
@app.route('/')
def home():
    return jsonify({
        "message": "Welcome to the API",
        "version": "1.0.2",
        "status": "running", 
        "timestamp": datetime.datetime.now().isoformat()
    })

# All API routes are handled by the blueprint in routes/api_routes.py

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({
        "error": "Resource not found",
        "status_code": 404
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "error": "Internal server error",
        "status_code": 500
    }), 500

@app.errorhandler(400)
def bad_request_error(error):
    return jsonify({
        "error": "Bad request",
        "status_code": 400
    }), 400

if __name__ == '__main__':
    # Get port from environment variable (for production) or use default for localhost
    port = int(os.environ.get('PORT', 7777))
    debug_mode = app.config['DEBUG']
    
    print(f"🚀 Starting Flask app on port {port}")
    print(f"🔧 Debug mode: {debug_mode}")
    print(f"🌍 Environment: {app.config['FLASK_ENV']}")
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
