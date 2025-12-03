"""
Centralized SSH Management for FreePBX Dashboard
Provides connection pooling and command execution for Asterisk/FreePBX
Can be used by any blueprint or module

Usage:
    from ssh_manager import ssh_manager

    # Execute Asterisk command
    output = ssh_manager.execute_asterisk_command('pjsip show aors')

    # Execute any command
    output = ssh_manager.execute_command('ls -la /etc/asterisk')

    # Execute with sudo
    output = ssh_manager.execute_command('systemctl status asterisk', use_sudo=True)
"""

import paramiko
import threading
import time
import logging
from flask import current_app

logger = logging.getLogger(__name__)


# ============================================================================
# SSH CONNECTION POOL - Singleton pattern for reusable connections
# ============================================================================

class SSHConnectionPool:
    """
    Singleton SSH connection pool - maintains persistent SSH connections
    Thread-safe implementation with automatic reconnection
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.client = None
                    cls._instance.last_used = 0
                    cls._instance.max_idle_time = 300  # 5 minutes
                    cls._instance.connection_lock = threading.Lock()
        return cls._instance

    def _get_ssh_config(self):
        """Get SSH configuration from Flask app config"""
        try:
            freepbx_host = current_app.config.get('FREEPBX_HOST', 'http://10.200.200.2:80')
            # Extract IP from URL
            freepbx_ip = freepbx_host.replace('http://', '').replace('https://', '').split(':')[0]

            return {
                'host': freepbx_ip,
                'user': current_app.config.get('FREEPBX_SSH_USER', 'root'),
                'password': current_app.config.get('FREEPBX_SSH_PASSWORD'),
                'key_path': current_app.config.get('FREEPBX_SSH_KEY')
            }
        except RuntimeError:
            # If called outside app context
            logger.error("SSH config accessed outside Flask app context")
            return None

    def get_connection(self, host=None, user=None, password=None, key_path=None, timeout=10):
        """
        Get or create SSH connection
        If no parameters provided, uses Flask app config
        """
        with self.connection_lock:
            # Use app config if no parameters provided
            if host is None:
                config = self._get_ssh_config()
                if not config:
                    return None
                host = config['host']
                user = config['user']
                password = config['password']
                key_path = config['key_path']

            # Check if existing connection is still valid
            if self.client and (time.time() - self.last_used) < self.max_idle_time:
                try:
                    transport = self.client.get_transport()
                    if transport and transport.is_active():
                        logger.debug("♻️ Reusing existing SSH connection")
                        self.last_used = time.time()
                        return self.client
                except Exception as e:
                    logger.debug(f"Existing connection invalid: {e}")

            # Create new connection
            logger.debug(f"🔌 Creating new SSH connection to {host}")
            try:
                self.client = paramiko.SSHClient()
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                # Check if key_path is valid (not None, not string "None", not empty)
                use_key = False
                if key_path:
                    # Convert to string and check
                    key_path_str = str(key_path).strip()
                    if key_path_str and key_path_str.lower() != 'none':
                        use_key = True
                        logger.debug(f"Using SSH key: {key_path_str}")

                if use_key:
                    self.client.connect(
                        host,
                        username=user,
                        key_filename=key_path_str,
                        timeout=timeout,
                        look_for_keys=False,
                        allow_agent=False
                    )
                elif password:
                    logger.debug(f"Using password authentication for {user}@{host}")
                    self.client.connect(
                        host,
                        username=user,
                        password=password,
                        timeout=timeout,
                        look_for_keys=False,
                        allow_agent=False
                    )
                else:
                    logger.error("No authentication method provided (password or key)")
                    return None

                self.last_used = time.time()
                logger.info(f"✅ SSH connected to {host} as {user}")
                return self.client

            except paramiko.AuthenticationException as e:
                logger.error(f"❌ SSH authentication failed for {user}@{host}: {e}")
                self.client = None
                return None
            except paramiko.SSHException as e:
                logger.error(f"❌ SSH error: {e}")
                self.client = None
                return None
            except FileNotFoundError as e:
                logger.error(f"❌ SSH key file not found: {e}")
                self.client = None
                return None
            except Exception as e:
                logger.error(f"❌ Connection error: {e}")
                self.client = None
                return None

    def execute_command(self, command, host=None, user=None, password=None,
                        key_path=None, use_sudo=False, timeout=10):
        """
        Execute command using pooled connection

        Args:
            command: Command to execute
            host: SSH host (uses app config if None)
            user: SSH user (uses app config if None)
            password: SSH password (uses app config if None)
            key_path: SSH key path (uses app config if None)
            use_sudo: Whether to prepend sudo
            timeout: Command timeout in seconds

        Returns:
            Command output as string, or None on error
        """
        client = self.get_connection(host, user, password, key_path, timeout)
        if not client:
            logger.error("Failed to get SSH connection")
            return None

        try:
            # Set proper PATH for all commands
            path_prefix = "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "

            # Prepare command
            if use_sudo:
                full_command = f"{path_prefix}sudo {command}"
            else:
                full_command = f"{path_prefix}{command}"

            logger.debug(f"Executing: {full_command}")

            # Execute command
            stdin, stdout, stderr = client.exec_command(full_command, timeout=timeout)
            exit_status = stdout.channel.recv_exit_status()

            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')

            if exit_status != 0:
                logger.error(f"Command failed (exit {exit_status}): {error}")
                return None

            logger.debug(f"Command output: {len(output)} bytes")
            return output

        except paramiko.SSHException as e:
            logger.error(f"SSH error executing command: {e}")
            self._invalidate_connection()
            return None
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            self._invalidate_connection()
            return None

    def _invalidate_connection(self):
        """Invalidate current connection"""
        with self.connection_lock:
            if self.client:
                try:
                    self.client.close()
                except:
                    pass
                self.client = None
                logger.debug("Connection invalidated")

    def close(self):
        """Close SSH connection"""
        with self.connection_lock:
            if self.client:
                try:
                    self.client.close()
                    logger.info("SSH connection closed")
                except:
                    pass
                self.client = None


# ============================================================================
# SSH MANAGER - High-level interface for SSH operations
# ============================================================================

class SSHManager:
    """
    High-level SSH manager with convenience methods
    Provides easy access to common Asterisk/FreePBX operations
    """

    def __init__(self):
        self.pool = SSHConnectionPool()

    def execute_command(self, command, use_sudo=False, timeout=10):
        """
        Execute any command on FreePBX server

        Args:
            command: Command to execute
            use_sudo: Whether to use sudo
            timeout: Command timeout

        Returns:
            Command output or None
        """
        return self.pool.execute_command(
            command=command,
            use_sudo=use_sudo,
            timeout=timeout
        )

    def execute_asterisk_command(self, asterisk_cmd, timeout=10):
        """
        Execute Asterisk CLI command

        Args:
            asterisk_cmd: Asterisk command (without 'asterisk -rx')
            timeout: Command timeout

        Returns:
            Command output or None

        Example:
            output = ssh_manager.execute_asterisk_command('pjsip show aors')
        """
        command = f"asterisk -rx '{asterisk_cmd}'"
        return self.pool.execute_command(
            command=command,
            use_sudo=True,
            timeout=timeout
        )

    def get_pjsip_aors(self, timeout=10):
        """Get PJSIP AORs status"""
        return self.execute_asterisk_command('pjsip show aors', timeout=timeout)

    def get_pjsip_endpoints(self, timeout=10):
        """Get PJSIP endpoints"""
        return self.execute_asterisk_command('pjsip show endpoints', timeout=timeout)

    def get_active_channels(self, timeout=10):
        """Get active channels"""
        return self.execute_asterisk_command('core show channels', timeout=timeout)

    def get_active_calls(self, timeout=10):
        """Get active calls"""
        return self.execute_asterisk_command('core show calls', timeout=timeout)

    def reload_asterisk(self, module=None, timeout=30):
        """
        Reload Asterisk or specific module

        Args:
            module: Specific module to reload (e.g., 'res_pjsip')
            timeout: Command timeout
        """
        if module:
            return self.execute_asterisk_command(f'module reload {module}', timeout=timeout)
        else:
            return self.execute_asterisk_command('core reload', timeout=timeout)

    def get_system_info(self, timeout=10):
        """Get basic system information"""
        commands = {
            'uptime': 'uptime',
            'memory': 'free -h',
            'disk': 'df -h',
            'asterisk_version': 'asterisk -V'
        }

        results = {}
        for key, cmd in commands.items():
            output = self.execute_command(cmd, use_sudo=False, timeout=timeout)
            results[key] = output.strip() if output else None

        return results

    def test_connection(self):
        """Test SSH connection"""
        output = self.execute_command('echo "SSH connection test"', timeout=5)
        return output is not None and 'SSH connection test' in output

    def close(self):
        """Close SSH connection"""
        self.pool.close()


# ============================================================================
# GLOBAL INSTANCE - Import this in your modules
# ============================================================================

ssh_manager = SSHManager()


# ============================================================================
# FLASK INTEGRATION - Optional teardown handler
# ============================================================================

def init_ssh_manager(app):
    """
    Initialize SSH manager with Flask app
    Adds teardown handler to close connections

    Usage in app.py:
        from ssh_manager import init_ssh_manager
        init_ssh_manager(app)
    """

    @app.teardown_appcontext
    def close_ssh_connection(error):
        """Close SSH connection on app teardown"""
        if error:
            logger.error(f"App teardown with error: {error}")
        # Note: We keep connection alive by default for performance
        # Uncomment next line if you want to close on every request
        # ssh_manager.close()

    logger.info("SSH Manager initialized with Flask app")