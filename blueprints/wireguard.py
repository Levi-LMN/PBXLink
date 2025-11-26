"""
WireGuard management blueprint
Handles WireGuard VPN configuration, user management, and QR code generation
Updated to work with /home/sibasi/wireguard and existing config structure
"""

from flask import Blueprint, render_template, request, jsonify, send_file, Response
import os
import subprocess
import logging
import ipaddress
import qrcode
import io
from datetime import datetime

logger = logging.getLogger(__name__)

wireguard_bp = Blueprint('wireguard', __name__)

# Configuration - Updated for your setup
WG_CONFIG_DIR = '/home/sibasi/wireguard'  # Your WireGuard directory
WG_USERS_DIR = os.path.join(WG_CONFIG_DIR, 'users')
WG_INTERFACE = 'wg0'
WG_SERVER_CONFIG = '/etc/wireguard/wg0.conf'  # System WireGuard config


class WireGuardManager:
    """Manages WireGuard configuration and user credentials"""

    def __init__(self):
        self.config_dir = WG_CONFIG_DIR
        self.users_dir = WG_USERS_DIR
        self.interface = WG_INTERFACE
        self.server_config = WG_SERVER_CONFIG

    def ensure_users_directory(self):
        """Create users directory if it doesn't exist"""
        try:
            if not os.path.exists(self.users_dir):
                os.makedirs(self.users_dir, mode=0o755, exist_ok=True)
                logger.info(f"Created users directory: {self.users_dir}")
            return True
        except Exception as e:
            logger.error(f"Error creating users directory: {e}")
            return False

    def read_server_config(self):
        """Read the WireGuard server configuration"""
        try:
            result = subprocess.run(
                ['sudo', 'cat', self.server_config],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout
        except Exception as e:
            logger.error(f"Error reading server config: {e}")
            return None

    def write_server_config(self, config_content):
        """Write the WireGuard server configuration"""
        try:
            # Write to temporary file first
            temp_file = '/tmp/wg0_temp.conf'
            with open(temp_file, 'w') as f:
                f.write(config_content)

            # Move to actual location with sudo
            subprocess.run(
                ['sudo', 'mv', temp_file, self.server_config],
                check=True
            )
            subprocess.run(
                ['sudo', 'chmod', '600', self.server_config],
                check=True
            )
            return True
        except Exception as e:
            logger.error(f"Error writing server config: {e}")
            return False

    def get_server_public_key(self):
        """Get server's public key from the config directory"""
        try:
            # Try the user's wireguard directory first
            publickey_path = os.path.join(self.config_dir, 'publickey')
            if os.path.exists(publickey_path):
                with open(publickey_path, 'r') as f:
                    return f.read().strip()

            # Fallback to /etc/wireguard
            result = subprocess.run(
                ['sudo', 'cat', '/etc/wireguard/publickey'],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except Exception as e:
            logger.error(f"Error reading server public key: {e}")
            return None

    def parse_server_config(self):
        """Parse server configuration to extract settings"""
        config = self.read_server_config()
        if not config:
            return None

        settings = {
            'address': None,
            'port': None,
            'private_key': None,
            'peers': []
        }

        current_section = None
        current_peer = {}

        for line in config.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if line.startswith('['):
                if current_section == 'Peer' and current_peer:
                    settings['peers'].append(current_peer)
                    current_peer = {}
                current_section = line.strip('[]')
            elif '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()

                if current_section == 'Interface':
                    if key == 'Address':
                        settings['address'] = value
                    elif key == 'ListenPort':
                        settings['port'] = value
                    elif key == 'PrivateKey':
                        settings['private_key'] = value
                elif current_section == 'Peer':
                    current_peer[key] = value

        if current_peer:
            settings['peers'].append(current_peer)

        return settings

    def get_next_ip(self):
        """Get next available IP address for a new user"""
        settings = self.parse_server_config()
        if not settings or not settings['address']:
            return None

        # Parse server network (10.200.200.1/24)
        server_network = settings['address'].split('/')[0]
        network_base = '.'.join(server_network.split('.')[:-1])

        # Get all used IPs from peers
        used_ips = set([server_network.split('.')[-1]])
        for peer in settings['peers']:
            if 'AllowedIPs' in peer:
                # Handle multiple IPs (e.g., "10.200.200.2/32, 192.168.0.0/24")
                for ip_range in peer['AllowedIPs'].split(','):
                    ip = ip_range.strip().split('/')[0]
                    if ip.startswith(network_base):
                        peer_ip = ip.split('.')[-1]
                        used_ips.add(peer_ip)

        # Find next available IP (starting from 100 as per your convention)
        for i in range(100, 255):
            if str(i) not in used_ips:
                return f"{network_base}.{i}/32"

        # If no IPs in 100+ range, try from 2
        for i in range(2, 100):
            if str(i) not in used_ips:
                return f"{network_base}.{i}/32"

        return None

    def generate_keypair(self):
        """Generate a new WireGuard keypair"""
        try:
            # Generate private key
            private_result = subprocess.run(
                ['wg', 'genkey'],
                capture_output=True,
                text=True,
                check=True
            )
            private_key = private_result.stdout.strip()

            # Generate public key from private key
            public_result = subprocess.run(
                ['wg', 'pubkey'],
                input=private_key,
                capture_output=True,
                text=True,
                check=True
            )
            public_key = public_result.stdout.strip()

            return private_key, public_key
        except Exception as e:
            logger.error(f"Error generating keypair: {e}")
            return None, None

    def get_server_endpoint(self):
        """Get server endpoint (IP:port)"""
        settings = self.parse_server_config()
        if not settings:
            return None

        # Try to get public IP or use request host
        # You should replace this with your actual server IP
        server_ip = "YOUR_SERVER_PUBLIC_IP"  # Replace with actual IP
        port = settings.get('port', '51820')

        return f"{server_ip}:{port}"

    def create_user(self, username, description=''):
        """Create a new WireGuard user"""
        try:
            self.ensure_users_directory()

            # Create user directory
            user_dir = os.path.join(self.users_dir, username)
            os.makedirs(user_dir, mode=0o755, exist_ok=True)

            # Generate keypair
            private_key, public_key = self.generate_keypair()
            if not private_key or not public_key:
                return None

            # Get next available IP
            client_ip = self.get_next_ip()
            if not client_ip:
                return None

            # Get server settings
            server_settings = self.parse_server_config()
            server_public_key = self.get_server_public_key()

            # Get server endpoint - update this with your actual server IP
            server_endpoint = self.get_server_endpoint()
            if not server_endpoint:
                server_endpoint = f"YOUR_SERVER_IP:{server_settings['port']}"

            # Save keys to user directory
            private_key_file = os.path.join(user_dir, 'privatekey')
            public_key_file = os.path.join(user_dir, 'publickey')

            with open(private_key_file, 'w') as f:
                f.write(private_key)
            with open(public_key_file, 'w') as f:
                f.write(public_key)

            os.chmod(private_key_file, 0o600)
            os.chmod(public_key_file, 0o644)

            # Create client config
            client_config = f"""[Interface]
PrivateKey = {private_key}
Address = {client_ip}
DNS = 1.1.1.1, 8.8.8.8

[Peer]
PublicKey = {server_public_key}
Endpoint = {server_endpoint}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""

            config_file = os.path.join(user_dir, f'{username}.conf')
            with open(config_file, 'w') as f:
                f.write(client_config)

            os.chmod(config_file, 0o644)

            # Add peer to server config
            server_config = self.read_server_config()

            # Format peer section to match your existing style
            peer_section = f"""
# {username} - {description} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
[Peer]
PublicKey = {public_key}
AllowedIPs = {client_ip}
PersistentKeepalive = 25
"""
            new_config = server_config + peer_section
            self.write_server_config(new_config)

            # Restart WireGuard
            self.restart_wireguard()

            logger.info(f"Created user: {username} with IP: {client_ip}")

            return {
                'username': username,
                'ip': client_ip,
                'public_key': public_key,
                'config_file': config_file
            }
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return None

    def list_users(self):
        """List all WireGuard users from the users directory"""
        try:
            if not os.path.exists(self.users_dir):
                logger.warning(f"Users directory does not exist: {self.users_dir}")
                return []

            users = []

            # List all directories in users directory
            for username in os.listdir(self.users_dir):
                user_dir = os.path.join(self.users_dir, username)

                if not os.path.isdir(user_dir):
                    continue

                # Read public key
                public_key_file = os.path.join(user_dir, 'publickey')
                public_key = 'N/A'
                if os.path.exists(public_key_file):
                    try:
                        with open(public_key_file, 'r') as f:
                            public_key = f.read().strip()
                    except Exception as e:
                        logger.error(f"Error reading public key for {username}: {e}")

                # Check if config exists
                config_file = os.path.join(user_dir, f'{username}.conf')
                config_exists = os.path.exists(config_file)

                # Get IP from server config if available
                client_ip = 'N/A'
                server_config = self.read_server_config()
                if server_config and public_key != 'N/A':
                    # Find this peer's AllowedIPs
                    lines = server_config.split('\n')
                    for i, line in enumerate(lines):
                        if f'PublicKey = {public_key}' in line:
                            # Look for AllowedIPs in next few lines
                            for j in range(i+1, min(i+5, len(lines))):
                                if 'AllowedIPs' in lines[j]:
                                    client_ip = lines[j].split('=')[1].strip()
                                    break
                            break

                users.append({
                    'username': username,
                    'ip': client_ip,
                    'public_key': public_key,
                    'config_exists': config_exists
                })

            return sorted(users, key=lambda x: x['username'])
        except Exception as e:
            logger.error(f"Error listing users: {e}")
            return []

    def delete_user(self, username):
        """Delete a WireGuard user"""
        try:
            user_dir = os.path.join(self.users_dir, username)

            if not os.path.exists(user_dir):
                logger.error(f"User directory does not exist: {user_dir}")
                return False

            # Get user's public key before deletion
            public_key_file = os.path.join(user_dir, 'publickey')
            public_key = None
            if os.path.exists(public_key_file):
                with open(public_key_file, 'r') as f:
                    public_key = f.read().strip()

            # Remove from server config
            if public_key:
                server_config = self.read_server_config()
                new_config_lines = []
                skip_lines = 0

                for i, line in enumerate(server_config.split('\n')):
                    if skip_lines > 0:
                        skip_lines -= 1
                        continue

                    # Check if this line contains the public key
                    if public_key in line:
                        # Skip this peer section (typically 4-5 lines)
                        # Go back and remove the comment line too
                        if new_config_lines and new_config_lines[-1].strip().startswith('#'):
                            new_config_lines.pop()
                        if new_config_lines and new_config_lines[-1].strip().startswith('[Peer]'):
                            new_config_lines.pop()
                        skip_lines = 3  # Skip AllowedIPs, PersistentKeepalive, and blank line
                        continue

                    new_config_lines.append(line)

                new_config = '\n'.join(new_config_lines)
                self.write_server_config(new_config)

            # Delete user directory
            subprocess.run(['rm', '-rf', user_dir], check=True)

            # Restart WireGuard
            self.restart_wireguard()

            logger.info(f"Deleted user: {username}")
            return True
        except Exception as e:
            logger.error(f"Error deleting user: {e}")
            return False

    def get_user_config(self, username):
        """Get user's configuration file content"""
        try:
            config_file = os.path.join(self.users_dir, username, f'{username}.conf')
            if not os.path.exists(config_file):
                return None

            with open(config_file, 'r') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading user config: {e}")
            return None

    def restart_wireguard(self):
        """Restart WireGuard service"""
        try:
            subprocess.run(
                ['sudo', 'systemctl', 'restart', f'wg-quick@{self.interface}'],
                check=True
            )
            logger.info("WireGuard service restarted")
            return True
        except Exception as e:
            logger.error(f"Error restarting WireGuard: {e}")
            return False

    def get_wireguard_status(self):
        """Get WireGuard interface status"""
        try:
            result = subprocess.run(
                ['sudo', 'wg', 'show', self.interface],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout
        except Exception as e:
            logger.error(f"Error getting WireGuard status: {e}")
            return None


# Initialize manager
wg_manager = WireGuardManager()


# Routes
@wireguard_bp.route('/')
def index():
    """WireGuard management page"""
    return render_template('wireguard/index.html')


@wireguard_bp.route('/api/config')
def get_config():
    """Get server configuration"""
    config = wg_manager.read_server_config()
    if config:
        return jsonify({'success': True, 'config': config})
    return jsonify({'success': False, 'error': 'Failed to read configuration'}), 500


@wireguard_bp.route('/api/config', methods=['POST'])
def update_config():
    """Update server configuration"""
    data = request.get_json()
    config_content = data.get('config')

    if not config_content:
        return jsonify({'success': False, 'error': 'No configuration provided'}), 400

    if wg_manager.write_server_config(config_content):
        wg_manager.restart_wireguard()
        return jsonify({'success': True, 'message': 'Configuration updated successfully'})

    return jsonify({'success': False, 'error': 'Failed to update configuration'}), 500


@wireguard_bp.route('/api/users')
def list_users():
    """List all users"""
    users = wg_manager.list_users()
    return jsonify({'success': True, 'users': users})


@wireguard_bp.route('/api/users', methods=['POST'])
def create_user():
    """Create a new user"""
    data = request.get_json()
    username = data.get('username')
    description = data.get('description', '')

    if not username:
        return jsonify({'success': False, 'error': 'Username is required'}), 400

    # Validate username (alphanumeric and underscores/hyphens only)
    if not username.replace('_', '').replace('-', '').isalnum():
        return jsonify({'success': False, 'error': 'Invalid username format'}), 400

    result = wg_manager.create_user(username, description)
    if result:
        return jsonify({'success': True, 'user': result})

    return jsonify({'success': False, 'error': 'Failed to create user'}), 500


@wireguard_bp.route('/api/users/<username>', methods=['DELETE'])
def delete_user(username):
    """Delete a user"""
    if wg_manager.delete_user(username):
        return jsonify({'success': True, 'message': f'User {username} deleted successfully'})

    return jsonify({'success': False, 'error': 'Failed to delete user'}), 500


@wireguard_bp.route('/api/users/<username>/config')
def get_user_config(username):
    """Get user's configuration file"""
    config = wg_manager.get_user_config(username)
    if config:
        return jsonify({'success': True, 'config': config})

    return jsonify({'success': False, 'error': 'Failed to read user configuration'}), 404


@wireguard_bp.route('/api/users/<username>/download')
def download_user_config(username):
    """Download user's configuration file"""
    config = wg_manager.get_user_config(username)
    if config:
        return Response(
            config,
            mimetype='text/plain',
            headers={'Content-Disposition': f'attachment; filename={username}.conf'}
        )

    return jsonify({'success': False, 'error': 'Failed to read user configuration'}), 404


@wireguard_bp.route('/api/users/<username>/qr')
def get_user_qr(username):
    """Generate QR code for user's configuration"""
    config = wg_manager.get_user_config(username)
    if not config:
        return jsonify({'success': False, 'error': 'Failed to read user configuration'}), 404

    try:
        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(config)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        # Save to bytes buffer
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)

        return send_file(img_buffer, mimetype='image/png')
    except Exception as e:
        logger.error(f"Error generating QR code: {e}")
        return jsonify({'success': False, 'error': 'Failed to generate QR code'}), 500


@wireguard_bp.route('/api/status')
def get_status():
    """Get WireGuard status"""
    status = wg_manager.get_wireguard_status()
    if status:
        return jsonify({'success': True, 'status': status})

    return jsonify({'success': False, 'error': 'Failed to get WireGuard status'}), 500


@wireguard_bp.route('/api/restart', methods=['POST'])
def restart_wireguard():
    """Restart WireGuard service"""
    if wg_manager.restart_wireguard():
        return jsonify({'success': True, 'message': 'WireGuard restarted successfully'})

    return jsonify({'success': False, 'error': 'Failed to restart WireGuard'}), 500


@wireguard_bp.route('/api/init-users-dir', methods=['POST'])
def init_users_directory():
    """Initialize users directory structure"""
    if wg_manager.ensure_users_directory():
        return jsonify({'success': True, 'message': 'Users directory initialized successfully'})

    return jsonify({'success': False, 'error': 'Failed to initialize users directory'}), 500