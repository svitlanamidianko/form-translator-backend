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
    SECRET_KEY = os.environ.get('SECRET_KEY') 
    DEBUG =  os.environ.get('DEBUG') 

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

# Example CRUD endpoints
@app.route('/api/items', methods=['GET']) # this is what is called. 
def get_items():
    # Example response
    items = [
        {"id": 1, "name": "Item 1"},
        {"id": 2, "name": "Item 2"}
    ]
    return jsonify(items)

@app.route('/api/items', methods=['POST'])
def create_item():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    # Process the data here
    return jsonify({
        "message": "Item created",
        "data": data
    }), 200

@app.route('/api/items/<int:item_id>', methods=['GET']) #api/items/1
def get_item(item_id):
    # Example response
    return jsonify({"id": item_id, "name": f"Item {item_id}"})

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
    app.run(host='0.0.0.0', port=7777, debug=True)
