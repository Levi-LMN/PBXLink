"""
FreePBX Management Dashboard - Main Application
Multi-module dashboard with CDR and Extensions management
"""

from flask import Flask, render_template
import os
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def create_app():
    """Application factory pattern"""
    app = Flask(__name__, static_folder='static', template_folder='templates')

    # Configuration
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['FREEPBX_HOST'] = os.getenv('FREEPBX_HOST', 'http://192.168.0.140')
    app.config['FREEPBX_CLIENT_ID'] = os.getenv('FREEPBX_CLIENT_ID')
    app.config['FREEPBX_CLIENT_SECRET'] = os.getenv('FREEPBX_CLIENT_SECRET')

    # Register blueprints
    from blueprints.cdr import cdr_bp
    from blueprints.extensions import extensions_bp
    from blueprints.api_core import api_core_bp

    app.register_blueprint(cdr_bp, url_prefix='/cdr')
    app.register_blueprint(extensions_bp, url_prefix='/extensions')
    app.register_blueprint(api_core_bp, url_prefix='/api')

    # Main routes
    @app.route('/')
    def index():
        """Dashboard home page"""
        return render_template('index.html')

    @app.route('/health')
    def health():
        """Health check endpoint"""
        return {'status': 'healthy', 'message': 'FreePBX Dashboard is running'}, 200

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.getenv('PORT', 5000))

    # Check configuration
    if not app.config['FREEPBX_CLIENT_ID'] or not app.config['FREEPBX_CLIENT_SECRET']:
        logger.error("FreePBX API credentials not configured!")
        logger.error("Set FREEPBX_HOST, FREEPBX_CLIENT_ID, and FREEPBX_CLIENT_SECRET in .env")
    else:
        logger.info(f"FreePBX Host: {app.config['FREEPBX_HOST']}")
        logger.info("API credentials configured ✓")

    app.run(debug=True, host='0.0.0.0', port=port)