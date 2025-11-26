"""
Main application file for FreePBX Dashboard
"""

from flask import Flask, render_template, redirect, url_for
import logging
import os

# Import configuration
from config import get_config

# Import blueprints
from blueprints.api_core import api_core_bp
from blueprints.extensions import extensions_bp
from blueprints.cdr import cdr_bp

def create_app(config_name=None):
    """Application factory"""
    app = Flask(__name__)

    # Load configuration
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    app.config.from_object(get_config(config_name))

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, app.config['LOG_LEVEL']),
        format=app.config['LOG_FORMAT']
    )

    logger = logging.getLogger(__name__)
    logger.info(f"Starting FreePBX Dashboard in {config_name} mode")
    logger.info(f"FreePBX Host: {app.config['FREEPBX_HOST']}")
    logger.info(f"SSH User: {app.config['FREEPBX_SSH_USER']}")
    logger.info(f"Using SSH Password: {'Yes' if app.config['FREEPBX_SSH_PASSWORD'] else 'No'}")

    # Register blueprints
    app.register_blueprint(api_core_bp, url_prefix='/api')
    app.register_blueprint(extensions_bp, url_prefix='/extensions')
    app.register_blueprint(cdr_bp, url_prefix='/cdr')

    # Root route
    @app.route('/')
    def index():
        """Dashboard home page"""
        return render_template('index.html')

    # Health check
    @app.route('/health')
    def health():
        return {'status': 'ok', 'message': 'FreePBX Dashboard is running'}

    return app


# For running directly with python app.py
if __name__ == '__main__':
    app = create_app()
    app.run(
        host='10.200.200.1',  # Bind specifically to WireGuard IP
        port=5000,
        debug=app.config['DEBUG']
    )