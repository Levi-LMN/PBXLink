"""
Microsoft Teams Notification Module
Sends alerts to MS Teams via webhooks for service downtime
FIXED: Support for Power Automate webhook format
"""

import requests
import logging
from datetime import datetime
from typing import Optional, Dict, Any
import json

logger = logging.getLogger(__name__)


class TeamsNotifier:
    """Handles Microsoft Teams webhook notifications"""

    def __init__(self, webhook_url: Optional[str] = None):
        """
        Initialize Teams notifier

        Args:
            webhook_url: Microsoft Teams incoming webhook URL or Power Automate webhook
        """
        self.webhook_url = webhook_url
        self.notification_history = {}  # Track sent notifications to avoid spam

        # Detect webhook type based on URL
        self.is_power_automate = self._detect_webhook_type(webhook_url)

    def _detect_webhook_type(self, webhook_url: Optional[str]) -> bool:
        """Detect if this is a Power Automate webhook"""
        if not webhook_url:
            return False
        return 'powerautomate' in webhook_url.lower() or 'logic.azure.com' in webhook_url.lower()

    def set_webhook_url(self, webhook_url: str):
        """Set or update the webhook URL"""
        self.webhook_url = webhook_url
        self.is_power_automate = self._detect_webhook_type(webhook_url)

        if self.is_power_automate:
            logger.info("Detected Power Automate webhook")
        else:
            logger.info("Detected standard Teams webhook")

    def send_notification(
            self,
            title: str,
            message: str,
            severity: str = "warning",
            service_name: str = "",
            additional_info: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send notification to Teams

        Args:
            title: Notification title
            message: Main message content
            severity: Severity level (info, warning, error, critical)
            service_name: Name of the affected service
            additional_info: Additional information to include

        Returns:
            True if notification sent successfully
        """
        if not self.webhook_url:
            logger.warning("Teams webhook URL not configured")
            return False

        try:
            # Build the appropriate payload based on webhook type
            if self.is_power_automate:
                payload = self._build_power_automate_payload(
                    title=title,
                    message=message,
                    severity=severity,
                    service_name=service_name,
                    additional_info=additional_info
                )
            else:
                payload = self._build_adaptive_card(
                    title=title,
                    message=message,
                    severity=severity,
                    service_name=service_name,
                    theme_color=self._get_theme_color(severity),
                    additional_info=additional_info
                )

            # Send to Teams
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )

            # Accept both 200 (OK) and 202 (Accepted) as success
            if response.status_code in [200, 202]:
                logger.info(f"Teams notification sent successfully: {title} (HTTP {response.status_code})")
                return True
            else:
                logger.error(f"Teams notification failed: {response.status_code} - {response.text}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending Teams notification: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in Teams notification: {e}")
            return False

    def _get_theme_color(self, severity: str) -> str:
        """Get theme color based on severity"""
        theme_colors = {
            "info": "0078D4",  # Blue
            "warning": "FFB900",  # Yellow
            "error": "D83B01",  # Orange
            "critical": "E81123"  # Red
        }
        return theme_colors.get(severity.lower(), theme_colors["warning"])

    def _get_severity_emoji(self, severity: str) -> str:
        """Get emoji based on severity"""
        severity_emojis = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨"
        }
        return severity_emojis.get(severity.lower(), "⚠️")

    def _build_power_automate_payload(
            self,
            title: str,
            message: str,
            severity: str,
            service_name: str,
            additional_info: Optional[Dict[str, Any]]
    ) -> Dict:
        """
        Build payload for Power Automate webhook (Adaptive Card format)
        This format works with the "Post adaptive card in a chat or channel" action
        """
        emoji = self._get_severity_emoji(severity)
        theme_color = self._get_theme_color(severity)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S EAT")

        # Build facts for additional info
        facts = []
        if service_name:
            facts.append({
                "title": "Service:",
                "value": service_name
            })

        facts.append({
            "title": "Severity:",
            "value": severity.upper()
        })

        facts.append({
            "title": "Time:",
            "value": timestamp
        })

        if additional_info:
            for key, value in additional_info.items():
                facts.append({
                    "title": f"{key.replace('_', ' ').title()}:",
                    "value": str(value)
                })

        # Build Adaptive Card
        adaptive_card = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": f"{emoji} {title}",
                    "weight": "Bolder",
                    "size": "Large",
                    "wrap": True,
                    "color": "Attention" if severity in ["error", "critical"] else "Good" if severity == "info" else "Warning"
                },
                {
                    "type": "TextBlock",
                    "text": message,
                    "wrap": True,
                    "spacing": "Small"
                },
                {
                    "type": "FactSet",
                    "facts": facts,
                    "spacing": "Medium"
                }
            ]
        }

        # Power Automate expects attachments array with contentUrl
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,  # Required by schema but can be null
                    "content": adaptive_card
                }
            ]
        }

    def _build_adaptive_card(
            self,
            title: str,
            message: str,
            severity: str,
            service_name: str,
            theme_color: str,
            additional_info: Optional[Dict[str, Any]]
    ) -> Dict:
        """Build a MessageCard for traditional Teams webhook"""
        emoji = self._get_severity_emoji(severity)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S EAT")

        # Build facts section
        facts = [
            {
                "title": "Service",
                "value": service_name or "N/A"
            },
            {
                "title": "Severity",
                "value": severity.upper()
            },
            {
                "title": "Time",
                "value": timestamp
            }
        ]

        # Add additional info as facts
        if additional_info:
            for key, value in additional_info.items():
                facts.append({
                    "title": key.replace('_', ' ').title(),
                    "value": str(value)
                })

        # MessageCard payload
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "themeColor": theme_color,
            "summary": f"{emoji} {title}",
            "sections": [
                {
                    "activityTitle": f"{emoji} **{title}**",
                    "activitySubtitle": message,
                    "facts": facts,
                    "markdown": True
                }
            ]
        }

    def send_service_down_alert(
            self,
            service_name: str,
            downtime_duration: str,
            last_seen: str,
            error_details: Optional[str] = None
    ) -> bool:
        """
        Send service down alert

        Args:
            service_name: Name of the service
            downtime_duration: How long service has been down
            last_seen: When service was last seen online
            error_details: Additional error information
        """
        additional_info = {
            "Downtime": downtime_duration,
            "Last Seen": last_seen
        }

        if error_details:
            additional_info["Error"] = error_details

        return self.send_notification(
            title=f"Service Down: {service_name}",
            message=f"{service_name} has been unreachable for {downtime_duration}",
            severity="error",
            service_name=service_name,
            additional_info=additional_info
        )

    def send_service_recovered_alert(
            self,
            service_name: str,
            downtime_duration: str
    ) -> bool:
        """
        Send service recovery alert

        Args:
            service_name: Name of the service
            downtime_duration: How long service was down
        """
        additional_info = {
            "Total Downtime": downtime_duration,
            "Status": "RECOVERED ✅"
        }

        return self.send_notification(
            title=f"Service Recovered: {service_name}",
            message=f"{service_name} is now online after {downtime_duration} of downtime",
            severity="info",
            service_name=service_name,
            additional_info=additional_info
        )

    def send_ai_agent_down_alert(
            self,
            last_heartbeat: str,
            missing_duration: str
    ) -> bool:
        """Send AI Agent down alert"""
        additional_info = {
            "Last Heartbeat": last_heartbeat,
            "Missing For": missing_duration,
            "Service": "AI Voice Agent"
        }

        return self.send_notification(
            title="AI Agent Not Responding",
            message="AI Voice Agent has stopped sending heartbeat signals",
            severity="critical",
            service_name="AI Agent",
            additional_info=additional_info
        )

    def send_wireguard_down_alert(
            self,
            downtime_duration: str,
            last_successful_ping: str
    ) -> bool:
        """Send WireGuard VPN down alert"""
        additional_info = {
            "VPN Status": "OFFLINE",
            "Last Successful Ping": last_successful_ping,
            "Downtime": downtime_duration
        }

        return self.send_notification(
            title="WireGuard VPN Down",
            message="WireGuard VPN is not responding to ping requests",
            severity="error",
            service_name="WireGuard VPN",
            additional_info=additional_info
        )

    def send_tg100_down_alert(
            self,
            device_ip: str,
            downtime_duration: str,
            packet_loss: float
    ) -> bool:
        """Send TG100 device down alert"""
        additional_info = {
            "Device IP": device_ip,
            "Downtime": downtime_duration,
            "Packet Loss": f"{packet_loss}%"
        }

        return self.send_notification(
            title="TG100 Gateway Down",
            message=f"TG100 Gateway ({device_ip}) is unreachable",
            severity="critical",
            service_name="TG100 Gateway",
            additional_info=additional_info
        )

    def should_send_notification(
            self,
            service_key: str,
            min_interval_minutes: int = 30
    ) -> bool:
        """
        Check if enough time has passed to send another notification
        Prevents notification spam

        Args:
            service_key: Unique identifier for the service
            min_interval_minutes: Minimum minutes between notifications

        Returns:
            True if notification should be sent
        """
        if service_key not in self.notification_history:
            self.notification_history[service_key] = datetime.now()
            return True

        last_notification = self.notification_history[service_key]
        time_since_last = datetime.now() - last_notification

        if time_since_last.total_seconds() / 60 >= min_interval_minutes:
            self.notification_history[service_key] = datetime.now()
            return True

        return False

    def clear_notification_history(self, service_key: Optional[str] = None):
        """
        Clear notification history

        Args:
            service_key: Specific service to clear, or None to clear all
        """
        if service_key:
            self.notification_history.pop(service_key, None)
        else:
            self.notification_history.clear()


# Global notifier instance
teams_notifier = TeamsNotifier()


def init_teams_notifier(webhook_url: str):
    """
    Initialize the Teams notifier with webhook URL

    Args:
        webhook_url: Microsoft Teams incoming webhook URL or Power Automate webhook
    """
    teams_notifier.set_webhook_url(webhook_url)
    logger.info("Teams notifier initialized")