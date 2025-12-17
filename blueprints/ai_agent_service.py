"""
AI Agent Service Manager - Fixed Implementation
Properly captures both stdout AND stderr for debugging
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
from queue import Queue, Empty

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
        self.stdout_thread = None
        self.stderr_thread = None
        self._stop_event = threading.Event()

        # Output queues for better monitoring
        self.stdout_queue = Queue()
        self.stderr_queue = Queue()
        self.last_output = []  # Store last 50 lines
        self.max_output_lines = 50

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

            # CRITICAL FIX: Use unbuffered output and capture both stdout/stderr
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'  # Force unbuffered output

            self.process = subprocess.Popen(
                ['python', '-u', str(self.agent_path)],  # -u for unbuffered
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,  # Unbuffered
                universal_newlines=True
            )

            self.running = True
            self.start_time = datetime.utcnow()
            self._stop_event.clear()
            self.last_output = []

            # Start BOTH stdout and stderr monitor threads
            self.stdout_thread = threading.Thread(
                target=self._monitor_stdout,
                daemon=True
            )
            self.stderr_thread = threading.Thread(
                target=self._monitor_stderr,
                daemon=True
            )

            self.stdout_thread.start()
            self.stderr_thread.start()

            # Wait a moment to check if it started successfully
            time.sleep(2)

            if self.process.poll() is not None:
                # Process died immediately - get error output
                error_lines = []
                try:
                    while True:
                        line = self.stderr_queue.get_nowait()
                        error_lines.append(line)
                except Empty:
                    pass

                self.running = False
                error_msg = '\n'.join(error_lines) if error_lines else "Unknown error"
                return False, f"Agent process failed to start:\n{error_msg}"

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

            # Wait for monitor threads
            if self.stdout_thread and self.stdout_thread.is_alive():
                self.stdout_thread.join(timeout=2)
            if self.stderr_thread and self.stderr_thread.is_alive():
                self.stderr_thread.join(timeout=2)

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
        """Get service status with recent output"""
        if not self.running or not self.process:
            return {
                'running': False,
                'uptime_seconds': None,
                'pid': None,
                'return_code': self.process.returncode if self.process else None,
                'last_output': self.last_output[-10:] if self.last_output else []
            }

        # Check if process is actually alive
        poll_result = self.process.poll()
        if poll_result is not None:
            # Process died
            self.running = False

            # Get error output
            error_lines = []
            try:
                while True:
                    line = self.stderr_queue.get_nowait()
                    error_lines.append(line)
            except Empty:
                pass

            return {
                'running': False,
                'uptime_seconds': None,
                'pid': None,
                'return_code': poll_result,
                'last_output': self.last_output[-10:] if self.last_output else [],
                'error_output': error_lines[-10:] if error_lines else []
            }

        uptime = None
        if self.start_time:
            uptime = (datetime.utcnow() - self.start_time).seconds

        return {
            'running': True,
            'uptime_seconds': uptime,
            'pid': self.process.pid,
            'return_code': None,
            'last_output': self.last_output[-10:] if self.last_output else []
        }

    def get_recent_logs(self, lines=50):
        """Get recent log output"""
        return self.last_output[-lines:] if self.last_output else []

    def _monitor_stdout(self):
        """Monitor stdout (runs in background thread)"""
        if not self.process:
            return

        try:
            for line in iter(self.process.stdout.readline, ''):
                if self._stop_event.is_set():
                    break
                if line:
                    clean_line = line.rstrip()

                    # Store in queue
                    self.stdout_queue.put(clean_line)

                    # Store in last_output
                    self.last_output.append(clean_line)
                    if len(self.last_output) > self.max_output_lines:
                        self.last_output.pop(0)

                    # Log it
                    logger.info(f"[AI Agent] {clean_line}")

        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"Error monitoring stdout: {e}")

    def _monitor_stderr(self):
        """Monitor stderr (runs in background thread) - CRITICAL for debugging"""
        if not self.process:
            return

        try:
            for line in iter(self.process.stderr.readline, ''):
                if self._stop_event.is_set():
                    break
                if line:
                    clean_line = line.rstrip()

                    # Store in queue
                    self.stderr_queue.put(clean_line)

                    # Store in last_output with ERROR prefix
                    error_msg = f"ERROR: {clean_line}"
                    self.last_output.append(error_msg)
                    if len(self.last_output) > self.max_output_lines:
                        self.last_output.pop(0)

                    # Log as ERROR
                    logger.error(f"[AI Agent ERROR] {clean_line}")

            # Check return code when stderr closes
            self.process.wait()

            if not self._stop_event.is_set() and self.process.returncode != 0:
                logger.error(f"AI Agent process exited with code: {self.process.returncode}")
                self.running = False

        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"Error monitoring stderr: {e}")
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