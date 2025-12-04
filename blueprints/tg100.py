"""
TG100 device monitoring blueprint
Handles TG100 device status monitoring via ping with audit logging
"""

from flask import Blueprint, render_template, jsonify, request
import subprocess
import logging
import re
import os
from datetime import datetime
import time
from audit_utils import log_action  # Import audit logging

logger = logging.getLogger(__name__)

tg100_bp = Blueprint('tg100', __name__)

# Configuration
TG100_IP = '192.168.0.35'
TG100_NAME = 'TG100 Gateway'


class TG100Monitor:
    """Monitors TG100 device status"""

    def __init__(self):
        self.device_ip = TG100_IP
        self.device_name = TG100_NAME
        self.ping_history = []  # Store recent ping results
        self.max_history = 50  # Keep last 50 pings

        # Define full paths to commands
        self.ping_cmd = '/usr/bin/ping'

        # Set environment with proper PATH
        self.env = {
            'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'HOME': os.environ.get('HOME', '/root')
        }

    def ping_device(self, count=4, timeout=2):
        """
        Ping the TG100 device
        Returns detailed ping statistics
        """
        try:
            start_time = time.time()

            # Ping command - Linux style with full path
            result = subprocess.run(
                [self.ping_cmd, '-c', str(count), '-W', str(timeout), self.device_ip],
                capture_output=True,
                text=True,
                timeout=timeout * count + 2,
                env=self.env
            )

            end_time = time.time()
            duration = end_time - start_time

            ping_data = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'online': result.returncode == 0,
                'ip': self.device_ip,
                'packets_sent': count,
                'packets_received': 0,
                'packet_loss': 100.0,
                'min_rtt': None,
                'avg_rtt': None,
                'max_rtt': None,
                'mdev_rtt': None,
                'duration': round(duration, 2),
                'raw_output': result.stdout
            }

            if result.returncode == 0:
                # Parse ping output
                ping_data.update(self._parse_ping_output(result.stdout, count))
                logger.debug(f"Ping successful to {self.device_ip}: avg_rtt={ping_data.get('avg_rtt')}ms")
            else:
                logger.warning(f"Ping failed to {self.device_ip}: {result.stderr}")

            # Add to history
            self._add_to_history(ping_data)

            return ping_data

        except subprocess.TimeoutExpired:
            logger.warning(f"Ping timeout for {self.device_ip}")
            ping_data = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'online': False,
                'ip': self.device_ip,
                'packets_sent': count,
                'packets_received': 0,
                'packet_loss': 100.0,
                'error': 'Timeout',
                'duration': timeout * count
            }
            self._add_to_history(ping_data)
            return ping_data

        except FileNotFoundError as e:
            logger.error(f"Ping command not found: {e}")
            ping_data = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'online': False,
                'ip': self.device_ip,
                'error': f'Command not found: {e}'
            }
            self._add_to_history(ping_data)
            return ping_data

        except Exception as e:
            logger.error(f"Error pinging device: {e}")
            ping_data = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'online': False,
                'ip': self.device_ip,
                'error': str(e)
            }
            self._add_to_history(ping_data)
            return ping_data

    def _parse_ping_output(self, output, packets_sent):
        """Parse ping command output for statistics"""
        stats = {}

        try:
            # Parse packets received
            # Example: "4 packets transmitted, 4 received, 0% packet loss"
            packets_match = re.search(r'(\d+) received', output)
            if packets_match:
                stats['packets_received'] = int(packets_match.group(1))
                stats['packet_loss'] = round(
                    ((packets_sent - stats['packets_received']) / packets_sent) * 100, 2
                )
            else:
                stats['packets_received'] = 0
                stats['packet_loss'] = 100.0

            # Parse RTT statistics
            # Example: "rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms"
            rtt_match = re.search(
                r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms',
                output
            )
            if rtt_match:
                stats['min_rtt'] = float(rtt_match.group(1))
                stats['avg_rtt'] = float(rtt_match.group(2))
                stats['max_rtt'] = float(rtt_match.group(3))
                stats['mdev_rtt'] = float(rtt_match.group(4))

        except Exception as e:
            logger.error(f"Error parsing ping output: {e}")

        return stats

    def _add_to_history(self, ping_data):
        """Add ping result to history"""
        # Create a simplified version for history
        history_entry = {
            'timestamp': ping_data['timestamp'],
            'online': ping_data['online'],
            'avg_rtt': ping_data.get('avg_rtt'),
            'packet_loss': ping_data.get('packet_loss', 100.0)
        }

        self.ping_history.append(history_entry)

        # Keep only recent history
        if len(self.ping_history) > self.max_history:
            self.ping_history.pop(0)

    def get_ping_history(self):
        """Get ping history"""
        return self.ping_history

    def get_statistics(self):
        """Calculate overall statistics from history"""
        if not self.ping_history:
            return {
                'total_checks': 0,
                'online_count': 0,
                'offline_count': 0,
                'uptime_percentage': 0.0,
                'avg_response_time': None
            }

        total_checks = len(self.ping_history)
        online_count = sum(1 for h in self.ping_history if h['online'])
        offline_count = total_checks - online_count

        # Calculate average response time (only for successful pings)
        response_times = [h['avg_rtt'] for h in self.ping_history if h['online'] and h['avg_rtt'] is not None]
        avg_response_time = sum(response_times) / len(response_times) if response_times else None

        return {
            'total_checks': total_checks,
            'online_count': online_count,
            'offline_count': offline_count,
            'uptime_percentage': round((online_count / total_checks) * 100, 2) if total_checks > 0 else 0.0,
            'avg_response_time': round(avg_response_time, 2) if avg_response_time else None
        }

    def quick_ping(self):
        """Quick ping with minimal packets for faster response"""
        return self.ping_device(count=1, timeout=1)


# Initialize monitor
tg100_monitor = TG100Monitor()


# ============================================================================
# FLASK ROUTES WITH AUDIT LOGGING
# ============================================================================

@tg100_bp.route('/')
def index():
    """TG100 monitoring page"""
    # Log page view
    log_action(
        action='view',
        resource_type='tg100_page',
        details={
            'device_name': TG100_NAME,
            'device_ip': TG100_IP,
            'message': 'Accessed TG100 monitoring page'
        }
    )

    return render_template('tg100/index.html',
                           device_name=TG100_NAME,
                           device_ip=TG100_IP)


@tg100_bp.route('/api/ping')
def ping():
    """Ping the TG100 device"""
    try:
        count = int(request.args.get('count', 4))
        timeout = int(request.args.get('timeout', 2))

        result = tg100_monitor.ping_device(count=count, timeout=timeout)

        # Log ping attempt
        log_action(
            action='ping',
            resource_type='tg100_device',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'packets_sent': count,
                'packets_received': result.get('packets_received', 0),
                'packet_loss': result.get('packet_loss', 100.0),
                'avg_rtt': result.get('avg_rtt'),
                'online': result.get('online', False),
                'timeout': timeout
            }
        )

        return jsonify({'success': True, 'ping': result})

    except Exception as e:
        logger.error(f"Error in ping endpoint: {e}")

        # Log error
        log_action(
            action='ping_error',
            resource_type='tg100_device',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500


@tg100_bp.route('/api/quick-ping')
def quick_ping():
    """Quick ping for status checks"""
    try:
        result = tg100_monitor.quick_ping()

        # Log quick ping (less verbose)
        log_action(
            action='quick_ping',
            resource_type='tg100_device',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'online': result.get('online', False),
                'avg_rtt': result.get('avg_rtt')
            }
        )

        return jsonify({'success': True, 'ping': result})

    except Exception as e:
        logger.error(f"Error in quick ping: {e}")

        # Log error
        log_action(
            action='quick_ping_error',
            resource_type='tg100_device',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500


@tg100_bp.route('/api/history')
def get_history():
    """Get ping history"""
    try:
        history = tg100_monitor.get_ping_history()

        # Log history view
        log_action(
            action='view',
            resource_type='tg100_history',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'history_count': len(history)
            }
        )

        return jsonify({'success': True, 'history': history})

    except Exception as e:
        logger.error(f"Error getting history: {e}")

        # Log error
        log_action(
            action='view_error',
            resource_type='tg100_history',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500


@tg100_bp.route('/api/statistics')
def get_statistics():
    """Get overall statistics"""
    try:
        stats = tg100_monitor.get_statistics()

        # Log statistics view
        log_action(
            action='view',
            resource_type='tg100_statistics',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'total_checks': stats.get('total_checks', 0),
                'online_count': stats.get('online_count', 0),
                'offline_count': stats.get('offline_count', 0),
                'uptime_percentage': stats.get('uptime_percentage', 0.0),
                'avg_response_time': stats.get('avg_response_time')
            }
        )

        return jsonify({'success': True, 'statistics': stats})

    except Exception as e:
        logger.error(f"Error getting statistics: {e}")

        # Log error
        log_action(
            action='view_error',
            resource_type='tg100_statistics',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500


@tg100_bp.route('/api/clear-history', methods=['POST'])
def clear_history():
    """Clear ping history"""
    try:
        # Get history count before clearing
        history_count = len(tg100_monitor.ping_history)

        # Clear history
        tg100_monitor.ping_history = []

        # Log history clear
        log_action(
            action='clear',
            resource_type='tg100_history',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'records_cleared': history_count,
                'message': 'Ping history cleared'
            }
        )

        return jsonify({'success': True, 'message': 'History cleared'})

    except Exception as e:
        logger.error(f"Error clearing history: {e}")

        # Log error
        log_action(
            action='clear_error',
            resource_type='tg100_history',
            resource_id=TG100_IP,
            details={
                'device_name': TG100_NAME,
                'device_ip': TG100_IP,
                'error': str(e)
            }
        )

        return jsonify({'success': False, 'error': str(e)}), 500