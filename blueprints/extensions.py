"""
Extensions Management Blueprint with Status Monitoring
Uses centralized SSH manager for Asterisk commands
Automatically reloads Asterisk after configuration changes
"""

from flask import Blueprint, render_template, jsonify, request
import logging
import threading
import time
from functools import wraps
from blueprints.api_core import api
from ssh_manager import ssh_manager  # Import centralized SSH manager

logger = logging.getLogger(__name__)

extensions_bp = Blueprint('extensions', __name__, template_folder='../templates/extensions')


# ============================================================================
# CACHING LAYER - Prevents hammering Asterisk with SSH requests
# ============================================================================

class StatusCache:
    """Cache for extension status with TTL"""
    def __init__(self, ttl=10):
        self.cache = {}
        self.ttl = ttl  # Time to live in seconds
        self.lock = threading.Lock()

    def get(self, key):
        """Get cached value if not expired"""
        with self.lock:
            if key in self.cache:
                value, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl:
                    logger.debug(f"📦 Cache HIT for {key} (age: {time.time() - timestamp:.1f}s)")
                    return value
                else:
                    logger.debug(f"⏰ Cache EXPIRED for {key}")
                    del self.cache[key]
        return None

    def set(self, key, value):
        """Set cached value with timestamp"""
        with self.lock:
            self.cache[key] = (value, time.time())
            logger.debug(f"💾 Cached {key}")

    def clear(self):
        """Clear all cached values"""
        with self.lock:
            self.cache.clear()
            logger.debug("🗑️ Cache cleared")


# Global cache instance (10 second TTL - prevents excessive SSH calls)
status_cache = StatusCache(ttl=10)


def cache_result(cache_key_func, ttl=10):
    """Decorator to cache function results"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = cache_key_func(*args, **kwargs) if callable(cache_key_func) else cache_key_func

            # Try to get from cache
            cached = status_cache.get(cache_key)
            if cached is not None:
                return cached

            # Call function and cache result
            result = func(*args, **kwargs)
            status_cache.set(cache_key, result)
            return result
        return wrapper
    return decorator


# ============================================================================
# ASTERISK RELOAD FUNCTION
# ============================================================================

def reload_asterisk():
    """
    Reload Asterisk configuration after changes
    This applies the changes made via GraphQL mutations
    Equivalent to clicking the "Apply Config" button in FreePBX GUI
    """
    try:
        logger.info("🔄 Reloading Asterisk configuration...")

        # CORRECT: doreload requires an empty input object {}
        # Note: transaction_id can be used to track async operations
        mutation = '''
        mutation {
            doreload(input: {}) {
                status
                message
                transaction_id
            }
        }
        '''

        result = api.graphql_query(mutation)
        reload_result = result.get('doreload', {})

        if reload_result.get('status'):
            transaction_id = reload_result.get('transaction_id', 'N/A')
            logger.info(f"✅ Asterisk reload initiated successfully (transaction: {transaction_id})")
            logger.info(f"   Message: {reload_result.get('message', '')}")
            return True
        else:
            logger.error(f"❌ Asterisk reload failed: {reload_result.get('message', '')}")
            return False

    except Exception as e:
        logger.error(f"❌ Error reloading Asterisk: {str(e)}")
        return False


# ============================================================================
# EXTENSION STATUS FUNCTIONS - Now using centralized SSH manager
# ============================================================================

@cache_result(cache_key_func=lambda: 'extension_status', ttl=10)
def get_extension_status():
    """
    Get extension registration status from Asterisk using pjsip show aors
    Returns dict with extension_id as key and status info
    CACHED for 10 seconds to prevent excessive SSH calls

    Status values from Asterisk:
    - Avail: Contact is available (registered and qualified)
    - Unavail: Contact unavailable (registered but qualify failed)
    - NonQual: Contact exists but qualify is disabled
    - Unknown: Contact status unknown
    """
    try:
        # Use centralized SSH manager
        output = ssh_manager.get_pjsip_aors(timeout=10)

        if not output:
            logger.error("Failed to get extension status via SSH")
            return {}

        status_dict = {}
        current_aor = None

        # Parse the output
        lines = output.split('\n')

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

            # Look for Contact lines
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
                    if contact_status == 'Avail':
                        status_dict[current_aor]['registered'] = True
                        status_dict[current_aor]['status'] = 'Online'
                    elif contact_status == 'NonQual':
                        status_dict[current_aor]['registered'] = True
                        status_dict[current_aor]['status'] = 'Online (Not Qualified)'
                    elif contact_status == 'Unavail':
                        status_dict[current_aor]['registered'] = True
                        status_dict[current_aor]['status'] = 'Unavailable'

        logger.debug(f"Retrieved status for {len(status_dict)} extensions")
        return status_dict

    except Exception as e:
        logger.error(f"Error getting extension status via SSH: {str(e)}")
        return {}


# ============================================================================
# FLASK ROUTES
# ============================================================================

@extensions_bp.route('/')
def index():
    """Extensions management page"""
    return render_template('extensions_index.html')


@extensions_bp.route('/api/status')
def get_status():
    """Get registration status for all extensions (CACHED)"""
    try:
        status_dict = get_extension_status()

        online_count = sum(1 for s in status_dict.values() if s['registered'])
        offline_count = len(status_dict) - online_count

        return jsonify({
            'status': 'success',
            'online_count': online_count,
            'offline_count': offline_count,
            'extensions': status_dict,
            'cached': True  # Indicate this may be cached data
        })
    except Exception as e:
        logger.error(f"Error in status endpoint: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@extensions_bp.route('/api/list')
def list_extensions():
    """Get all extensions with registration status (CACHED)"""
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

        # Get registration status via SSH (CACHED - won't hammer server)
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
    """Get single extension details with status (CACHED)"""
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

        # Get status via SSH (CACHED)
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


@extensions_bp.route('/api/refresh-status', methods=['POST'])
def refresh_status():
    """Force refresh extension status (clears cache)"""
    try:
        status_cache.clear()
        logger.info("🔄 Extension status cache cleared")
        return jsonify({
            'status': 'success',
            'message': 'Status cache cleared, next request will fetch fresh data'
        })
    except Exception as e:
        logger.error(f"Error refreshing status: {str(e)}")
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
        max_contacts = data.get('maxContacts', 1)

        # maxContacts must be passed as a STRING even though it's an integer type
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
                    maxContacts: "{max_contacts}"
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
            # Clear caches
            api.clear_cache('extensions')
            status_cache.clear()

            # RELOAD ASTERISK after creating extension
            reload_success = reload_asterisk()

            return jsonify({
                'status': 'success',
                'message': add_result.get('message', 'Extension created successfully'),
                'extension_id': extension_id,
                'reloaded': reload_success
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
        max_contacts = data.get('maxContacts', 1)

        # maxContacts must be passed as a STRING even though it's an integer type
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
                    maxContacts: "{max_contacts}"
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
            # Clear caches
            api.clear_cache('extensions')
            status_cache.clear()

            # RELOAD ASTERISK after updating extension
            reload_success = reload_asterisk()

            return jsonify({
                'status': 'success',
                'message': update_result.get('message', 'Extension updated successfully'),
                'reloaded': reload_success
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
            # Clear caches
            api.clear_cache('extensions')
            status_cache.clear()

            # RELOAD ASTERISK after deleting extension
            reload_success = reload_asterisk()

            return jsonify({
                'status': 'success',
                'message': delete_result.get('message', 'Extension deleted successfully'),
                'reloaded': reload_success
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

        # startExtension and numberOfExtensions should be passed as integers (not strings)
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
            # Clear caches
            api.clear_cache('extensions')
            status_cache.clear()

            # RELOAD ASTERISK after bulk creating extensions
            reload_success = reload_asterisk()

            return jsonify({
                'status': 'success',
                'message': create_result.get('message', 'Extensions created successfully'),
                'reloaded': reload_success
            })
        else:
            return jsonify({
                'status': 'error',
                'message': create_result.get('message', 'Failed to create extensions')
            }), 500

    except Exception as e:
        logger.error(f"Error bulk creating extensions: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@extensions_bp.route('/api/reload', methods=['POST'])
def manual_reload():
    """Manual Asterisk reload endpoint"""
    try:
        reload_success = reload_asterisk()

        if reload_success:
            # Clear status cache after reload
            status_cache.clear()

            return jsonify({
                'status': 'success',
                'message': 'Asterisk configuration reloaded successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to reload Asterisk configuration'
            }), 500

    except Exception as e:
        logger.error(f"Error in manual reload: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500