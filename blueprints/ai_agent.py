"""
AI Agent service management blueprint
Handles AI Agent service monitoring, status, and logs
"""

from flask import Blueprint, render_template, jsonify
import subprocess
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

ai_agent_bp = Blueprint('ai_agent', __name__)

# Configuration
SERVICE_NAME = 'ai_agent.service'


class AIAgentManager:
    """Manages AI Agent service monitoring and control"""

    def __init__(self):
        self.service_name = SERVICE_NAME

    def get_service_status(self):
        """Get detailed service status"""
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'status', self.service_name],
                capture_output=True,
                text=True,
                timeout=5
            )

            # Parse the output
            output = result.stdout
            status_info = {
                'active': 'active (running)' in output.lower(),
                'status': self._extract_status(output),
                'pid': self._extract_pid(output),
                'memory': self._extract_memory(output),
                'uptime': self._extract_uptime(output),
                'main_pid': self._extract_main_pid(output),
                'tasks': self._extract_tasks(output),
                'loaded': self._extract_loaded_status(output),
                'raw_output': output
            }

            return status_info

        except subprocess.TimeoutExpired:
            logger.error("Service status check timed out")
            return {'active': False, 'status': 'timeout', 'error': 'Status check timed out'}
        except Exception as e:
            logger.error(f"Error getting service status: {e}")
            return {'active': False, 'status': 'error', 'error': str(e)}

    def _extract_status(self, output):
        """Extract service status"""
        if 'Active: active (running)' in output:
            return 'running'
        elif 'Active: inactive (dead)' in output:
            return 'stopped'
        elif 'Active: failed' in output:
            return 'failed'
        elif 'Active: activating' in output:
            return 'starting'
        else:
            return 'unknown'

    def _extract_pid(self, output):
        """Extract PID from status output"""
        match = re.search(r'Main PID:\s+(\d+)', output)
        return match.group(1) if match else 'N/A'

    def _extract_main_pid(self, output):
        """Extract main PID with process name"""
        match = re.search(r'Main PID:\s+(\d+)\s+\((.*?)\)', output)
        if match:
            return {'pid': match.group(1), 'name': match.group(2)}
        return None

    def _extract_memory(self, output):
        """Extract memory usage"""
        match = re.search(r'Memory:\s+([\d.]+[A-Z])', output)
        return match.group(1) if match else 'N/A'

    def _extract_uptime(self, output):
        """Extract uptime/start time"""
        # Try to find "Active: active (running) since ..."
        match = re.search(r'Active:.*since\s+(.+?)(?:;|$)', output)
        if match:
            return match.group(1).strip()
        return 'N/A'

    def _extract_tasks(self, output):
        """Extract number of tasks"""
        match = re.search(r'Tasks:\s+(\d+)', output)
        return match.group(1) if match else 'N/A'

    def _extract_loaded_status(self, output):
        """Extract loaded status"""
        match = re.search(r'Loaded:\s+(.+?)(?:\n|$)', output)
        return match.group(1).strip() if match else 'unknown'

    def get_service_logs(self, lines=100):
        """Get recent service logs"""
        try:
            result = subprocess.run(
                ['sudo', 'journalctl', '-u', self.service_name, '-n', str(lines), '--no-pager'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                logs = result.stdout.strip().split('\n')
                return self._parse_logs(logs)
            else:
                return []

        except subprocess.TimeoutExpired:
            logger.error("Log retrieval timed out")
            return []
        except Exception as e:
            logger.error(f"Error getting service logs: {e}")
            return []

    def _parse_logs(self, log_lines):
        """Parse log lines into structured format"""
        parsed_logs = []

        for line in log_lines:
            if not line.strip():
                continue

            # Parse systemd journal format
            # Example: "Nov 27 12:34:56 hostname service[1234]: Log message"
            match = re.match(r'(\w+\s+\d+\s+\d+:\d+:\d+)\s+(\S+)\s+(\S+)\[(\d+)\]:\s+(.*)', line)

            if match:
                timestamp, hostname, service, pid, message = match.groups()
                parsed_logs.append({
                    'timestamp': timestamp,
                    'hostname': hostname,
                    'service': service,
                    'pid': pid,
                    'message': message,
                    'level': self._detect_log_level(message)
                })
            else:
                # If line doesn't match expected format, add as-is
                parsed_logs.append({
                    'timestamp': '',
                    'hostname': '',
                    'service': '',
                    'pid': '',
                    'message': line,
                    'level': 'info'
                })

        return parsed_logs

    def _detect_log_level(self, message):
        """Detect log level from message content"""
        message_lower = message.lower()
        if any(word in message_lower for word in ['error', 'failed', 'exception']):
            return 'error'
        elif any(word in message_lower for word in ['warning', 'warn']):
            return 'warning'
        elif any(word in message_lower for word in ['success', 'completed', 'started']):
            return 'success'
        else:
            return 'info'

    def start_service(self):
        """Start the AI Agent service"""
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'start', self.service_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Error starting service: {e}")
            return False

    def stop_service(self):
        """Stop the AI Agent service"""
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'stop', self.service_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Error stopping service: {e}")
            return False

    def restart_service(self):
        """Restart the AI Agent service"""
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'restart', self.service_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Error restarting service: {e}")
            return False

    def get_service_properties(self):
        """Get detailed service properties"""
        try:
            result = subprocess.run(
                ['sudo', 'systemctl', 'show', self.service_name],
                capture_output=True,
                text=True,
                timeout=5
            )

            properties = {}
            for line in result.stdout.split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    properties[key] = value

            return properties
        except Exception as e:
            logger.error(f"Error getting service properties: {e}")
            return {}


# Initialize manager
ai_agent_manager = AIAgentManager()


# Routes
@ai_agent_bp.route('/')
def index():
    """AI Agent monitoring page"""
    return render_template('ai_agent/index.html')


@ai_agent_bp.route('/api/status')
def get_status():
    """Get AI Agent service status"""
    status = ai_agent_manager.get_service_status()
    return jsonify({'success': True, 'status': status})


@ai_agent_bp.route('/api/logs')
def get_logs():
    """Get AI Agent service logs"""
    lines = int(request.args.get('lines', 100))
    logs = ai_agent_manager.get_service_logs(lines)
    return jsonify({'success': True, 'logs': logs})


@ai_agent_bp.route('/api/start', methods=['POST'])
def start_service():
    """Start AI Agent service"""
    if ai_agent_manager.start_service():
        return jsonify({'success': True, 'message': 'Service started successfully'})
    return jsonify({'success': False, 'error': 'Failed to start service'}), 500


@ai_agent_bp.route('/api/stop', methods=['POST'])
def stop_service():
    """Stop AI Agent service"""
    if ai_agent_manager.stop_service():
        return jsonify({'success': True, 'message': 'Service stopped successfully'})
    return jsonify({'success': False, 'error': 'Failed to stop service'}), 500


@ai_agent_bp.route('/api/restart', methods=['POST'])
def restart_service():
    """Restart AI Agent service"""
    if ai_agent_manager.restart_service():
        return jsonify({'success': True, 'message': 'Service restarted successfully'})
    return jsonify({'success': False, 'error': 'Failed to restart service'}), 500


@ai_agent_bp.route('/api/properties')
def get_properties():
    """Get detailed service properties"""
    properties = ai_agent_manager.get_service_properties()
    return jsonify({'success': True, 'properties': properties})