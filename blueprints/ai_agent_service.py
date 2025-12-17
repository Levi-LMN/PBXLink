"""
AI Agent Service Manager - Complete Working Version
Fixed: Permissions, PATH, and proper subprocess management
"""

import subprocess
import logging
import os
import time
import tempfile
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

        # Setup log file - try multiple locations
        log_locations = [
            Path("/var/log/ai_agent"),
            Path("/tmp/ai_agent"),
            self.agent_path.parent / "logs",
            Path(tempfile.gettempdir()) / "ai_agent"
        ]

        for log_dir in log_locations:
            try:
                log_dir.mkdir(exist_ok=True, parents=True)
                # Test write permission
                test_file = log_dir / ".write_test"
                test_file.write_text("test")
                test_file.unlink()
                # Success - use this location
                self.log_file = log_dir / "agent.log"
                logger.info(f"Using log directory: {log_dir}")
                break
            except (PermissionError, OSError) as e:
                logger.debug(f"Cannot use {log_dir}: {e}")
                continue

        if not self.log_file:
            # Last resort - use temp file
            self.log_file = Path(tempfile.mktemp(suffix=".log", prefix="ai_agent_"))
            logger.warning(f"Using temp log file: {self.log_file}")

        logger.info(f"AI Agent Service initialized: {self.agent_path}")
        logger.info(f"Log file: {self.log_file}")

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
            logger.info(f"Logs will be written to: {self.log_file}")

            # Open log file for writing
            try:
                log_handle = open(self.log_file, 'a', buffering=1)  # Line buffered
            except Exception as e:
                logger.error(f"Cannot open log file: {e}")
                return False, f"Cannot create log file: {e}"

            # Prepare environment with proper PATH
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'

            # Ensure PATH includes standard binary locations for ffmpeg/ffprobe
            current_path = env.get('PATH', '')
            additional_paths = [
                '/usr/local/bin',
                '/usr/bin',
                '/bin',
                '/usr/local/sbin',
                '/usr/sbin',
                '/sbin'
            ]

            # Add paths that aren't already in PATH
            path_parts = current_path.split(':') if current_path else []
            for path in additional_paths:
                if path not in path_parts:
                    path_parts.insert(0, path)

            env['PATH'] = ':'.join(path_parts)
            logger.info(f"PATH set to: {env['PATH']}")

            # Start process - completely detached from Flask
            try:
                self.process = subprocess.Popen(
                    ['python3', str(self.agent_path)],
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,  # Merge stderr into stdout
                    env=env,
                    cwd=str(self.agent_path.parent),
                    start_new_session=True,  # Detach from parent
                    close_fds=True
                )
            except Exception as e:
                log_handle.close()
                logger.error(f"Failed to start process: {e}")
                return False, f"Process start failed: {e}"

            self.running = True
            self.start_time = datetime.utcnow()

            # Wait to verify it started
            time.sleep(3)

            if self.process.poll() is not None:
                self.running = False
                log_handle.close()
                # Read last few lines of log to see why it failed
                try:
                    with open(self.log_file, 'r') as f:
                        lines = f.readlines()
                        error_msg = ''.join(lines[-10:]) if lines else "No log output"
                except:
                    error_msg = "Could not read log"
                return False, f"Agent failed to start (exit code: {self.process.returncode}). Check logs: {self.log_file}\n{error_msg}"

            logger.info(f"✅ AI Agent started (PID: {self.process.pid})")
            logger.info(f"📋 Logs: tail -f {self.log_file}")
            return True, f"Service started (PID: {self.process.pid}). Logs: tail -f {self.log_file}"

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
                try:
                    self.process.terminate()
                except Exception as e:
                    logger.warning(f"Terminate failed: {e}")

                # Wait up to 10 seconds
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.warning("Force killing agent")
                    try:
                        self.process.kill()
                        self.process.wait()
                    except:
                        pass

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
        if not success and "not running" not in message.lower():
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
                'log_file': str(self.log_file) if self.log_file else None
            }

        # Check if actually running
        try:
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
        except:
            self.running = False
            return {
                'running': False,
                'uptime_seconds': None,
                'pid': None,
                'log_file': str(self.log_file) if self.log_file else None
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

    def get_logs(self, lines=100):
        """Get recent log lines"""
        try:
            if not self.log_file or not Path(self.log_file).exists():
                return []

            with open(self.log_file, 'r') as f:
                all_lines = f.readlines()
                return [line.strip() for line in all_lines[-lines:] if line.strip()]
        except Exception as e:
            logger.error(f"Error reading logs: {e}")
            return [f"Error reading logs: {e}"]

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