"""
AI Agent Blueprint - Complete Working Version
"""

from flask import Blueprint, render_template, jsonify, request
import logging
import os

logger = logging.getLogger(__name__)

ai_agent_bp = Blueprint('ai_agent', __name__)

from blueprints.ai_agent_service import get_ai_service

ai_service = get_ai_service()


@ai_agent_bp.route('/')
def index():
    """AI Agent control page"""
    return render_template('ai_agent/index.html')


@ai_agent_bp.route('/api/status', methods=['GET'])
def get_status():
    """Get AI agent service status"""
    try:
        status = ai_service.get_status()

        env_status = {
            'azure_openai_configured': bool(os.environ.get('AZURE_OPENAI_ENDPOINT')),
            'azure_speech_configured': bool(os.environ.get('AZURE_SPEECH_KEY')),
            'dataverse_configured': bool(os.environ.get('DATAVERSE_URL'))
        }

        return jsonify({
            'success': True,
            'status': status,
            'environment': env_status
        })

    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ai_agent_bp.route('/api/start', methods=['POST'])
def start_service():
    """Start AI agent service"""
    try:
        success, message = ai_service.start()

        return jsonify({
            'success': success,
            'message': message
        }), 200 if success else 400

    except Exception as e:
        logger.error(f"Error starting service: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ai_agent_bp.route('/api/stop', methods=['POST'])
def stop_service():
    """Stop AI agent service"""
    try:
        success, message = ai_service.stop()

        return jsonify({
            'success': success,
            'message': message
        }), 200 if success else 400

    except Exception as e:
        logger.error(f"Error stopping service: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ai_agent_bp.route('/api/restart', methods=['POST'])
def restart_service():
    """Restart AI agent service"""
    try:
        success, message = ai_service.restart()

        return jsonify({
            'success': success,
            'message': message
        }), 200 if success else 400

    except Exception as e:
        logger.error(f"Error restarting service: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ai_agent_bp.route('/api/logs', methods=['GET'])
def get_logs():
    """Get recent agent logs"""
    try:
        lines = request.args.get('lines', 100, type=int)
        logs = ai_service.get_logs(lines)

        return jsonify({
            'success': True,
            'logs': logs,
            'count': len(logs),
            'log_file': ai_service.get_status().get('log_file')
        })

    except Exception as e:
        logger.error(f"Error getting logs: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ai_agent_bp.route('/api/config', methods=['GET'])
def get_config():
    """Get configuration status"""
    try:
        config = {
            'agent_path': str(ai_service.agent_path),
            'log_file': str(ai_service.log_file) if ai_service.log_file else None,
            'environment': {
                'AZURE_OPENAI_ENDPOINT': os.environ.get('AZURE_OPENAI_ENDPOINT', 'Not set'),
                'AZURE_OPENAI_DEPLOYMENT': os.environ.get('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o-mini'),
                'AZURE_SPEECH_REGION': os.environ.get('AZURE_SPEECH_REGION', 'eastus'),
                'DATAVERSE_URL': os.environ.get('DATAVERSE_URL', 'Not configured'),
                'ARI_URL': os.environ.get('ARI_URL', 'http://10.200.200.2:8088/ari'),
                'ARI_APPLICATION': os.environ.get('ARI_APPLICATION', 'ai-agent')
            }
        }

        return jsonify({
            'success': True,
            'config': config
        })

    except Exception as e:
        logger.error(f"Error getting config: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500