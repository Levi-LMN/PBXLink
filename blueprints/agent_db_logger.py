"""
Database Logger for AI Agent
Sends call logs to Flask app's database via HTTP API
"""

import requests
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


class AgentDatabaseLogger:
    """Logs agent activity to the Flask app database"""

    def __init__(self, flask_app_url=None):
        self.flask_app_url = flask_app_url or os.environ.get(
            'FLASK_APP_URL',
            'http://localhost:5000'
        )
        self.api_base = f"{self.flask_app_url}/api/ai-agent-logs"
        self.enabled = True

        # Test connection
        try:
            response = requests.get(
                f"{self.flask_app_url}/health",
                timeout=2
            )
            if response.status_code == 200:
                logger.info(f"✅ Database logger connected to {self.flask_app_url}")
            else:
                logger.warning(f"⚠️ Flask app returned status {response.status_code}")
                self.enabled = False
        except Exception as e:
            logger.warning(f"⚠️ Cannot connect to Flask app: {e}")
            self.enabled = False

    def log_call_start(self, call_id, caller_number, caller_name=None, customer_data=None):
        """Log the start of a new call"""
        if not self.enabled:
            return None

        try:
            response = requests.post(
                f"{self.api_base}/call/start",
                json={
                    'call_id': call_id,
                    'caller_number': caller_number,
                    'caller_name': caller_name,
                    'customer_data': customer_data
                },
                timeout=5
            )

            if response.status_code == 200:
                data = response.json()
                logger.debug(f"📝 Call start logged: {call_id}")
                return data.get('call_log_id')
            else:
                logger.error(f"❌ Failed to log call start: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"❌ Error logging call start: {e}")
            return None

    def log_turn(self, call_id, turn_number, user_text=None, user_confidence=None,
                 ai_text=None, function_called=None, function_args=None,
                 function_result=None):
        """Log a conversation turn"""
        if not self.enabled:
            return False

        try:
            response = requests.post(
                f"{self.api_base}/call/turn",
                json={
                    'call_id': call_id,
                    'turn_number': turn_number,
                    'user_text': user_text,
                    'user_confidence': user_confidence,
                    'ai_text': ai_text,
                    'function_called': function_called,
                    'function_args': function_args,
                    'function_result': function_result
                },
                timeout=5
            )

            if response.status_code == 200:
                logger.debug(f"📝 Turn {turn_number} logged for {call_id}")
                return True
            else:
                logger.error(f"❌ Failed to log turn: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"❌ Error logging turn: {e}")
            return False

    def log_call_end(self, call_id, call_duration=None, transcript=None,
                     summary=None, intent=None, sentiment=None,
                     functions_called=None, transferred_to=None,
                     tickets_created=None, turns_count=0,
                     no_speech_count=0, unclear_count=0, avg_confidence=None):
        """Log the end of a call"""
        if not self.enabled:
            return False

        try:
            response = requests.post(
                f"{self.api_base}/call/end",
                json={
                    'call_id': call_id,
                    'call_duration': call_duration,
                    'transcript': transcript,
                    'summary': summary,
                    'intent': intent,
                    'sentiment': sentiment,
                    'functions_called': functions_called or [],
                    'transferred_to': transferred_to,
                    'tickets_created': tickets_created or [],
                    'turns_count': turns_count,
                    'no_speech_count': no_speech_count,
                    'unclear_count': unclear_count,
                    'avg_confidence': avg_confidence
                },
                timeout=5
            )

            if response.status_code == 200:
                logger.info(f"✅ Call end logged: {call_id}")
                return True
            else:
                logger.error(f"❌ Failed to log call end: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"❌ Error logging call end: {e}")
            return False

    def log_call_error(self, call_id, error):
        """Log a call error"""
        if not self.enabled:
            return False

        try:
            response = requests.post(
                f"{self.api_base}/call/error",
                json={
                    'call_id': call_id,
                    'error': str(error)
                },
                timeout=5
            )

            return response.status_code == 200

        except Exception as e:
            logger.error(f"❌ Error logging call error: {e}")
            return False