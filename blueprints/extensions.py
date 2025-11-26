"""
Extensions Management Blueprint with Status Monitoring via SSH
Handles CRUD operations for PBX extensions + registration status
"""

from flask import Blueprint, render_template, jsonify, request, current_app
import logging
import subprocess
import re
from blueprints.api_core import api

logger = logging.getLogger(__name__)

extensions_bp = Blueprint('extensions', __name__, template_folder='../templates/extensions')


@extensions_bp.route('/')
def index():
    """Extensions management page"""
    return render_template('extensions_index.html')


def get_extension_status():
    """
    Get extension registration status from Asterisk using pjsip show aors via SSH
    Returns dict with extension_id as key and status info

    Status values from Asterisk:
    - Avail: Contact is available (registered and qualified)
    - Unavail: Contact unavailable (registered but qualify failed)
    - NonQual: Contact exists but qualify is disabled
    - Unknown: Contact status unknown
    """
    try:
        # Get FreePBX host from config
        freepbx_host = current_app.config.get('FREEPBX_HOST', 'http://10.200.200.2:80')

        # Extract hostname/IP from URL
        freepbx_ip = freepbx_host.replace('http://', '').replace('https://', '').split(':')[0]

        # SSH credentials from config
        ssh_user = current_app.config.get('FREEPBX_SSH_USER', 'root')
        ssh_key = current_app.config.get('FREEPBX_SSH_KEY', None)
        ssh_password = current_app.config.get('FREEPBX_SSH_PASSWORD', None)

        # Build SSH command
        if ssh_key:
            # Use SSH key authentication (preferred)
            cmd = f'ssh -i {ssh_key} -o StrictHostKeyChecking=no -o ConnectTimeout=5 {ssh_user}@{freepbx_ip} "asterisk -rx \'pjsip show aors\'"'
        elif ssh_password:
            # Use sshpass for password authentication
            cmd = f'sshpass -p "{ssh_password}" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 {ssh_user}@{freepbx_ip} "asterisk -rx \'pjsip show aors\'"'
        else:
            logger.error("No SSH authentication method configured (FREEPBX_SSH_KEY or FREEPBX_SSH_PASSWORD)")
            return {}

        logger.debug(f"Executing SSH command to {freepbx_ip}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            logger.error(f"Failed to get extension status via SSH: {result.stderr}")
            return {}

        status_dict = {}
        current_aor = None

        # Parse the output
        # Format: Aor: <name> <max_contacts>
        #         Contact: <aor>/<uri> <hash> <Status> <RTT>
        lines = result.stdout.split('\n')

        for line in lines:
            line = line.strip()

            # Skip header and separator lines
            if not line or line.startswith('===') or (line.startswith('Aor:') and 'MaxContact' in line):
                continue

            # Look for AOR lines (e.g., "Aor: 1000 1")
            if line.startswith('Aor:'):
                parts = line.split()
                if len(parts) >= 2:
                    current_aor = parts[1]
                    status_dict[current_aor] = {
                        'registered': False,
                        'status': 'Offline',
                        'contacts': [],
                        'contact_status': []
                    }
                continue

            # Look for Contact lines (e.g., "Contact: 1000/sip:1000@192.168.1.100:5060 abcd1234 Avail 10.5")
            if current_aor and line.startswith('Contact:'):
                parts = line.split()
                if len(parts) >= 3:
                    contact_uri = parts[1]
                    contact_hash = parts[2] if len(parts) > 2 else ''
                    contact_status = parts[3] if len(parts) > 3 else 'Unknown'
                    rtt = parts[4] if len(parts) > 4 else 'nan'

                    status_dict[current_aor]['contacts'].append(contact_uri)
                    status_dict[current_aor]['contact_status'].append(contact_status)

                    # Determine overall status
                    # Avail = registered and qualified
                    # Unavail = registered but not responding
                    # NonQual = registered but qualify disabled
                    # Unknown = status unknown
                    if contact_status == 'Avail':
                        status_dict[current_aor]['registered'] = True
                        status_dict[current_aor]['status'] = 'Online'
                    elif contact_status == 'NonQual':
                        status_dict[current_aor]['registered'] = True
                        status_dict[current_aor]['status'] = 'Online (Not Qualified)'
                    elif contact_status == 'Unavail':
                        status_dict[current_aor]['registered'] = True
                        status_dict[current_aor]['status'] = 'Unavailable'
                    # If Unknown or empty, leave as Offline

        logger.debug(f"Retrieved status for {len(status_dict)} extensions")
        return status_dict

    except subprocess.TimeoutExpired:
        logger.error("Timeout while getting extension status via SSH")
        return {}
    except FileNotFoundError as e:
        if 'sshpass' in str(e):
            logger.error("sshpass not found. Please install: apt-get install sshpass (Ubuntu) or yum install sshpass (CentOS)")
        else:
            logger.error(f"SSH command not found: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Error getting extension status via SSH: {str(e)}")
        return {}


@extensions_bp.route('/api/status')
def get_status():
    """Get registration status for all extensions"""
    try:
        status_dict = get_extension_status()

        online_count = sum(1 for s in status_dict.values() if s['registered'])
        offline_count = len(status_dict) - online_count

        return jsonify({
            'status': 'success',
            'online_count': online_count,
            'offline_count': offline_count,
            'extensions': status_dict
        })
    except Exception as e:
        logger.error(f"Error in status endpoint: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@extensions_bp.route('/api/list')
def list_extensions():
    """Get all extensions with registration status"""
    try:
        # Get extensions from GraphQL
        query = '''
        {
            fetchAllExtensions {
                status
                message
                totalCount
                extension {
                    id
                    extensionId
                    user {
                        name
                        outboundCid
                        ringtimer
                        noanswer
                        sipname
                        password
                    }
                    coreDevice {
                        deviceId
                        dial
                        devicetype
                        description
                        emergencyCid
                        tech
                    }
                }
            }
        }
        '''

        data = api.graphql_query(query)
        result = data.get('fetchAllExtensions', {})

        if not result.get('status'):
            return jsonify({
                'status': 'error',
                'message': result.get('message', 'Failed to fetch extensions')
            }), 500

        # Get registration status via SSH
        status_dict = get_extension_status()

        extensions = []
        for ext in result.get('extension', []):
            user = ext.get('user', {})
            device = ext.get('coreDevice', {})
            ext_id = ext.get('extensionId')

            # Get status info
            ext_status = status_dict.get(ext_id, {
                'registered': False,
                'status': 'Unknown',
                'contacts': []
            })

            extensions.append({
                'id': ext.get('id'),
                'extension_id': ext_id,
                'name': user.get('name', '-'),
                'tech': device.get('tech', 'pjsip'),
                'caller_id': device.get('description', '-'),
                'outbound_cid': user.get('outboundCid', '-'),
                'emergency_cid': device.get('emergencyCid', '-'),
                'dial': device.get('dial', '-'),
                'description': device.get('description', '-'),
                'device_type': device.get('devicetype', '-'),
                # Status information
                'registered': ext_status['registered'],
                'status': ext_status['status'],
                'contact_count': len(ext_status['contacts'])
            })

        return jsonify({
            'status': 'success',
            'total': result.get('totalCount', 0),
            'extensions': extensions
        })

    except Exception as e:
        logger.error(f"Error fetching extensions: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@extensions_bp.route('/api/get/<extension_id>')
def get_extension(extension_id):
    """Get single extension details with status"""
    try:
        query = f'''
        {{
            fetchExtension(extensionId: "{extension_id}") {{
                status
                message
                id
                extensionId
                user {{
                    name
                    outboundCid
                    voicemail
                    ringtimer
                    noanswer
                    noanswerDestination
                    noanswerCid
                    busyCid
                    sipname
                    password
                    extPassword
                }}
                coreDevice {{
                    deviceId
                    dial
                    devicetype
                    description
                    emergencyCid
                    tech
                }}
            }}
        }}
        '''

        data = api.graphql_query(query)
        result = data.get('fetchExtension', {})

        if not result.get('status'):
            return jsonify({
                'status': 'error',
                'message': result.get('message', 'Extension not found')
            }), 404

        user = result.get('user', {})
        device = result.get('coreDevice', {})

        # Get status via SSH
        status_dict = get_extension_status()
        ext_status = status_dict.get(extension_id, {
            'registered': False,
            'status': 'Unknown',
            'contacts': []
        })

        extension = {
            'id': result.get('id'),
            'extension_id': result.get('extensionId'),
            'name': user.get('name'),
            'tech': device.get('tech'),
            'caller_id': device.get('description'),
            'outbound_cid': user.get('outboundCid'),
            'emergency_cid': device.get('emergencyCid'),
            'voicemail': user.get('voicemail'),
            'ringtimer': user.get('ringtimer'),
            'noanswer': user.get('noanswer'),
            'description': device.get('description'),
            'dial': device.get('dial'),
            'device_type': device.get('devicetype'),
            'email': user.get('email', ''),
            'max_contacts': 1,
            # Status
            'registered': ext_status['registered'],
            'status': ext_status['status'],
            'contacts': ext_status['contacts']
        }

        return jsonify({
            'status': 'success',
            'extension': extension
        })

    except Exception as e:
        logger.error(f"Error fetching extension {extension_id}: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@extensions_bp.route('/api/create', methods=['POST'])
def create_extension():
    """Create new extension"""
    try:
        data = request.get_json()

        # Required fields
        extension_id = data.get('extensionId')
        name = data.get('name')

        if not extension_id or not name:
            return jsonify({
                'status': 'error',
                'message': 'Extension ID and Name are required'
            }), 400

        # Build mutation with optional fields
        tech = data.get('tech', 'pjsip')
        email = data.get('email', '')
        outbound_cid = data.get('outboundCid', '')
        emergency_cid = data.get('emergencyCid', '')
        caller_id = data.get('callerId', '')
        vm_enable = data.get('vmEnable', True)
        vm_password = data.get('vmPassword', '')
        um_enable = data.get('umEnable', True)
        um_password = data.get('umPassword', '')
        um_groups = data.get('umGroups', '1')
        max_contacts = int(data.get('maxContacts', 1))

        mutation = f'''
        mutation {{
            addExtension(
                input: {{
                    extensionId: "{extension_id}"
                    name: "{name}"
                    tech: "{tech}"
                    email: "{email}"
                    outboundCid: "{outbound_cid}"
                    emergencyCid: "{emergency_cid}"
                    callerID: "{caller_id}"
                    vmEnable: {str(vm_enable).lower()}
                    vmPassword: "{vm_password}"
                    umEnable: {str(um_enable).lower()}
                    umPassword: "{um_password}"
                    umGroups: "{um_groups}"
                    maxContacts: {max_contacts}
                    clientMutationId: "create_{extension_id}"
                }}
            ) {{
                status
                message
            }}
        }}
        '''

        result = api.graphql_query(mutation)
        add_result = result.get('addExtension', {})

        if add_result.get('status'):
            # Clear extensions cache
            api.clear_cache('extensions')

            return jsonify({
                'status': 'success',
                'message': add_result.get('message', 'Extension created successfully'),
                'extension_id': extension_id
            })
        else:
            return jsonify({
                'status': 'error',
                'message': add_result.get('message', 'Failed to create extension')
            }), 500

    except Exception as e:
        logger.error(f"Error creating extension: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@extensions_bp.route('/api/update/<extension_id>', methods=['PUT'])
def update_extension(extension_id):
    """Update existing extension"""
    try:
        data = request.get_json()

        # Name is required for update
        name = data.get('name')
        if not name:
            return jsonify({
                'status': 'error',
                'message': 'Name is required'
            }), 400

        # Build mutation with provided fields
        tech = data.get('tech', 'pjsip')
        email = data.get('email', '')
        outbound_cid = data.get('outboundCid', '')
        emergency_cid = data.get('emergencyCid', '')
        caller_id = data.get('callerId', '')
        vm_enable = data.get('vmEnable', True)
        vm_password = data.get('vmPassword', '')
        um_enable = data.get('umEnable', True)
        um_password = data.get('umPassword', '')
        um_groups = data.get('umGroups', '1')
        ext_password = data.get('extPassword', '')
        max_contacts = int(data.get('maxContacts', 1))

        mutation = f'''
        mutation {{
            updateExtension(
                input: {{
                    extensionId: "{extension_id}"
                    name: "{name}"
                    tech: "{tech}"
                    email: "{email}"
                    outboundCid: "{outbound_cid}"
                    emergencyCid: "{emergency_cid}"
                    callerID: "{caller_id}"
                    vmEnable: {str(vm_enable).lower()}
                    vmPassword: "{vm_password}"
                    umEnable: {str(um_enable).lower()}
                    umPassword: "{um_password}"
                    umGroups: "{um_groups}"
                    extPassword: "{ext_password}"
                    maxContacts: {max_contacts}
                    clientMutationId: "update_{extension_id}"
                }}
            ) {{
                status
                message
                clientMutationId
            }}
        }}
        '''

        result = api.graphql_query(mutation)
        update_result = result.get('updateExtension', {})

        if update_result.get('status'):
            # Clear extensions cache
            api.clear_cache('extensions')

            return jsonify({
                'status': 'success',
                'message': update_result.get('message', 'Extension updated successfully')
            })
        else:
            return jsonify({
                'status': 'error',
                'message': update_result.get('message', 'Failed to update extension')
            }), 500

    except Exception as e:
        logger.error(f"Error updating extension {extension_id}: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@extensions_bp.route('/api/delete/<extension_id>', methods=['DELETE'])
def delete_extension(extension_id):
    """Delete extension"""
    try:
        mutation = f'''
        mutation {{
            deleteExtension(
                input: {{ extensionId: "{extension_id}" }}
            ) {{
                status
                message
            }}
        }}
        '''

        result = api.graphql_query(mutation)
        delete_result = result.get('deleteExtension', {})

        if delete_result.get('status'):
            # Clear extensions cache
            api.clear_cache('extensions')

            return jsonify({
                'status': 'success',
                'message': delete_result.get('message', 'Extension deleted successfully')
            })
        else:
            return jsonify({
                'status': 'error',
                'message': delete_result.get('message', 'Failed to delete extension')
            }), 500

    except Exception as e:
        logger.error(f"Error deleting extension {extension_id}: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@extensions_bp.route('/api/bulk-create', methods=['POST'])
def bulk_create_extensions():
    """Create range of extensions"""
    try:
        data = request.get_json()

        start_ext = data.get('startExtension')
        num_extensions = data.get('numberOfExtensions')

        if not start_ext or not num_extensions:
            return jsonify({
                'status': 'error',
                'message': 'Start extension and number of extensions are required'
            }), 400

        name = data.get('name', '')
        tech = data.get('tech', 'pjsip')
        email = data.get('email', '')
        outbound_cid = data.get('outboundCid', '')
        emergency_cid = data.get('emergencyCid', '')
        vm_enable = data.get('vmEnable', True)
        vm_password = data.get('vmPassword', '')
        um_enable = data.get('umEnable', True)
        um_groups = data.get('umGroups', '1')

        mutation = f'''
        mutation {{
            createRangeofExtension(
                input: {{
                    startExtension: {start_ext}
                    numberOfExtensions: {num_extensions}
                    name: "{name}"
                    tech: "{tech}"
                    email: "{email}"
                    outboundCid: "{outbound_cid}"
                    emergencyCid: "{emergency_cid}"
                    vmEnable: {str(vm_enable).lower()}
                    vmPassword: "{vm_password}"
                    umEnable: {str(um_enable).lower()}
                    umGroups: "{um_groups}"
                    clientMutationId: "bulk_create"
                }}
            ) {{
                status
                message
            }}
        }}
        '''

        result = api.graphql_query(mutation)
        create_result = result.get('createRangeofExtension', {})

        if create_result.get('status'):
            # Clear extensions cache
            api.clear_cache('extensions')

            return jsonify({
                'status': 'success',
                'message': create_result.get('message', 'Extensions created successfully')
            })
        else:
            return jsonify({
                'status': 'error',
                'message': create_result.get('message', 'Failed to create extensions')
            }), 500

    except Exception as e:
        logger.error(f"Error bulk creating extensions: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500