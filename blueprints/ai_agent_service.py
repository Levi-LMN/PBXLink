"""
AI Agent Service Manager - Fresh Implementation
Runs the working standalone agent as a background service
"""

import asyncio
import threading
import logging
import subprocess
import signal
import os
import time
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class AIAgentService:
    """Manages the AI agent as a subprocess service"""

    def __init__(self, agent_script_path=None):
        """
        Initialize AI Agent Service

        Args:
            agent_script_path: Path to agent.py (defaults to same directory)
        """
        self.process = None
        self.running = False
        self.start_time = None
        self.monitor_thread = None
        self._stop_event = threading.Event()

        # Find agent.py
        if agent_script_path:
            self.agent_path = Path(agent_script_path)
        else:
            # Look in current directory or blueprints/ai_agent/
            current_dir = Path(__file__).parent
            candidates = [
                current_dir / "agent.py",
                current_dir / "ai_agent" / "agent.py",
                current_dir.parent / "agent.py"
            ]

            self.agent_path = None
            for candidate in candidates:
                if candidate.exists():
                    self.agent_path = candidate
                    break

            if not self.agent_path:
                raise FileNotFoundError(
                    "agent.py not found. Please provide agent_script_path"
                )

        logger.info(f"AI Agent Service initialized with: {self.agent_path}")

    def start(self):
        """Start the AI agent service"""
        if self.running:
            return False, "Service already running"

        try:
            # Validate environment variables
            required_vars = [
                'AZURE_OPENAI_ENDPOINT',
                'AZURE_OPENAI_API_KEY',
                'AZURE_SPEECH_KEY'
            ]

            missing = [var for var in required_vars if not os.environ.get(var)]
            if missing:
                return False, f"Missing environment variables: {', '.join(missing)}"

            # Start the agent as a subprocess
            logger.info(f"Starting AI Agent: {self.agent_path}")

            self.process = subprocess.Popen(
                ['python', str(self.agent_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
                bufsize=1,
                universal_newlines=True
            )

            self.running = True
            self.start_time = datetime.utcnow()
            self._stop_event.clear()

            # Start monitor thread to capture output
            self.monitor_thread = threading.Thread(
                target=self._monitor_process,
                daemon=True
            )
            self.monitor_thread.start()

            # Wait a moment to check if it started successfully
            time.sleep(2)

            if self.process.poll() is not None:
                # Process died immediately
                self.running = False
                return False, "Agent process failed to start"

            logger.info("✅ AI Agent Service started successfully")
            return True, "Service started successfully"

        except Exception as e:
            logger.error(f"Failed to start AI Agent: {e}")
            self.running = False
            return False, str(e)

    def stop(self):
        """Stop the AI agent service"""
        if not self.running:
            return False, "Service not running"

        try:
            logger.info("Stopping AI Agent Service...")

            self._stop_event.set()

            if self.process:
                # Try graceful shutdown first
                self.process.send_signal(signal.SIGINT)

                # Wait up to 5 seconds for graceful shutdown
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if still running
                    logger.warning("Force killing AI Agent process")
                    self.process.kill()
                    self.process.wait()

            self.running = False
            self.start_time = None

            # Wait for monitor thread
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=2)

            logger.info("✅ AI Agent Service stopped")
            return True, "Service stopped successfully"

        except Exception as e:
            logger.error(f"Error stopping AI Agent: {e}")
            self.running = False
            return False, str(e)

    def restart(self):
        """Restart the AI agent service"""
        logger.info("Restarting AI Agent Service...")

        success, message = self.stop()
        if not success:
            return False, f"Failed to stop: {message}"

        time.sleep(1)  # Brief pause

        return self.start()

    def get_status(self):
        """Get service status"""
        if not self.running or not self.process:
            return {
                'running': False,
                'uptime_seconds': None,
                'pid': None,
                'return_code': self.process.returncode if self.process else None
            }

        # Check if process is actually alive
        poll_result = self.process.poll()
        if poll_result is not None:
            # Process died
            self.running = False
            return {
                'running': False,
                'uptime_seconds': None,
                'pid': None,
                'return_code': poll_result
            }

        uptime = None
        if self.start_time:
            uptime = (datetime.utcnow() - self.start_time).seconds

        return {
            'running': True,
            'uptime_seconds': uptime,
            'pid': self.process.pid,
            'return_code': None
        }

    def _monitor_process(self):
        """Monitor process output (runs in background thread)"""
        if not self.process:
            return

        try:
            # Stream stdout
            for line in iter(self.process.stdout.readline, ''):
                if self._stop_event.is_set():
                    break
                if line:
                    logger.info(f"[AI Agent] {line.rstrip()}")

            # Check return code
            self.process.wait()

            if not self._stop_event.is_set():
                logger.error(f"AI Agent process exited unexpectedly: {self.process.returncode}")
                self.running = False

        except Exception as e:
            logger.error(f"Error monitoring AI Agent: {e}")
            self.running = False

    def __del__(self):
        """Cleanup on deletion"""
        if self.running:
            self.stop()


# Global service instance
_ai_service = None


def get_ai_service(agent_script_path=None):
    """Get global AI service instance"""
    global _ai_service
    if _ai_service is None:
        _ai_service = AIAgentService(agent_script_path)
    return _ai_service