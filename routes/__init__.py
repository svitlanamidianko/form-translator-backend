from flask import Blueprint

# Create the blueprint
api = Blueprint('api', __name__)

# Import routes AFTER blueprint creation
from . import api_routes 