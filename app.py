"""
Main application file for FreePBX Dashboard
Includes Azure AD authentication, audit logging, and database support
"""

from flask import Flask, render_template, redirect, url_for, session
import logging
import os

# Load .env first so config picks up environment variables
from dotenv import load_dotenv
load_dotenv()  # Looks for .env in current directory


# Import configuration
from config import get_config

# Import database
from models import db, init_db

# Import blueprints
from blueprints.api_core import api_core_bp
from blueprints.extensions import extensions_bp
from blueprints.cdr import cdr_bp
from blueprints.wireguard import wireguard_bp
from blueprints.ai_agent import ai_agent_bp
from blueprints.tg100 import tg100_bp
from blueprints.auth import auth_bp, login_required
from blueprints.admin import admin_bp

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

    # Initialize database
    init_db(app)

    # Initialize SSH manager
    init_ssh_manager(app)

    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(api_core_bp, url_prefix='/api')
    app.register_blueprint(extensions_bp, url_prefix='/extensions')
    app.register_blueprint(cdr_bp, url_prefix='/cdr')
    app.register_blueprint(wireguard_bp, url_prefix='/wireguard')
    app.register_blueprint(ai_agent_bp, url_prefix='/ai_agent')
    app.register_blueprint(tg100_bp, url_prefix='/tg100')
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Root route - require login
    @app.route('/')
    @login_required
    def index():
        """Dashboard home page"""
        return render_template('index.html')

    # Health check - no auth required
    @app.route('/health')
    def health():
        return {'status': 'ok', 'message': 'FreePBX Dashboard is running'}

    # API test endpoint
    @app.route('/api/test')
    @login_required
    def api_test():
        """Test API connection"""
        try:
            return {
                'status': 'success',
                'message': 'API is operational',
                'authenticated': True,
                'user': session.get('user', {}).get('email')
            }
        except Exception as e:
            logger.error(f"API test error: {e}")
            return {
                'status': 'error',
                'message': str(e)
            }, 500

    # SSH connection test endpoint
    @app.route('/api/ssh-test')
    @login_required
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

    # Error handlers
    @app.errorhandler(401)
    def unauthorized(e):
        """Redirect to login page for unauthorized access"""
        if '/api/' in str(e):
            return {'error': 'Unauthorized', 'message': 'Please log in'}, 401
        return redirect(url_for('auth.login_page'))

    @app.errorhandler(403)
    def forbidden(e):
        """Handle forbidden access"""
        if '/api/' in str(e):
            return {'error': 'Forbidden', 'message': 'Insufficient permissions'}, 403
        # Check if error template exists, otherwise use simple message
        try:
            return render_template('errors/403.html'), 403
        except:
            return render_template('403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        """Handle not found errors"""
        if '/api/' in str(e):
            return {'error': 'Not found'}, 404
        # Check if error template exists, otherwise use simple message
        try:
            return render_template('errors/404.html'), 404
        except:
            return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_error(e):
        """Handle internal server errors"""
        logger.error(f"Internal error: {str(e)}")
        if '/api/' in str(e):
            return {'error': 'Internal server error'}, 500
        # Check if error template exists, otherwise use simple message
        try:
            return render_template('errors/500.html'), 500
        except:
            return render_template('500.html'), 500

    # Context processor to make user available in all templates
    @app.context_processor
    def inject_user():
        """Make user available in all templates"""
        return {
            'current_user': session.get('user'),
            'is_authenticated': 'user' in session
        }

    return app


# For running directly with python app.py
if __name__ == '__main__':
    app = create_app()
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=app.config['DEBUG']
    )