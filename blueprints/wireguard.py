"""
WireGuard management blueprint - Modern UI Version
"""
from flask import Blueprint, render_template, request, jsonify, send_file, Response
import os
import subprocess
import logging
import ipaddress
import qrcode
import io
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

wireguard_bp = Blueprint('wireguard', __name__)

# Configuration
WG_CONFIG_DIR = '/home/sibasi/wireguard'
WG_USERS_DIR = os.path.join(WG_CONFIG_DIR, 'users')
WG_INTERFACE = 'wg0'
WG_SERVER_CONFIG = '/etc/wireguard/wg0.conf'

class WireGuardManager:
    def __init__(self):
        self.config_dir = WG_CONFIG_DIR
        self.users_dir = WG_USERS_DIR
        self.interface = WG_INTERFACE
        self.server_config = WG_SERVER_CONFIG

    def ensure_users_directory(self):
        try:
            if not os.path.exists(self.users_dir):
                os.makedirs(self.users_dir, mode=0o755, exist_ok=True)
                logger.info(f"Created users directory: {self.users_dir}")
            return True
        except Exception as e:
            logger.error(f"Error creating users directory: {e}")
            return False

    def read_server_config(self):
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
        try:
            temp_file = '/tmp/wg0_temp.conf'
            with open(temp_file, 'w') as f:
                f.write(config_content)

            subprocess.run(['sudo', 'mv', temp_file, self.server_config], check=True)
            subprocess.run(['sudo', 'chmod', '600', self.server_config], check=True)
            return True
        except Exception as e:
            logger.error(f"Error writing server config: {e}")
            return False

    def get_server_public_key(self):
        try:
            publickey_path = os.path.join(self.config_dir, 'publickey')
            if os.path.exists(publickey_path):
                with open(publickey_path, 'r') as f:
                    return f.read().strip()

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
        config = self.read_server_config()
        if not config:
            return None

        settings = {'address': None, 'port': None, 'private_key': None, 'peers': []}
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
                key, value = key.strip(), value.strip()

                if current_section == 'Interface':
                    if key == 'Address': settings['address'] = value
                    elif key == 'ListenPort': settings['port'] = value
                    elif key == 'PrivateKey': settings['private_key'] = value
                elif current_section == 'Peer':
                    current_peer[key] = value

        if current_peer:
            settings['peers'].append(current_peer)

        return settings

    def get_next_ip(self):
        settings = self.parse_server_config()
        if not settings or not settings['address']:
            return None

        server_network = settings['address'].split('/')[0]
        network_base = '.'.join(server_network.split('.')[:-1])
        used_ips = set([server_network.split('.')[-1]])

        for peer in settings['peers']:
            if 'AllowedIPs' in peer:
                for ip_range in peer['AllowedIPs'].split(','):
                    ip = ip_range.strip().split('/')[0]
                    if ip.startswith(network_base):
                        used_ips.add(ip.split('.')[-1])

        for i in range(100, 255):
            if str(i) not in used_ips:
                return f"{network_base}.{i}/32"

        for i in range(2, 100):
            if str(i) not in used_ips:
                return f"{network_base}.{i}/32"

        return None

    def generate_keypair(self):
        try:
            private_result = subprocess.run(['wg', 'genkey'], capture_output=True, text=True, check=True)
            private_key = private_result.stdout.strip()
            public_result = subprocess.run(['wg', 'pubkey'], input=private_key, capture_output=True, text=True, check=True)
            public_key = public_result.stdout.strip()
            return private_key, public_key
        except Exception as e:
            logger.error(f"Error generating keypair: {e}")
            return None, None

    def create_user(self, username, description='', private_key=None, public_key=None):
        try:
            self.ensure_users_directory()
            user_dir = os.path.join(self.users_dir, username)
            os.makedirs(user_dir, mode=0o755, exist_ok=True)

            if not private_key or not public_key:
                private_key, public_key = self.generate_keypair()
                if not private_key or not public_key:
                    return None

            client_ip = self.get_next_ip()
            if not client_ip:
                return None

            server_public_key = self.get_server_public_key()
            server_settings = self.parse_server_config()

            # Save keys
            private_key_file = os.path.join(user_dir, 'privatekey')
            public_key_file = os.path.join(user_dir, 'publickey')
            with open(private_key_file, 'w') as f: f.write(private_key)
            with open(public_key_file, 'w') as f: f.write(public_key)
            os.chmod(private_key_file, 0o600)
            os.chmod(public_key_file, 0o644)

            # Create client config
            client_config = f"""[Interface]
PrivateKey = {private_key}
Address = {client_ip}
DNS = 1.1.1.1, 8.8.8.8

[Peer]
PublicKey = {server_public_key}
Endpoint = YOUR_SERVER_IP:{server_settings['port']}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""

            config_file = os.path.join(user_dir, f'{username}.conf')
            with open(config_file, 'w') as f:
                f.write(client_config)
            os.chmod(config_file, 0o644)

            # Add to server config
            server_config = self.read_server_config()
            peer_section = f"""
# {username} - {description} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
[Peer]
PublicKey = {public_key}
AllowedIPs = {client_ip}
PersistentKeepalive = 25
"""
            self.write_server_config(server_config + peer_section)
            self.restart_wireguard()

            return {'username': username, 'ip': client_ip, 'public_key': public_key, 'config_file': config_file}
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return None

    def list_users(self):
        try:
            if not os.path.exists(self.users_dir):
                return []

            users = []
            for username in os.listdir(self.users_dir):
                user_dir = os.path.join(self.users_dir, username)
                if not os.path.isdir(user_dir):
                    continue

                public_key = 'N/A'
                public_key_file = os.path.join(user_dir, 'publickey')
                if os.path.exists(public_key_file):
                    with open(public_key_file, 'r') as f:
                        public_key = f.read().strip()

                config_file = os.path.join(user_dir, f'{username}.conf')
                config_exists = os.path.exists(config_file)

                client_ip = 'N/A'
                server_config = self.read_server_config()
                if server_config and public_key != 'N/A':
                    lines = server_config.split('\n')
                    for i, line in enumerate(lines):
                        if f'PublicKey = {public_key}' in line:
                            for j in range(i+1, min(i+5, len(lines))):
                                if 'AllowedIPs' in lines[j]:
                                    client_ip = lines[j].split('=')[1].strip()
                                    break
                            break

                users.append({'username': username, 'ip': client_ip, 'public_key': public_key, 'config_exists': config_exists})
            return sorted(users, key=lambda x: x['username'])
        except Exception as e:
            logger.error(f"Error listing users: {e}")
            return []

    def delete_user(self, username):
        try:
            user_dir = os.path.join(self.users_dir, username)
            if not os.path.exists(user_dir):
                return False

            public_key = None
            public_key_file = os.path.join(user_dir, 'publickey')
            if os.path.exists(public_key_file):
                with open(public_key_file, 'r') as f:
                    public_key = f.read().strip()

            if public_key:
                server_config = self.read_server_config()
                lines = server_config.split('\n')
                new_config_lines = []
                i = 0

                while i < len(lines):
                    line = lines[i]
                    if public_key in line and 'PublicKey' in line:
                        while new_config_lines and (new_config_lines[-1].strip().startswith('#') or new_config_lines[-1].strip() == '' or new_config_lines[-1].strip() == '[Peer]'):
                            new_config_lines.pop()
                        i += 1
                        while i < len(lines):
                            next_line = lines[i].strip()
                            if next_line.startswith('[') or (next_line.startswith('#') and i + 1 < len(lines) and lines[i + 1].strip().startswith('[')):
                                break
                            i += 1
                        continue
                    new_config_lines.append(line)
                    i += 1

                while new_config_lines and new_config_lines[-1].strip() == '':
                    new_config_lines.pop()

                new_config = '\n'.join(new_config_lines)
                if not new_config.endswith('\n'):
                    new_config += '\n'
                self.write_server_config(new_config)

            subprocess.run(['rm', '-rf', user_dir], check=True)
            self.restart_wireguard()
            return True
        except Exception as e:
            logger.error(f"Error deleting user: {e}")
            return False

    def get_user_config(self, username):
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
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', f'wg-quick@{self.interface}'], check=True)
            return True
        except Exception as e:
            logger.error(f"Error restarting WireGuard: {e}")
            return False

    def parse_wireguard_status(self):
        try:
            result = subprocess.run(['sudo', 'wg', 'show', self.interface], capture_output=True, text=True, check=True)
            return self._format_status_for_ui(result.stdout)
        except Exception as e:
            logger.error(f"Error getting WireGuard status: {e}")
            return None

    def _format_status_for_ui(self, raw_status):
        if not raw_status:
            return None

        lines = raw_status.strip().split('\n')
        status_data = {'interface': {}, 'peers': []}
        current_peer = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith('interface:'):
                status_data['interface']['name'] = line.split(':')[1].strip()
            elif line.startswith('public key:'):
                if current_peer is None:
                    status_data['interface']['public_key'] = line.split(':')[1].strip()
                else:
                    current_peer['public_key'] = line.split(':')[1].strip()
            elif line.startswith('listening port:'):
                status_data['interface']['port'] = line.split(':')[1].strip()
            elif line.startswith('peer:'):
                if current_peer:
                    status_data['peers'].append(current_peer)
                current_peer = {'public_key': line.split(':')[1].strip()}
            elif line.startswith('endpoint:') and current_peer:
                endpoint = line.split(':')[1].strip()
                current_peer['endpoint'] = endpoint
                current_peer['ip'] = endpoint.split(':')[0] if ':' in endpoint else endpoint
            elif line.startswith('allowed ips:') and current_peer:
                current_peer['allowed_ips'] = line.split(':')[1].strip()
                ips = current_peer['allowed_ips'].split(',')
                for ip in ips:
                    ip_clean = ip.strip()
                    if ip_clean.endswith('/32'):
                        current_peer['client_ip'] = ip_clean.replace('/32', '')
                        break
            elif line.startswith('latest handshake:') and current_peer:
                handshake = line.split(':', 1)[1].strip()
                current_peer['latest_handshake'] = handshake
                current_peer['status'] = self._determine_peer_status(handshake)
            elif line.startswith('transfer:') and current_peer:
                transfer = line.split(':', 1)[1].strip()
                received, sent = self._parse_transfer_data(transfer)
                current_peer['transfer_received'] = received
                current_peer['transfer_sent'] = sent
                current_peer['transfer_total'] = self._calculate_total_transfer(received, sent)

        if current_peer:
            status_data['peers'].append(current_peer)

        return status_data

    def _determine_peer_status(self, handshake_text):
        if not handshake_text or 'never' in handshake_text.lower():
            return 'offline'

        time_components = handshake_text.split(',')
        total_seconds = 0

        for component in time_components:
            component = component.strip()
            if 'minute' in component:
                minutes = int(component.split()[0])
                total_seconds += minutes * 60
            elif 'second' in component:
                seconds = int(component.split()[0])
                total_seconds += seconds

        return 'online' if total_seconds <= 180 else 'stale'

    def _parse_transfer_data(self, transfer_text):
        try:
            received_part, sent_part = transfer_text.split(',')
            received = received_part.strip().split()[0] + ' ' + received_part.strip().split()[1]
            sent = sent_part.strip().split()[0] + ' ' + sent_part.strip().split()[1]
            return received, sent
        except:
            return '0 B', '0 B'

    def _calculate_total_transfer(self, received, sent):
        try:
            units = {'B': 1, 'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3}
            rec_value, rec_unit = received.split()
            sent_value, sent_unit = sent.split()

            rec_bytes = float(rec_value) * units.get(rec_unit, 1)
            sent_bytes = float(sent_value) * units.get(sent_unit, 1)
            total_bytes = rec_bytes + sent_bytes

            if total_bytes >= units['GiB']:
                return f"{total_bytes/units['GiB']:.2f} GiB"
            elif total_bytes >= units['MiB']:
                return f"{total_bytes/units['MiB']:.2f} MiB"
            elif total_bytes >= units['KiB']:
                return f"{total_bytes/units['KiB']:.2f} KiB"
            else:
                return f"{total_bytes:.0f} B"
        except:
            return '0 B'

    def get_wireguard_status(self):
        try:
            result = subprocess.run(['sudo', 'wg', 'show', self.interface], capture_output=True, text=True, check=True)
            return result.stdout
        except Exception as e:
            logger.error(f"Error getting WireGuard status: {e}")
            return None

# Initialize manager
wg_manager = WireGuardManager()

# Routes
@wireguard_bp.route('/')
def index():
    return render_template('wireguard/index.html')

@wireguard_bp.route('/api/config')
def get_config():
    config = wg_manager.read_server_config()
    if config:
        return jsonify({'success': True, 'config': config})
    return jsonify({'success': False, 'error': 'Failed to read configuration'}), 500

@wireguard_bp.route('/api/config', methods=['POST'])
def update_config():
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
    users = wg_manager.list_users()
    return jsonify({'success': True, 'users': users})

@wireguard_bp.route('/api/users', methods=['POST'])
def create_user():
    data = request.get_json()
    username = data.get('username')
    description = data.get('description', '')
    private_key = data.get('private_key', '').strip()
    public_key = data.get('public_key', '').strip()

    if not username:
        return jsonify({'success': False, 'error': 'Username is required'}), 400

    if not username.replace('_', '').replace('-', '').isalnum():
        return jsonify({'success': False, 'error': 'Invalid username format'}), 400

    if (private_key and not public_key) or (public_key and not private_key):
        return jsonify({'success': False, 'error': 'Both private and public keys must be provided'}), 400

    result = wg_manager.create_user(username, description, private_key or None, public_key or None)
    if result:
        return jsonify({'success': True, 'user': result})
    return jsonify({'success': False, 'error': 'Failed to create user'}), 500

@wireguard_bp.route('/api/users/<username>', methods=['DELETE'])
def delete_user(username):
    if wg_manager.delete_user(username):
        return jsonify({'success': True, 'message': f'User {username} deleted successfully'})
    return jsonify({'success': False, 'error': 'Failed to delete user'}), 500

@wireguard_bp.route('/api/users/<username>/config')
def get_user_config(username):
    config = wg_manager.get_user_config(username)
    if config:
        return jsonify({'success': True, 'config': config})
    return jsonify({'success': False, 'error': 'Failed to read user configuration'}), 404

@wireguard_bp.route('/api/users/<username>/download')
def download_user_config(username):
    config = wg_manager.get_user_config(username)
    if config:
        return Response(config, mimetype='text/plain', headers={'Content-Disposition': f'attachment; filename={username}.conf'})
    return jsonify({'success': False, 'error': 'Failed to read user configuration'}), 404

@wireguard_bp.route('/api/users/<username>/qr')
def get_user_qr(username):
    config = wg_manager.get_user_config(username)
    if not config:
        return jsonify({'success': False, 'error': 'Failed to read user configuration'}), 404
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(config)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        return send_file(img_buffer, mimetype='image/png')
    except Exception as e:
        logger.error(f"Error generating QR code: {e}")
        return jsonify({'success': False, 'error': 'Failed to generate QR code'}), 500

@wireguard_bp.route('/api/status')
def get_status():
    status = wg_manager.get_wireguard_status()
    if status:
        return jsonify({'success': True, 'status': status})
    return jsonify({'success': False, 'error': 'Failed to get WireGuard status'}), 500

@wireguard_bp.route('/api/status-ui')
def get_status_ui():
    status = wg_manager.parse_wireguard_status()
    if status:
        return jsonify({'success': True, 'status': status})
    return jsonify({'success': False, 'error': 'Failed to get WireGuard status'}), 500

@wireguard_bp.route('/api/restart', methods=['POST'])
def restart_wireguard():
    if wg_manager.restart_wireguard():
        return jsonify({'success': True, 'message': 'WireGuard restarted successfully'})
    return jsonify({'success': False, 'error': 'Failed to restart WireGuard'}), 500

@wireguard_bp.route('/api/init-users-dir', methods=['POST'])
def init_users_directory():
    if wg_manager.ensure_users_directory():
        return jsonify({'success': True, 'message': 'Users directory initialized successfully'})
    return jsonify({'success': False, 'error': 'Failed to initialize users directory'}), 500