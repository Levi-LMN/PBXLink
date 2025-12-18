"""
Service Monitoring Module - FIXED VERSION
Monitors critical services and sends Teams alerts on downtime
NOW RESPECTS THE GLOBAL NOTIFICATION TOGGLE
"""

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable
import os
import json
from pathlib import Path

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
            downtime_threshold: int = 180,
            notification_interval: int = 30
    ):
        """Register a service to monitor"""
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

        # CHECK IF NOTIFICATIONS ARE ENABLED BEFORE SENDING
        from blueprints.admin import TEAMS_NOTIFICATIONS_ENABLED

        if TEAMS_NOTIFICATIONS_ENABLED:
            # Send Teams notification
            teams_notifier.send_service_down_alert(
                service_name=service_name,
                downtime_duration=downtime_str,
                last_seen=last_seen,
                error_details=f"{service_info['consecutive_failures']} consecutive failures"
            )
            logger.info(f"📧 Sent Teams notification for {service_name} down")
        else:
            logger.info(f"🔕 Teams notifications disabled - NOT sending alert for {service_name}")

    def _handle_service_recovery(self, service_name: str, service_info: Dict):
        """Handle service recovery event"""
        if service_info['last_success']:
            downtime = (datetime.now() - service_info['last_success']).total_seconds()
            downtime_str = self._format_duration(downtime)
        else:
            downtime_str = "Unknown"

        logger.info(f"Service RECOVERED: {service_name} - Total downtime: {downtime_str}")

        # CHECK IF NOTIFICATIONS ARE ENABLED BEFORE SENDING
        from blueprints.admin import TEAMS_NOTIFICATIONS_ENABLED

        # Send recovery notification only if alert was sent
        if service_info['alert_sent'] and not service_info.get('recovery_sent', False):
            if TEAMS_NOTIFICATIONS_ENABLED:
                teams_notifier.send_service_recovered_alert(
                    service_name=service_name,
                    downtime_duration=downtime_str
                )
                logger.info(f"📧 Sent Teams recovery notification for {service_name}")
            else:
                logger.info(f"🔕 Teams notifications disabled - NOT sending recovery alert for {service_name}")

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


# ============================================================================
# SERVICE CHECK FUNCTIONS - FIXED TO MATCH DASHBOARD
# ============================================================================

def check_ai_agent_heartbeat(heartbeat_file: str, max_age_seconds: int = 60) -> bool:
    """Check if AI Agent is alive by reading heartbeat file"""
    try:
        if not os.path.exists(heartbeat_file):
            logger.debug(f"AI Agent heartbeat file not found: {heartbeat_file}")
            return False

        with open(heartbeat_file, 'r') as f:
            heartbeat_data = json.load(f)

        timestamp = heartbeat_data.get('timestamp', 0)
        age = time.time() - timestamp

        is_alive = age <= max_age_seconds
        logger.debug(f"AI Agent heartbeat age: {age:.1f}s (threshold: {max_age_seconds}s) - {'alive' if is_alive else 'dead'}")
        return is_alive

    except Exception as e:
        logger.error(f"Error checking AI agent heartbeat: {e}")
        return False


def check_wireguard_vpn() -> bool:
    """Check if WireGuard VPN is running - matches dashboard check"""
    try:
        import subprocess
        result = subprocess.run(
            ['/usr/bin/sudo', '/usr/bin/systemctl', 'is-active', 'wg-quick@wg0'],
            capture_output=True,
            text=True,
            timeout=5
        )
        is_active = result.returncode == 0 and 'active' in result.stdout
        logger.debug(f"WireGuard check: returncode={result.returncode}, output={result.stdout.strip()}, is_active={is_active}")
        return is_active
    except Exception as e:
        logger.error(f"Error checking WireGuard: {e}")
        return False


def check_tg100_device(device_ip: str = '192.168.0.35') -> bool:
    """Check if TG100 device is reachable - matches dashboard check"""
    try:
        import subprocess
        result = subprocess.run(
            ['/usr/bin/ping', '-c', '1', '-W', '2', device_ip],
            capture_output=True,
            timeout=3
        )
        is_online = result.returncode == 0
        logger.debug(f"TG100 ping check: returncode={result.returncode}, is_online={is_online}")
        return is_online
    except Exception as e:
        logger.error(f"Error checking TG100: {e}")
        return False


def check_freepbx_api() -> bool:
    """Check if FreePBX API is responding - matches dashboard check"""
    try:
        import requests
        from flask import current_app

        # Use the same check as dashboard
        freepbx_host = current_app.config.get('FREEPBX_HOST')
        if not freepbx_host:
            logger.debug("FreePBX host not configured")
            return False

        # Try to access the token endpoint (same as dashboard does)
        token_url = f"{freepbx_host}/admin/api/api/token"
        response = requests.get(token_url, timeout=5)

        # API is up if we get ANY response (even 401 is fine, means API is running)
        is_up = response.status_code in [200, 401, 403]
        logger.debug(f"FreePBX API check: status={response.status_code}, is_up={is_up}")
        return is_up

    except Exception as e:
        logger.error(f"Error checking FreePBX API: {e}")
        return False


def check_ssh_connection() -> bool:
    """Check if SSH connection to FreePBX is working - matches dashboard check"""
    try:
        from ssh_manager import ssh_manager
        is_connected = ssh_manager.test_connection()
        logger.debug(f"SSH connection check: is_connected={is_connected}")
        return is_connected
    except Exception as e:
        logger.error(f"Error checking SSH: {e}")
        return False


# Global monitor instance
service_monitor = ServiceMonitor()


def init_service_monitor(app, webhook_url: str):
    """Initialize service monitoring with Flask app"""
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
                notification_interval=30
            )
        else:
            logger.warning("⚠️ AI Agent heartbeat file not configured - skipping monitoring")

        # WireGuard VPN
        service_monitor.register_service(
            service_name='WireGuard VPN',
            check_function=check_wireguard_vpn,
            downtime_threshold=180,  # 3 minutes
            notification_interval=60
        )

        # TG100 Gateway
        service_monitor.register_service(
            service_name='TG100 Gateway',
            check_function=lambda: check_tg100_device('192.168.0.35'),
            downtime_threshold=300,  # 5 minutes
            notification_interval=60
        )

        # FreePBX API
        service_monitor.register_service(
            service_name='FreePBX API',
            check_function=check_freepbx_api,
            downtime_threshold=180,  # 3 minutes
            notification_interval=30
        )

        # SSH Connection
        service_monitor.register_service(
            service_name='FreePBX SSH',
            check_function=check_ssh_connection,
            downtime_threshold=180,  # 3 minutes
            notification_interval=30
        )

        # Start monitoring
        service_monitor.start()

        logger.info("✅ Service monitoring initialized with Teams notifications")