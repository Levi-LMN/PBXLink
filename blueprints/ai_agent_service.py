"""
AI Agent Service Manager - WITH RELIABLE STATUS CHECK
Uses heartbeat file to verify agent is actually running
"""

import subprocess
import logging
import os
import time
import tempfile
import json
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class AIAgentService:
    """Manages the AI agent with reliable status checking via heartbeat"""

    def __init__(self, agent_script_path=None):
        self.process = None
        self.running = False
        self.start_time = None
        self.log_file = None
        self.heartbeat_file = None

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

        # Setup log and heartbeat files
        log_locations = [
            Path("/var/log/ai_agent"),
            Path("/tmp/ai_agent"),
            self.agent_path.parent / "logs",
            Path(tempfile.gettempdir()) / "ai_agent"
        ]

        for log_dir in log_locations:
            try:
                log_dir.mkdir(exist_ok=True, parents=True)
                test_file = log_dir / ".write_test"
                test_file.write_text("test")
                test_file.unlink()

                self.log_file = log_dir / "agent.log"
                self.heartbeat_file = log_dir / "agent.heartbeat"
                logger.info(f"Using log directory: {log_dir}")
                break
            except (PermissionError, OSError) as e:
                logger.debug(f"Cannot use {log_dir}: {e}")
                continue

        if not self.log_file:
            self.log_file = Path(tempfile.mktemp(suffix=".log", prefix="ai_agent_"))
            self.heartbeat_file = Path(tempfile.mktemp(suffix=".heartbeat", prefix="ai_agent_"))
            logger.warning(f"Using temp log file: {self.log_file}")

        logger.info(f"AI Agent Service initialized: {self.agent_path}")
        logger.info(f"Log file: {self.log_file}")
        logger.info(f"Heartbeat file: {self.heartbeat_file}")

    def is_really_running(self):
        """Check if agent is ACTUALLY running by checking heartbeat"""
        try:
            if not self.heartbeat_file.exists():
                return False

            # Read heartbeat
            with open(self.heartbeat_file, 'r') as f:
                data = json.load(f)

            last_beat = data.get('timestamp', 0)
            current_time = time.time()

            # If heartbeat is older than 15 seconds, consider it dead
            if (current_time - last_beat) > 15:
                logger.warning(f"Heartbeat stale: {current_time - last_beat:.1f}s old")
                return False

            # Check if PID matches
            if self.process and data.get('pid') != self.process.pid:
                logger.warning(f"PID mismatch: expected {self.process.pid}, got {data.get('pid')}")
                return False

            return True

        except Exception as e:
            logger.debug(f"Heartbeat check failed: {e}")
            return False

    def start(self):
        """Start the AI agent service"""
        if self.is_really_running():
            return False, "Service already running"

        try:
            # Validate environment
            required = ['AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY', 'AZURE_SPEECH_KEY']
            missing = [v for v in required if not os.environ.get(v)]
            if missing:
                return False, f"Missing: {', '.join(missing)}"

            # Clean old heartbeat
            if self.heartbeat_file.exists():
                try:
                    self.heartbeat_file.unlink()
                except:
                    pass

            logger.info(f"Starting AI Agent: {self.agent_path}")
            logger.info(f"Logs: {self.log_file}")
            logger.info(f"Heartbeat: {self.heartbeat_file}")

            # Open log file
            try:
                log_handle = open(self.log_file, 'a', buffering=1)
            except Exception as e:
                logger.error(f"Cannot open log file: {e}")
                return False, f"Cannot create log file: {e}"

            # Prepare environment with heartbeat file path
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            env['AI_AGENT_HEARTBEAT_FILE'] = str(self.heartbeat_file)

            # Add proper PATH
            current_path = env.get('PATH', '')
            additional_paths = ['/usr/local/bin', '/usr/bin', '/bin', '/usr/local/sbin', '/usr/sbin', '/sbin']
            path_parts = current_path.split(':') if current_path else []
            for path in additional_paths:
                if path not in path_parts:
                    path_parts.insert(0, path)
            env['PATH'] = ':'.join(path_parts)

            # Start process
            try:
                self.process = subprocess.Popen(
                    ['python3', str(self.agent_path)],
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=str(self.agent_path.parent),
                    start_new_session=True,
                    close_fds=True
                )
            except Exception as e:
                log_handle.close()
                logger.error(f"Failed to start process: {e}")
                return False, f"Process start failed: {e}"

            self.running = True
            self.start_time = datetime.utcnow()

            # Wait and verify it's actually running
            time.sleep(5)

            if not self.is_really_running():
                self.running = False
                log_handle.close()

                # Try to read error from log
                try:
                    with open(self.log_file, 'r') as f:
                        lines = f.readlines()
                        error_msg = ''.join(lines[-10:]) if lines else "No log output"
                except:
                    error_msg = "Could not read log"

                return False, f"Agent failed to start. Check logs: {self.log_file}\n{error_msg}"

            logger.info(f"✅ AI Agent started (PID: {self.process.pid})")
            return True, f"Service started (PID: {self.process.pid})"

        except Exception as e:
            logger.error(f"Failed to start: {e}")
            self.running = False
            return False, str(e)

    def stop(self):
        """Stop the AI agent service"""
        if not self.is_really_running():
            self.running = False
            return False, "Service not running"

        try:
            logger.info("Stopping AI Agent...")

            if self.process:
                try:
                    self.process.terminate()
                except Exception as e:
                    logger.warning(f"Terminate failed: {e}")

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

            # Clean heartbeat
            if self.heartbeat_file and self.heartbeat_file.exists():
                try:
                    self.heartbeat_file.unlink()
                except:
                    pass

            logger.info("✅ AI Agent stopped")
            return True, "Service stopped"

        except Exception as e:
            logger.error(f"Error stopping: {e}")
            self.running = False
            return False, str(e)

    def restart(self):
        """Restart the service"""
        logger.info("Restarting AI Agent...")

        # Force stop first
        if self.is_really_running():
            success, message = self.stop()
            if not success:
                logger.warning(f"Stop had issues: {message}")

        time.sleep(2)
        return self.start()

    def get_status(self):
        """Get service status with reliable heartbeat check"""
        actually_running = self.is_really_running()

        if not actually_running:
            # Clean up stale state
            if self.running:
                logger.warning("Agent was marked running but heartbeat check failed")
                self.running = False

            return {
                'running': False,
                'uptime_seconds': None,
                'pid': None,
                'log_file': str(self.log_file),
                'heartbeat_file': str(self.heartbeat_file),
                'last_heartbeat': None
            }

        # Get heartbeat data
        heartbeat_data = {}
        try:
            with open(self.heartbeat_file, 'r') as f:
                heartbeat_data = json.load(f)
        except:
            pass

        uptime = None
        if self.start_time:
            uptime = int((datetime.utcnow() - self.start_time).total_seconds())

        return {
            'running': True,
            'uptime_seconds': uptime,
            'pid': self.process.pid if self.process else heartbeat_data.get('pid'),
            'log_file': str(self.log_file),
            'heartbeat_file': str(self.heartbeat_file),
            'last_heartbeat': heartbeat_data.get('timestamp'),
            'active_calls': heartbeat_data.get('active_calls', 0),
            'total_calls': heartbeat_data.get('total_calls', 0)
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
        if self.is_really_running():
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