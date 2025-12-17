"""
Service Monitoring Module
Monitors critical services and sends Teams alerts on downtime
"""

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable
import os
import json
from pathlib import Path

# FIX: Import from same directory (blueprints)
from blueprints.teams_notifier import teams_notifier

logger = logging.getLogger(__name__)


class ServiceMonitor:
    """Monitors services and triggers Teams notifications on downtime"""

    def __init__(self):
        self.running = False
        self.thread = None
        self.services = {}
        self.check_interval = 60  # Check every 60 seconds

    def register_service(
            self,
            service_name: str,
            check_function: Callable[[], bool],
            downtime_threshold: int = 180,  # 3 minutes
            notification_interval: int = 30  # 30 minutes between alerts
    ):
        """
        Register a service to monitor

        Args:
            service_name: Unique name for the service
            check_function: Function that returns True if service is up
            downtime_threshold: Seconds before triggering alert
            notification_interval: Minutes between repeated notifications
        """
        self.services[service_name] = {
            'check_function': check_function,
            'downtime_threshold': downtime_threshold,
            'notification_interval': notification_interval,
            'status': 'unknown',
            'last_check': None,
            'last_success': None,
            'consecutive_failures': 0,
            'alert_sent': False,
            'recovery_sent': False
        }

        logger.info(f"Registered service for monitoring: {service_name}")

    def start(self):
        """Start the monitoring thread"""
        if self.running:
            logger.warning("Service monitor already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logger.info("Service monitor started")

    def stop(self):
        """Stop the monitoring thread"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Service monitor stopped")

    def _monitor_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                self._check_all_services()
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")

            # Sleep for check interval
            time.sleep(self.check_interval)

    def _check_all_services(self):
        """Check all registered services"""
        for service_name, service_info in self.services.items():
            try:
                self._check_service(service_name, service_info)
            except Exception as e:
                logger.error(f"Error checking service {service_name}: {e}")

    def _check_service(self, service_name: str, service_info: Dict):
        """Check a single service"""
        check_function = service_info['check_function']
        current_time = datetime.now()

        # Run the check
        try:
            is_up = check_function()
        except Exception as e:
            logger.error(f"Service check failed for {service_name}: {e}")
            is_up = False

        service_info['last_check'] = current_time

        if is_up:
            # Service is UP
            if service_info['status'] == 'down':
                # Service recovered!
                self._handle_service_recovery(service_name, service_info)

            service_info['status'] = 'up'
            service_info['last_success'] = current_time
            service_info['consecutive_failures'] = 0

        else:
            # Service is DOWN
            service_info['consecutive_failures'] += 1

            # Calculate downtime
            if service_info['last_success']:
                downtime = (current_time - service_info['last_success']).total_seconds()
            else:
                downtime = 0

            # Check if we should send alert
            if downtime >= service_info['downtime_threshold']:
                if not service_info['alert_sent']:
                    # First alert
                    self._handle_service_down(service_name, service_info, downtime)
                    service_info['alert_sent'] = True
                    service_info['status'] = 'down'
                    service_info['recovery_sent'] = False
                else:
                    # Check if we should send repeated alert
                    service_key = f"service_down_{service_name}"
                    if teams_notifier.should_send_notification(
                            service_key,
                            service_info['notification_interval']
                    ):
                        self._handle_service_down(service_name, service_info, downtime)

    def _handle_service_down(self, service_name: str, service_info: Dict, downtime: float):
        """Handle service down event"""
        downtime_str = self._format_duration(downtime)

        if service_info['last_success']:
            last_seen = service_info['last_success'].strftime('%Y-%m-%d %H:%M:%S')
        else:
            last_seen = "Unknown"

        logger.warning(
            f"Service DOWN: {service_name} - "
            f"Downtime: {downtime_str}, Last seen: {last_seen}"
        )

        # Send Teams notification
        teams_notifier.send_service_down_alert(
            service_name=service_name,
            downtime_duration=downtime_str,
            last_seen=last_seen,
            error_details=f"{service_info['consecutive_failures']} consecutive failures"
        )

    def _handle_service_recovery(self, service_name: str, service_info: Dict):
        """Handle service recovery event"""
        if service_info['last_success']:
            downtime = (datetime.now() - service_info['last_success']).total_seconds()
            downtime_str = self._format_duration(downtime)
        else:
            downtime_str = "Unknown"

        logger.info(f"Service RECOVERED: {service_name} - Total downtime: {downtime_str}")

        # Send recovery notification only if alert was sent
        if service_info['alert_sent'] and not service_info.get('recovery_sent', False):
            teams_notifier.send_service_recovered_alert(
                service_name=service_name,
                downtime_duration=downtime_str
            )
            service_info['recovery_sent'] = True

        service_info['alert_sent'] = False

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format"""
        if seconds < 60:
            return f"{int(seconds)} seconds"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours} hour{'s' if hours != 1 else ''} {minutes} minute{'s' if minutes != 1 else ''}"
        else:
            days = int(seconds / 86400)
            hours = int((seconds % 86400) / 3600)
            return f"{days} day{'s' if days != 1 else ''} {hours} hour{'s' if hours != 1 else ''}"

    def get_service_status(self, service_name: str) -> Optional[Dict]:
        """Get current status of a service"""
        return self.services.get(service_name)

    def get_all_status(self) -> Dict:
        """Get status of all monitored services"""
        status_summary = {}

        for service_name, service_info in self.services.items():
            status_summary[service_name] = {
                'status': service_info['status'],
                'last_check': service_info['last_check'].isoformat() if service_info['last_check'] else None,
                'last_success': service_info['last_success'].isoformat() if service_info['last_success'] else None,
                'consecutive_failures': service_info['consecutive_failures']
            }

        return status_summary


# Specific service check functions

def check_ai_agent_heartbeat(heartbeat_file: str, max_age_seconds: int = 60) -> bool:
    """
    Check if AI Agent is alive by reading heartbeat file

    Args:
        heartbeat_file: Path to heartbeat file
        max_age_seconds: Maximum age of heartbeat before considering down
    """
    try:
        if not os.path.exists(heartbeat_file):
            return False

        # Read heartbeat file
        with open(heartbeat_file, 'r') as f:
            heartbeat_data = json.load(f)

        timestamp = heartbeat_data.get('timestamp', 0)
        age = time.time() - timestamp

        return age <= max_age_seconds

    except Exception as e:
        logger.error(f"Error checking AI agent heartbeat: {e}")
        return False


def check_wireguard_vpn() -> bool:
    """Check if WireGuard VPN is running"""
    try:
        import subprocess
        result = subprocess.run(
            ['/usr/bin/sudo', '/usr/bin/systemctl', 'is-active', 'wg-quick@wg0'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0 and 'active' in result.stdout
    except Exception as e:
        logger.error(f"Error checking WireGuard: {e}")
        return False


def check_tg100_device(device_ip: str = '192.168.0.35') -> bool:
    """Check if TG100 device is reachable"""
    try:
        import subprocess
        result = subprocess.run(
            ['/usr/bin/ping', '-c', '1', '-W', '2', device_ip],
            capture_output=True,
            timeout=3
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Error checking TG100: {e}")
        return False


def check_freepbx_api() -> bool:
    """Check if FreePBX API is responding"""
    try:
        from flask import current_app
        from blueprints.api_core import api

        # Try to get access token
        token = api.get_access_token()
        return token is not None

    except Exception as e:
        logger.error(f"Error checking FreePBX API: {e}")
        return False


def check_ssh_connection() -> bool:
    """Check if SSH connection to FreePBX is working"""
    try:
        from ssh_manager import ssh_manager
        return ssh_manager.test_connection()
    except Exception as e:
        logger.error(f"Error checking SSH: {e}")
        return False


# Global monitor instance
service_monitor = ServiceMonitor()


def init_service_monitor(app, webhook_url: str):
    """
    Initialize service monitoring with Flask app

    Args:
        app: Flask application instance
        webhook_url: Microsoft Teams webhook URL
    """
    from blueprints.teams_notifier import init_teams_notifier

    # Initialize Teams notifier
    init_teams_notifier(webhook_url)

    with app.app_context():
        # Register services to monitor

        # AI Agent (if heartbeat file is configured)
        heartbeat_file = os.environ.get('AI_AGENT_HEARTBEAT_FILE')
        if heartbeat_file:
            service_monitor.register_service(
                service_name='AI Voice Agent',
                check_function=lambda: check_ai_agent_heartbeat(heartbeat_file, max_age_seconds=60),
                downtime_threshold=120,  # 2 minutes
                notification_interval=30  # Alert every 30 minutes
            )

        # WireGuard VPN
        service_monitor.register_service(
            service_name='WireGuard VPN',
            check_function=check_wireguard_vpn,
            downtime_threshold=180,  # 3 minutes
            notification_interval=60  # Alert every 60 minutes
        )

        # TG100 Gateway
        service_monitor.register_service(
            service_name='TG100 Gateway',
            check_function=lambda: check_tg100_device('192.168.0.35'),
            downtime_threshold=300,  # 5 minutes
            notification_interval=60  # Alert every 60 minutes
        )

        # FreePBX API
        service_monitor.register_service(
            service_name='FreePBX API',
            check_function=check_freepbx_api,
            downtime_threshold=180,  # 3 minutes
            notification_interval=30  # Alert every 30 minutes
        )

        # SSH Connection
        service_monitor.register_service(
            service_name='FreePBX SSH',
            check_function=check_ssh_connection,
            downtime_threshold=180,  # 3 minutes
            notification_interval=30  # Alert every 30 minutes
        )

        # Start monitoring
        service_monitor.start()

        logger.info("Service monitoring initialized with Teams notifications")