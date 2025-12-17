"""
Main application file for FreePBX Dashboard
FIXED: AI Agent logging endpoints bypass authentication
ADDED: Service monitoring with Teams notifications
FIXED: Import paths for service_monitor
"""

from flask import Flask, render_template, redirect, url_for, session, request, jsonify
import logging
import os

# Load .env first so config picks up environment variables
from dotenv import load_dotenv
load_dotenv()

from config import get_config
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

from ssh_manager import init_ssh_manager, ssh_manager
from audit_utils import init_log_cleanup
# FIX: Import from blueprints directory
from blueprints.service_monitor import init_service_monitor


def create_app(config_name=None):
    """Application factory"""
    app = Flask(__name__)

    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    app.config.from_object(get_config(config_name))

    logging.basicConfig(
        level=getattr(logging, app.config['LOG_LEVEL']),
        format=app.config['LOG_FORMAT']
    )

    logger = logging.getLogger(__name__)
    logger.info(f"Starting FreePBX Dashboard in {config_name} mode")

    init_db(app)
    init_ssh_manager(app)
    init_log_cleanup(app)

    # Initialize service monitoring with Teams notifications (if enabled)
    if app.config.get('ENABLE_TEAMS_NOTIFICATIONS') and app.config.get('TEAMS_WEBHOOK_URL'):
        try:
            init_service_monitor(app, app.config['TEAMS_WEBHOOK_URL'])
            logger.info("✅ Service monitoring with Teams notifications enabled")
        except Exception as e:
            logger.error(f"❌ Failed to initialize service monitoring: {e}")
    else:
        if not app.config.get('TEAMS_WEBHOOK_URL'):
            logger.info("⚠️  Teams notifications disabled - TEAMS_WEBHOOK_URL not configured")
        else:
            logger.info("ℹ️  Teams notifications disabled - ENABLE_TEAMS_NOTIFICATIONS=false")

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

    @app.before_request
    def require_authentication():
        """Require authentication for all routes except public ones"""
        # Public endpoints (no auth required)
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

        # Allow static files
        if request.path.startswith('/static/'):
            return None

        # CRITICAL FIX: Allow AI Agent logging endpoints without authentication
        # These are internal service-to-service calls from the agent.py script
        if request.path.startswith('/api/ai-agent-logs/'):
            logger.debug(f"AI Agent logging endpoint bypassing auth: {request.path}")
            return None

        # Handle AI Agent UI API routes (require auth)
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

    @app.route('/')
    def index():
        """Dashboard home page"""
        return render_template('index.html')

    @app.route('/health')
    def health():
        return {'status': 'ok', 'message': 'FreePBX Dashboard is running'}

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
            return {'status': 'error', 'message': str(e)}, 500

    @app.route('/api/ssh-test')
    def ssh_test():
        """Test SSH connection"""
        try:
            if ssh_manager.test_connection():
                uptime_output = ssh_manager.execute_command('uptime -p', timeout=5)
                uptime = uptime_output.strip() if uptime_output else 'Unknown'
                if uptime.startswith('up '):
                    uptime = uptime[3:]

                return {
                    'status': 'success',
                    'message': 'SSH connection successful',
                    'uptime': uptime
                }
            else:
                return {'status': 'error', 'message': 'SSH connection failed'}, 500
        except Exception as e:
            logger.error(f"SSH test error: {e}")
            return {'status': 'error', 'message': str(e)}, 500

    @app.route('/api/wireguard-status')
    def wireguard_status():
        """Check WireGuard VPN status"""
        try:
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
        if request.path.startswith('/api/') or '/api/' in request.path:
            return jsonify({'error': 'Unauthorized', 'message': 'Please log in', 'success': False}), 401
        return redirect(url_for('auth.login_page'))

    @app.errorhandler(403)
    def forbidden(e):
        if request.path.startswith('/api/') or '/api/' in request.path:
            return jsonify({'error': 'Forbidden', 'message': 'Insufficient permissions', 'success': False}), 403
        try:
            return render_template('errors/403.html'), 403
        except:
            return render_template('403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith('/api/') or '/api/' in request.path:
            return jsonify({'error': 'Not found', 'success': False}), 404
        try:
            return render_template('errors/404.html'), 404
        except:
            return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_error(e):
        logger.error(f"Internal error: {str(e)}")
        if request.path.startswith('/api/') or '/api/' in request.path:
            return jsonify({'error': 'Internal server error', 'success': False}), 500
        try:
            return render_template('errors/500.html'), 500
        except:
            return render_template('500.html'), 500

    @app.context_processor
    def inject_user():
        return {
            'current_user': session.get('user'),
            'is_authenticated': 'user' in session
        }

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=app.config['DEBUG'])