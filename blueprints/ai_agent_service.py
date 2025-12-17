"""
AI Agent Service Manager - Complete Working Version
Runs agent.py as an independent subprocess without interference
"""

import subprocess
import logging
import os
import time
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class AIAgentService:
    """Manages the AI agent as a completely independent subprocess"""

    def __init__(self, agent_script_path=None):
        self.process = None
        self.running = False
        self.start_time = None
        self.log_file = None

        # Find agent.py
        if agent_script_path:
            self.agent_path = Path(agent_script_path)
        else:
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
                raise FileNotFoundError("agent.py not found")

        # Setup log file
        log_dir = Path("/var/log/ai_agent")
        log_dir.mkdir(exist_ok=True, parents=True)
        self.log_file = log_dir / "agent.log"

        logger.info(f"AI Agent Service initialized: {self.agent_path}")

    def start(self):
        """Start the AI agent service"""
        if self.running:
            return False, "Service already running"

        try:
            # Validate environment
            required = ['AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY', 'AZURE_SPEECH_KEY']
            missing = [v for v in required if not os.environ.get(v)]
            if missing:
                return False, f"Missing: {', '.join(missing)}"

            logger.info(f"Starting AI Agent: {self.agent_path}")

            # Open log file
            log_handle = open(self.log_file, 'a')

            # Start process - completely detached from Flask
            self.process = subprocess.Popen(
                ['python3', str(self.agent_path)],
                stdout=log_handle,
                stderr=log_handle,
                env=os.environ.copy(),
                cwd=str(self.agent_path.parent),
                start_new_session=True  # Detach from parent
            )

            self.running = True
            self.start_time = datetime.utcnow()

            # Wait to verify it started
            time.sleep(3)

            if self.process.poll() is not None:
                self.running = False
                return False, "Agent failed to start"

            logger.info(f"✅ AI Agent started (PID: {self.process.pid})")
            logger.info(f"📋 Logs: {self.log_file}")
            return True, f"Service started (PID: {self.process.pid})"

        except Exception as e:
            logger.error(f"Failed to start: {e}")
            self.running = False
            return False, str(e)

    def stop(self):
        """Stop the AI agent service"""
        if not self.running:
            return False, "Service not running"

        try:
            logger.info("Stopping AI Agent...")

            if self.process:
                # Send SIGTERM for graceful shutdown
                self.process.terminate()

                # Wait up to 10 seconds
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.warning("Force killing agent")
                    self.process.kill()
                    self.process.wait()

            self.running = False
            self.start_time = None

            logger.info("✅ AI Agent stopped")
            return True, "Service stopped"

        except Exception as e:
            logger.error(f"Error stopping: {e}")
            self.running = False
            return False, str(e)

    def restart(self):
        """Restart the service"""
        logger.info("Restarting AI Agent...")

        success, message = self.stop()
        if not success and "not running" not in message:
            return False, f"Stop failed: {message}"

        time.sleep(2)
        return self.start()

    def get_status(self):
        """Get service status"""
        if not self.running or not self.process:
            return {
                'running': False,
                'uptime_seconds': None,
                'pid': None,
                'log_file': str(self.log_file)
            }

        # Check if actually running
        poll = self.process.poll()
        if poll is not None:
            self.running = False
            return {
                'running': False,
                'uptime_seconds': None,
                'pid': None,
                'exit_code': poll,
                'log_file': str(self.log_file)
            }

        uptime = None
        if self.start_time:
            uptime = int((datetime.utcnow() - self.start_time).total_seconds())

        return {
            'running': True,
            'uptime_seconds': uptime,
            'pid': self.process.pid,
            'log_file': str(self.log_file)
        }

    def get_logs(self, lines=50):
        """Get recent log lines"""
        try:
            if not self.log_file.exists():
                return []

            with open(self.log_file, 'r') as f:
                all_lines = f.readlines()
                return [line.strip() for line in all_lines[-lines:]]
        except Exception as e:
            logger.error(f"Error reading logs: {e}")
            return []

    def __del__(self):
        """Cleanup"""
        if self.running:
            try:
                self.stop()
            except:
                pass


# Global instance
_ai_service = None


def get_ai_service(agent_script_path=None):
    """Get global AI service instance"""
    global _ai_service
    if _ai_service is None:
        _ai_service = AIAgentService(agent_script_path)
    return _ai_service