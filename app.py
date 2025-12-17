"""
Main application file for FreePBX Dashboard
Includes Azure AD authentication, audit logging, database support, automatic log cleanup, and AI Agent
FIXED: Proper JSON error handling for API routes and AI Agent status polling
"""

from flask import Flask, render_template, redirect, url_for, session, request, jsonify
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
from blueprints.wireguard import wireguard_bp, wg_manager
from blueprints.tg100 import tg100_bp
from blueprints.auth import auth_bp, login_required
from blueprints.admin import admin_bp
from blueprints.ai_agent import ai_agent_bp
from blueprints.ai_agent_logging import ai_logging_bp



# Import SSH manager
from ssh_manager import init_ssh_manager, ssh_manager

# Import log cleanup system
from audit_utils import init_log_cleanup


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

    # Initialize automatic log cleanup system
    init_log_cleanup(app)
    logger.info("Automatic log cleanup initialized (runs every 24 hours)")

    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(api_core_bp, url_prefix='/api')
    app.register_blueprint(extensions_bp, url_prefix='/extensions')
    app.register_blueprint(cdr_bp, url_prefix='/cdr')
    app.register_blueprint(wireguard_bp, url_prefix='/wireguard')
    app.register_blueprint(tg100_bp, url_prefix='/tg100')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(ai_agent_bp, url_prefix='/ai_agent')
    app.register_blueprint(ai_logging_bp, url_prefix='/api/ai-agent-logs')

    # Global authentication check
    @app.before_request
    def require_authentication():
        """Require authentication for all routes except public ones"""
        # List of public endpoints that don't require authentication
        public_endpoints = {
            'auth.login_page',
            'auth.login',
            'auth.callback',
            'health',
            'static'
        }

        # Check if current endpoint is public
        if request.endpoint in public_endpoints:
            return None

        # Check if accessing static files
        if request.path.startswith('/static/'):
            return None

        # FIXED: Handle AI Agent API routes separately to prevent flickering
        if request.path.startswith('/ai_agent/api/'):
            if 'user' not in session:
                return jsonify({
                    'error': 'Unauthorized',
                    'message': 'Please log in',
                    'success': False
                }), 401
            return None

        # Require authentication for all other routes
        if 'user' not in session:
            # For API routes, return JSON error
            if request.path.startswith('/api/') or '/api/' in request.path:
                return jsonify({
                    'error': 'Unauthorized',
                    'message': 'Please log in',
                    'success': False
                }), 401
            # For web routes, redirect to login
            return redirect(url_for('auth.login_page'))

        return None

    # Root route
    @app.route('/')
    def index():
        """Dashboard home page"""
        return render_template('index.html')

    # Health check - no auth required
    @app.route('/health')
    def health():
        return {'status': 'ok', 'message': 'FreePBX Dashboard is running'}

    # API test endpoint
    @app.route('/api/test')
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
    def ssh_test():
        """Test SSH connection and get uptime"""
        try:
            if ssh_manager.test_connection():
                # Get system uptime
                uptime_output = ssh_manager.execute_command('uptime -p', timeout=5)
                uptime = uptime_output.strip() if uptime_output else 'Unknown'

                # Clean up uptime format (remove "up " prefix if present)
                if uptime.startswith('up '):
                    uptime = uptime[3:]

                return {
                    'status': 'success',
                    'message': 'SSH connection successful',
                    'uptime': uptime
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

    # WireGuard status endpoint
    @app.route('/api/wireguard-status')
    def wireguard_status():
        """Check WireGuard VPN status (local)"""
        try:
            # Use the WireGuardManager's method to check status
            status = wg_manager.get_wireguard_status()

            if status:
                return {
                    'status': 'success',
                    'message': 'Online',
                    'interface': 'wg0',
                    'state': 'active'
                }
            else:
                return {
                    'status': 'error',
                    'message': 'Offline',
                    'interface': 'wg0',
                    'state': 'inactive'
                }

        except Exception as e:
            logger.error(f"WireGuard status check error: {e}")
            return {
                'status': 'error',
                'message': 'Check Failed',
                'error': str(e)
            }, 500

    # Error handlers
    @app.errorhandler(401)
    def unauthorized(e):
        """Redirect to login page for unauthorized access"""
        if request.path.startswith('/api/') or '/api/' in request.path:
            return jsonify({'error': 'Unauthorized', 'message': 'Please log in', 'success': False}), 401
        return redirect(url_for('auth.login_page'))

    @app.errorhandler(403)
    def forbidden(e):
        """Handle forbidden access"""
        if request.path.startswith('/api/') or '/api/' in request.path:
            return jsonify({'error': 'Forbidden', 'message': 'Insufficient permissions', 'success': False}), 403
        try:
            return render_template('errors/403.html'), 403
        except:
            return render_template('403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        """Handle not found errors"""
        if request.path.startswith('/api/') or '/api/' in request.path:
            return jsonify({'error': 'Not found', 'success': False}), 404
        try:
            return render_template('errors/404.html'), 404
        except:
            return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_error(e):
        """Handle internal server errors"""
        logger.error(f"Internal error: {str(e)}")
        if request.path.startswith('/api/') or '/api/' in request.path:
            return jsonify({'error': 'Internal server error', 'success': False}), 500
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