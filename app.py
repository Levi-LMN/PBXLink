"""
Main application file for FreePBX Dashboard
Includes centralized SSH management, WireGuard VPN management, and AI Agent monitoring
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
from blueprints.wireguard import wireguard_bp
from blueprints.ai_agent import ai_agent_bp  # NEW

# Import SSH manager
from ssh_manager import init_ssh_manager, ssh_manager


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

    # Initialize SSH manager
    init_ssh_manager(app)

    # Register blueprints
    app.register_blueprint(api_core_bp, url_prefix='/api')
    app.register_blueprint(extensions_bp, url_prefix='/extensions')
    app.register_blueprint(cdr_bp, url_prefix='/cdr')
    app.register_blueprint(wireguard_bp, url_prefix='/wireguard')
    app.register_blueprint(ai_agent_bp, url_prefix='/ai_agent')  # NEW

    # Root route
    @app.route('/')
    def index():
        """Dashboard home page"""
        return render_template('index.html')

    # Health check
    @app.route('/health')
    def health():
        return {'status': 'ok', 'message': 'FreePBX Dashboard is running'}

    # SSH connection test endpoint
    @app.route('/api/ssh-test')
    def ssh_test():
        """Test SSH connection"""
        try:
            if ssh_manager.test_connection():
                return {
                    'status': 'success',
                    'message': 'SSH connection successful'
                }
            else:
                return {
                    'status': 'error',
                    'message': 'SSH connection failed'
                }, 500
        except Exception as e:
            logger.error(f"SSH test error: {e}")
            return {
                'status': 'error',
                'message': str(e)
            }, 500

    return app


# For running directly with python app.py
if __name__ == '__main__':
    app = create_app()
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=app.config['DEBUG']
    )