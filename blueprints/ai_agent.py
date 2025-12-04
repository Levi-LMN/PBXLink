"""
AI Agent service management blueprint
Handles AI Agent service monitoring, status, and logs with audit logging
"""

from flask import Blueprint, render_template, jsonify, request
import subprocess
import logging
import re
import os
from datetime import datetime
from audit_utils import log_action  # Import audit logging

logger = logging.getLogger(__name__)

ai_agent_bp = Blueprint('ai_agent', __name__)

# Configuration
SERVICE_NAME = 'ai_agent.service'


class AIAgentManager:
    """Manages AI Agent service monitoring and control"""

    def __init__(self):
        self.service_name = SERVICE_NAME
        # Define full paths to commands
        self.sudo_cmd = '/usr/bin/sudo'
        self.systemctl_cmd = '/usr/bin/systemctl'
        self.journalctl_cmd = '/usr/bin/journalctl'

        # Set environment with proper PATH
        self.env = {
            'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'HOME': os.environ.get('HOME', '/root')
        }

    def get_service_status(self):
        """Get detailed service status"""
        try:
            result = subprocess.run(
                [self.sudo_cmd, self.systemctl_cmd, 'status', self.service_name],
                capture_output=True,
                text=True,
                timeout=5,
                env=self.env
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
        except FileNotFoundError as e:
            logger.error(f"Command not found: {e}")
            return {'active': False, 'status': 'error', 'error': f'Command not found: {e}'}
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
                [self.sudo_cmd, self.journalctl_cmd, '-u', self.service_name,
                 '-n', str(lines), '--no-pager'],
                capture_output=True,
                text=True,
                timeout=10,
                env=self.env
            )

            if result.returncode == 0:
                logs = result.stdout.strip().split('\n')
                return self._parse_logs(logs)
            else:
                logger.error(f"journalctl returned non-zero exit code: {result.returncode}")
                return []

        except subprocess.TimeoutExpired:
            logger.error("Log retrieval timed out")
            return []
        except FileNotFoundError as e:
            logger.error(f"Command not found: {e}")
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
                [self.sudo_cmd, self.systemctl_cmd, 'start', self.service_name],
                capture_output=True,
                text=True,
                timeout=10,
                env=self.env
            )

            if result.returncode == 0:
                logger.info(f"Service {self.service_name} started successfully")
                return True
            else:
                logger.error(f"Failed to start service: {result.stderr}")
                return False

        except FileNotFoundError as e:
            logger.error(f"Command not found: {e}")
            return False
        except Exception as e:
            logger.error(f"Error starting service: {e}")
            return False

    def stop_service(self):
        """Stop the AI Agent service"""
        try:
            result = subprocess.run(
                [self.sudo_cmd, self.systemctl_cmd, 'stop', self.service_name],
                capture_output=True,
                text=True,
                timeout=10,
                env=self.env
            )

            if result.returncode == 0:
                logger.info(f"Service {self.service_name} stopped successfully")
                return True
            else:
                logger.error(f"Failed to stop service: {result.stderr}")
                return False

        except FileNotFoundError as e:
            logger.error(f"Command not found: {e}")
            return False
        except Exception as e:
            logger.error(f"Error stopping service: {e}")
            return False

    def restart_service(self):
        """Restart the AI Agent service"""
        try:
            result = subprocess.run(
                [self.sudo_cmd, self.systemctl_cmd, 'restart', self.service_name],
                capture_output=True,
                text=True,
                timeout=10,
                env=self.env
            )

            if result.returncode == 0:
                logger.info(f"Service {self.service_name} restarted successfully")
                return True
            else:
                logger.error(f"Failed to restart service: {result.stderr}")
                return False

        except FileNotFoundError as e:
            logger.error(f"Command not found: {e}")
            return False
        except Exception as e:
            logger.error(f"Error restarting service: {e}")
            return False

    def get_service_properties(self):
        """Get detailed service properties"""
        try:
            result = subprocess.run(
                [self.sudo_cmd, self.systemctl_cmd, 'show', self.service_name],
                capture_output=True,
                text=True,
                timeout=5,
                env=self.env
            )

            properties = {}
            for line in result.stdout.split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    properties[key] = value

            return properties

        except FileNotFoundError as e:
            logger.error(f"Command not found: {e}")
            return {}
        except Exception as e:
            logger.error(f"Error getting service properties: {e}")
            return {}


# Initialize manager
ai_agent_manager = AIAgentManager()


# ============================================================================
# FLASK ROUTES WITH AUDIT LOGGING
# ============================================================================

@ai_agent_bp.route('/')
def index():
    """AI Agent monitoring page"""
    # Log page view
    log_action(
        action='view',
        resource_type='ai_agent_page',
        details='Accessed AI Agent monitoring page'
    )
    return render_template('ai_agent/index.html')


@ai_agent_bp.route('/api/status')
def get_status():
    """Get AI Agent service status"""
    try:
        status = ai_agent_manager.get_service_status()

        # Log status check
        log_action(
            action='view',
            resource_type='ai_agent_status',
            details={
                'service': SERVICE_NAME,
                'status': status.get('status', 'unknown'),
                'active': status.get('active', False),
                'pid': status.get('pid', 'N/A'),
                'memory': status.get('memory', 'N/A')
            }
        )

        return jsonify({'success': True, 'status': status})

    except Exception as e:
        logger.error(f"Error getting status: {e}")

        # Log error
        log_action(
            action='view_error',
            resource_type='ai_agent_status',
            details={
                'service': SERVICE_NAME,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_bp.route('/api/logs')
def get_logs():
    """Get AI Agent service logs"""
    try:
        lines = int(request.args.get('lines', 100))
        logs = ai_agent_manager.get_service_logs(lines)

        # Log logs retrieval
        log_action(
            action='view',
            resource_type='ai_agent_logs',
            details={
                'service': SERVICE_NAME,
                'lines_requested': lines,
                'lines_returned': len(logs)
            }
        )

        return jsonify({'success': True, 'logs': logs})

    except Exception as e:
        logger.error(f"Error getting logs: {e}")

        # Log error
        log_action(
            action='view_error',
            resource_type='ai_agent_logs',
            details={
                'service': SERVICE_NAME,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_bp.route('/api/start', methods=['POST'])
def start_service():
    """Start AI Agent service"""
    try:
        if ai_agent_manager.start_service():
            # Log successful start
            log_action(
                action='start',
                resource_type='ai_agent_service',
                details={
                    'service': SERVICE_NAME,
                    'message': 'Service started successfully'
                }
            )

            return jsonify({'success': True, 'message': 'Service started successfully'})

        # Log failed start
        log_action(
            action='start_failed',
            resource_type='ai_agent_service',
            details={
                'service': SERVICE_NAME,
                'error': 'Failed to start service'
            }
        )

        return jsonify({'success': False, 'error': 'Failed to start service'}), 500

    except Exception as e:
        logger.error(f"Error starting service: {e}")

        # Log error
        log_action(
            action='start_error',
            resource_type='ai_agent_service',
            details={
                'service': SERVICE_NAME,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_bp.route('/api/stop', methods=['POST'])
def stop_service():
    """Stop AI Agent service"""
    try:
        if ai_agent_manager.stop_service():
            # Log successful stop
            log_action(
                action='stop',
                resource_type='ai_agent_service',
                details={
                    'service': SERVICE_NAME,
                    'message': 'Service stopped successfully'
                }
            )

            return jsonify({'success': True, 'message': 'Service stopped successfully'})

        # Log failed stop
        log_action(
            action='stop_failed',
            resource_type='ai_agent_service',
            details={
                'service': SERVICE_NAME,
                'error': 'Failed to stop service'
            }
        )

        return jsonify({'success': False, 'error': 'Failed to stop service'}), 500

    except Exception as e:
        logger.error(f"Error stopping service: {e}")

        # Log error
        log_action(
            action='stop_error',
            resource_type='ai_agent_service',
            details={
                'service': SERVICE_NAME,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_bp.route('/api/restart', methods=['POST'])
def restart_service():
    """Restart AI Agent service"""
    try:
        if ai_agent_manager.restart_service():
            # Log successful restart
            log_action(
                action='restart',
                resource_type='ai_agent_service',
                details={
                    'service': SERVICE_NAME,
                    'message': 'Service restarted successfully'
                }
            )

            return jsonify({'success': True, 'message': 'Service restarted successfully'})

        # Log failed restart
        log_action(
            action='restart_failed',
            resource_type='ai_agent_service',
            details={
                'service': SERVICE_NAME,
                'error': 'Failed to restart service'
            }
        )

        return jsonify({'success': False, 'error': 'Failed to restart service'}), 500

    except Exception as e:
        logger.error(f"Error restarting service: {e}")

        # Log error
        log_action(
            action='restart_error',
            resource_type='ai_agent_service',
            details={
                'service': SERVICE_NAME,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_bp.route('/api/properties')
def get_properties():
    """Get detailed service properties"""
    try:
        properties = ai_agent_manager.get_service_properties()

        # Log properties view
        log_action(
            action='view',
            resource_type='ai_agent_properties',
            details={
                'service': SERVICE_NAME,
                'properties_count': len(properties)
            }
        )

        return jsonify({'success': True, 'properties': properties})

    except Exception as e:
        logger.error(f"Error getting properties: {e}")

        # Log error
        log_action(
            action='view_error',
            resource_type='ai_agent_properties',
            details={
                'service': SERVICE_NAME,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500