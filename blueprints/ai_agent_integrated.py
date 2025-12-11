"""
Integrated AI Agent Blueprint - FIXED FLICKERING ISSUE
Fixed status flickering by using proper threading locks and status states
"""

from flask import Blueprint, render_template, jsonify, request, session, current_app
import logging
import asyncio
import threading
import json
import os
import traceback
from datetime import datetime
from audit_utils import log_action
from models import db, AIAgentConfig, AIAgentDepartment, AIAgentCallLog, AIAgentTurn

# =============================================================================
# CLEAR FOCUSED LOGGING SETUP
# =============================================================================

# Suppress debug noise from other modules
logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('ssh_manager').setLevel(logging.WARNING)
logging.getLogger('blueprints').setLevel(logging.WARNING)
logging.getLogger('gtts.tts').setLevel(logging.WARNING)
logging.getLogger('aioswagger11.client').setLevel(logging.WARNING)
logging.getLogger('aioari.client').setLevel(logging.WARNING)
logging.getLogger('aioari.model').setLevel(logging.WARNING)
logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)

# Create AI Agent specific logger with CLEAR formatting
ai_logger = logging.getLogger('AI_AGENT')
ai_logger.setLevel(logging.INFO)
ai_logger.handlers = []  # Clear any existing handlers

# Console handler with clean format
console = logging.StreamHandler()
console.setLevel(logging.INFO)

# CLEAR, EASY-TO-READ FORMAT
formatter = logging.Formatter(
    '\n%(asctime)s 🤖 [AI-AGENT] %(message)s',
    datefmt='%H:%M:%S'
)
console.setFormatter(formatter)
ai_logger.addHandler(console)
ai_logger.propagate = False

# Use ai_logger for all AI agent logs
logger = ai_logger

ai_agent_integrated_bp = Blueprint('ai_agent_integrated', __name__)


# ============================================================================
# CONFIGURATION CONTAINER - Replaces ORM object for thread safety
# ============================================================================

class AIAgentConfigContainer:
    """Thread-safe configuration container - NOT a SQLAlchemy object"""

    def __init__(self, config_dict):
        """Initialize from dictionary of config values"""
        # User-configurable settings
        self.id = config_dict.get('id')
        self.name = config_dict.get('name')
        self.enabled = config_dict.get('enabled', True)
        self.system_prompt = config_dict.get('system_prompt')
        self.greeting_template = config_dict.get('greeting_template')
        self.company_name = config_dict.get('company_name')
        self.max_turns = config_dict.get('max_turns', 6)
        self.recording_duration = config_dict.get('recording_duration', 8)
        self.silence_duration = config_dict.get('silence_duration', 2.0)
        self.beep_delay = config_dict.get('beep_delay', 0.1)
        self.beep_pause = config_dict.get('beep_pause', 0.15)
        self.store_recordings = config_dict.get('store_recordings', True)
        self.store_transcripts = config_dict.get('store_transcripts', True)

        # Azure credentials from environment
        self.azure_endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
        self.azure_api_key = os.environ.get('AZURE_OPENAI_API_KEY')
        self.azure_deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o-mini')
        self.azure_speech_key = os.environ.get('AZURE_SPEECH_KEY')
        self.azure_speech_region = os.environ.get('AZURE_SPEECH_REGION', 'eastus')

    @classmethod
    def from_db_config(cls, db_config):
        """Create container from SQLAlchemy ORM object"""
        return cls({
            'id': db_config.id,
            'name': db_config.name,
            'enabled': db_config.enabled,
            'system_prompt': db_config.system_prompt,
            'greeting_template': db_config.greeting_template,
            'company_name': db_config.company_name,
            'max_turns': db_config.max_turns,
            'recording_duration': db_config.recording_duration,
            'silence_duration': db_config.silence_duration,
            'beep_delay': db_config.beep_delay,
            'beep_pause': db_config.beep_pause,
            'store_recordings': db_config.store_recordings,
            'store_transcripts': db_config.store_transcripts
        })


# ============================================================================
# AI AGENT SERVICE MANAGER - FIXED FLICKERING WITH PROPER LOCKS
# ============================================================================

class AIAgentService:
    """Manages the AI agent service lifecycle with comprehensive monitoring"""

    def __init__(self):
        self.running = False
        self.thread = None
        self.loop = None
        self.active_calls = {}
        self.app = None
        self.config_container = None

        # FIXED: Add thread lock for status updates
        self._status_lock = threading.Lock()

        # FIXED: Add explicit status states
        self._service_state = "stopped"  # stopped, starting, running, stopping

        # System stats - protected by lock
        self.stats = {
            'azure_openai_connected': False,
            'azure_speech_connected': False,
            'ari_connected': False,
            'ssh_connected': False,
            'dataverse_connected': False,
            'cache_loaded': False,
            'cache_phrases_count': 0,
            'last_error': None,
            'start_time': None,
            'total_calls_handled': 0,
            'config_loaded': False
        }

    def start(self, config):
        """Start AI agent service with given config"""
        with self._status_lock:
            if self.running or self._service_state in ["starting", "running"]:
                return False, "Service already running or starting"

            try:
                from flask import current_app
                self.app = current_app._get_current_object()

                # Get credentials from environment
                azure_endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
                azure_api_key = os.environ.get('AZURE_OPENAI_API_KEY')
                azure_deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o-mini')
                azure_speech_key = os.environ.get('AZURE_SPEECH_KEY')
                azure_speech_region = os.environ.get('AZURE_SPEECH_REGION', 'eastus')

                # Validate credentials
                if not all([azure_endpoint, azure_api_key, azure_speech_key]):
                    error = "Missing Azure credentials in environment"
                    self.stats['last_error'] = error
                    logger.error(f"❌ {error}")
                    return False, error

                # Check for required modules
                try:
                    import openai
                    import aioari
                    import azure.cognitiveservices.speech
                except ImportError as e:
                    error = f"Missing required Python module: {str(e)}"
                    self.stats['last_error'] = error
                    logger.error(f"❌ {error}")
                    logger.error(
                        "Install with: pip install openai aioari azure-cognitiveservices-speech msal pydub gtts")
                    return False, error

                # FIXED: Create thread-safe config container from ORM object
                self.config_container = AIAgentConfigContainer.from_db_config(config)
                self.stats['config_loaded'] = True

                # FIXED: Set state to starting BEFORE launching thread
                self._service_state = "starting"

                # Start service in background thread
                self.thread = threading.Thread(target=self._run_service, daemon=True)
                self.thread.start()

                # FIXED: Set running flag AFTER thread starts
                self.running = True
                self.stats['start_time'] = datetime.utcnow()

                logger.info("🚀 AI AGENT SERVICE STARTING...")
                return True, "Service started successfully"

            except Exception as e:
                logger.error(f"❌ FAILED TO START: {e}")
                logger.error(traceback.format_exc())
                self.stats['last_error'] = str(e)
                self.running = False
                self._service_state = "stopped"
                return False, str(e)

    def stop(self):
        """Stop AI agent service"""
        with self._status_lock:
            if not self.running or self._service_state == "stopped":
                return False, "Service not running"

            try:
                logger.info("🛑 STOPPING AI AGENT SERVICE...")

                # FIXED: Set state to stopping
                self._service_state = "stopping"
                self.running = False

                # Stop all active calls
                if self.loop and not self.loop.is_closed():
                    for call_id in list(self.active_calls.keys()):
                        try:
                            asyncio.run_coroutine_threadsafe(
                                self._hangup_call(call_id),
                                self.loop
                            )
                        except:
                            pass

                # Wait for thread to finish
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=5)

                # Reset stats
                self.stats = {
                    'azure_openai_connected': False,
                    'azure_speech_connected': False,
                    'ari_connected': False,
                    'ssh_connected': False,
                    'dataverse_connected': False,
                    'cache_loaded': False,
                    'cache_phrases_count': 0,
                    'last_error': None,
                    'start_time': None,
                    'total_calls_handled': self.stats.get('total_calls_handled', 0),
                    'config_loaded': False
                }

                self.config_container = None

                # FIXED: Set state to stopped at the very end
                self._service_state = "stopped"

                logger.info("✅ SERVICE STOPPED")
                return True, "Service stopped successfully"

            except Exception as e:
                logger.error(f"❌ FAILED TO STOP: {e}")
                logger.error(traceback.format_exc())
                self.stats['last_error'] = str(e)
                self._service_state = "stopped"
                return False, str(e)

    def _run_service(self):
        """Run the async service loop"""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._service_main())
        except Exception as e:
            logger.error(f"❌ SERVICE ERROR: {e}")
            logger.error(traceback.format_exc())
            with self._status_lock:
                self.stats['last_error'] = str(e)
                self.running = False
                self._service_state = "stopped"
        finally:
            try:
                self.loop.close()
            except:
                pass
            with self._status_lock:
                self.running = False
                self._service_state = "stopped"

    async def _service_main(self):
        """Main service loop - connects to ARI and handles calls"""
        import aioari
        from openai import AsyncAzureOpenAI
        import azure.cognitiveservices.speech as speechsdk
        from blueprints.ai_agent_handler import SoundCache, get_dataverse_token

        # Get ARI connection details
        ari_url = os.environ.get('ARI_URL', 'http://10.200.200.2:8088')
        ari_user = os.environ.get('ARI_USERNAME', 'asterisk')
        ari_pass = os.environ.get('ARI_PASSWORD')
        ari_app = os.environ.get('ARI_APPLICATION', 'ai-agent')

        if not ari_pass:
            logger.error("❌ ARI_PASSWORD not configured in environment")
            with self._status_lock:
                self.stats['last_error'] = "ARI_PASSWORD not configured"
                self.running = False
                self._service_state = "stopped"
            return

        logger.info("=" * 80)
        logger.info("🔧 INITIALIZING AI AGENT COMPONENTS")
        logger.info(f"   ARI URL: {ari_url}")
        logger.info(f"   ARI Application: {ari_app}")
        logger.info("=" * 80)

        try:
            # Initialize Azure OpenAI
            logger.info("🧠 Testing Azure OpenAI connection...")
            ai_client = AsyncAzureOpenAI(
                api_key=self.config_container.azure_api_key,
                api_version="2024-08-01-preview",
                azure_endpoint=self.config_container.azure_endpoint
            )

            try:
                await ai_client.chat.completions.create(
                    model=self.config_container.azure_deployment,
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=5
                )
                with self._status_lock:
                    self.stats['azure_openai_connected'] = True
                logger.info("   ✅ Azure OpenAI: CONNECTED")
            except Exception as e:
                logger.error(f"   ❌ Azure OpenAI: FAILED - {e}")
                with self._status_lock:
                    self.stats['last_error'] = f"Azure OpenAI failed: {str(e)}"
                    self.running = False
                    self._service_state = "stopped"
                return

            # Initialize Azure Speech
            logger.info("🎤 Configuring Azure Speech...")
            speech_config = speechsdk.SpeechConfig(
                subscription=self.config_container.azure_speech_key,
                region=self.config_container.azure_speech_region
            )
            speech_config.speech_recognition_language = "en-US"
            with self._status_lock:
                self.stats['azure_speech_connected'] = True
            logger.info("   ✅ Azure Speech: CONFIGURED")

            # Initialize SSH
            logger.info("🔌 Testing SSH connection...")
            try:
                with self.app.app_context():
                    from ssh_manager import ssh_manager
                    if ssh_manager.test_connection():
                        with self._status_lock:
                            self.stats['ssh_connected'] = True
                        logger.info("   ✅ SSH: CONNECTED")
                    else:
                        logger.warning("   ⚠️  SSH: FAILED (non-critical)")
            except Exception as e:
                logger.warning(f"   ⚠️  SSH: ERROR - {e} (non-critical)")

            # Test Dataverse (optional)
            dataverse_url = os.environ.get('DATAVERSE_URL')
            if dataverse_url:
                logger.info("📊 Testing Dataverse connection...")
                try:
                    token = await get_dataverse_token()
                    if token:
                        with self._status_lock:
                            self.stats['dataverse_connected'] = True
                        logger.info("   ✅ Dataverse: CONNECTED")
                    else:
                        logger.warning("   ⚠️  Dataverse: AUTH FAILED (optional)")
                except Exception as e:
                    logger.warning(f"   ⚠️  Dataverse: ERROR - {e} (optional)")
            else:
                logger.info("   ℹ️  Dataverse: NOT CONFIGURED (optional)")

            # Pre-cache TTS phrases
            logger.info("📊 Pre-caching common TTS phrases...")
            cache = SoundCache()

            common_phrases = [
                "Good morning, thank you for calling Sibasi Limited. How can I help you today?",
                "Good afternoon, thank you for calling Sibasi Limited. How can I help you today?",
                "Good evening, thank you for calling Sibasi Limited. How can I help you today?",
                "Anytime! Bye!",
                "Thanks for calling!",
                "Could you repeat that?",
                "Didn't catch that. Go ahead.",
                "Having trouble hearing you. Call back if needed.",
                "Let me have someone call you back."
            ]

            cached_count = 0
            for phrase in common_phrases:
                try:
                    await cache.get(phrase)
                    cached_count += 1
                except Exception as e:
                    logger.warning(f"   ⚠️  Cache warning: {e}")

            with self._status_lock:
                self.stats['cache_loaded'] = True
                self.stats['cache_phrases_count'] = cached_count
            logger.info(f"   ✅ Cached {cached_count}/{len(common_phrases)} phrases")

            # Connect to ARI
            logger.info("📞 Connecting to Asterisk ARI...")
            ari_client = await aioari.connect(ari_url, ari_user, ari_pass)
            with self._status_lock:
                self.stats['ari_connected'] = True
            logger.info("   ✅ ARI: CONNECTED")

            # Register event handler
            async def on_stasis_start(event):
                channel_id = event.get('channel', {}).get('id')
                caller_number = event.get('channel', {}).get('caller', {}).get('number', 'Unknown')

                logger.info("=" * 80)
                logger.info(f"📞 INCOMING CALL")
                logger.info(f"   Channel: {channel_id}")
                logger.info(f"   Caller: {caller_number}")
                logger.info("=" * 80)

                if channel_id:
                    with self._status_lock:
                        self.stats['total_calls_handled'] += 1
                    await self._handle_call(channel_id, event, ari_client)

            ari_client.on_event('StasisStart', on_stasis_start)

            # FIXED: Mark service as fully running AFTER all initialization
            with self._status_lock:
                self._service_state = "running"

            logger.info("=" * 80)
            logger.info("🎙️  AI AGENT READY - WAITING FOR CALLS...")
            logger.info("=" * 80)

            # Run until stopped
            await ari_client.run(apps=ari_app)

        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"❌ ARI CONNECTION ERROR: {e}")
            logger.error(traceback.format_exc())
            logger.error("=" * 80)
            with self._status_lock:
                self.stats['last_error'] = str(e)
                self.stats['ari_connected'] = False
                self.running = False
                self._service_state = "stopped"
        finally:
            if 'ari_client' in locals():
                try:
                    await ari_client.close()
                except:
                    pass
            with self._status_lock:
                self.running = False
                self._service_state = "stopped"
            logger.info("🛑 ARI Connection closed")

    async def _handle_call(self, channel_id, event, ari_client):
        """Handle incoming call - WITH PROPER SESSION MANAGEMENT"""
        try:
            channel = await ari_client.channels.get(channelId=channel_id)
            caller_number = event.get('channel', {}).get('caller', {}).get('number', 'Unknown')

            # Store active call
            with self._status_lock:
                self.active_calls[channel_id] = {
                    'start_time': datetime.utcnow(),
                    'caller_number': caller_number,
                    'channel': channel
                }

            logger.info(f"🎯 Processing call from {caller_number}...")

            # Use config container directly (no DB access in background thread)
            with self.app.app_context():
                from blueprints.ai_agent_handler import handle_call
                await handle_call(channel, ari_client, self.config_container, caller_number)

            logger.info(f"✅ Call from {caller_number} completed")

        except Exception as e:
            logger.error(f"❌ CALL HANDLING ERROR: {e}")
            logger.error(traceback.format_exc())
            with self._status_lock:
                self.stats['last_error'] = f"Call handling: {str(e)}"
        finally:
            # Remove from active calls
            with self._status_lock:
                self.active_calls.pop(channel_id, None)

    async def _hangup_call(self, channel_id):
        """Hangup a specific call"""
        with self._status_lock:
            call_info = self.active_calls.get(channel_id)

        if call_info and 'channel' in call_info:
            try:
                await call_info['channel'].hangup()
            except:
                pass

    def get_status(self):
        """Get comprehensive service status - THREAD SAFE"""
        with self._status_lock:
            # FIXED: Base running status on service state
            is_running = self._service_state == "running"

            uptime = None
            if self.stats['start_time'] and is_running:
                uptime = (datetime.utcnow() - self.stats['start_time']).seconds

            # Create snapshot of active calls
            active_calls_snapshot = [
                {
                    'channel_id': cid,
                    'caller_number': info['caller_number'],
                    'duration': (datetime.utcnow() - info['start_time']).seconds
                }
                for cid, info in self.active_calls.items()
            ]

            return {
                'running': is_running,
                'service_state': self._service_state,  # For debugging
                'active_calls': len(active_calls_snapshot),
                'calls': active_calls_snapshot,
                'uptime_seconds': uptime,
                'total_calls_handled': self.stats.get('total_calls_handled', 0),
                'connections': {
                    'azure_openai': self.stats.get('azure_openai_connected', False),
                    'azure_speech': self.stats.get('azure_speech_connected', False),
                    'ari': self.stats.get('ari_connected', False),
                    'ssh': self.stats.get('ssh_connected', False),
                    'dataverse': self.stats.get('dataverse_connected', False)
                },
                'cache': {
                    'loaded': self.stats.get('cache_loaded', False),
                    'phrases_count': self.stats.get('cache_phrases_count', 0)
                },
                'config_loaded': self.stats.get('config_loaded', False),
                'last_error': self.stats.get('last_error')
            }


# Global service instance
ai_service = AIAgentService()


# ============================================================================
# FLASK ROUTES
# ============================================================================

@ai_agent_integrated_bp.route('/')
def index():
    """AI Agent configuration and management page"""
    try:
        log_action(
            action='view',
            resource_type='ai_agent_config_page',
            details='Accessed AI Agent configuration page'
        )
        return render_template('ai_agent/index.html')
    except Exception as e:
        logger.error(f"Error rendering index: {e}")
        return f"Error: {str(e)}", 500


@ai_agent_integrated_bp.route('/api/config', methods=['GET'])
def get_config():
    """Get current AI agent configuration"""
    try:
        config = AIAgentConfig.query.first()

        if not config:
            config = AIAgentConfig(name='Default Configuration')
            db.session.add(config)
            db.session.commit()

        log_action(
            action='view',
            resource_type='ai_agent_config',
            details={'config_id': config.id}
        )

        return jsonify({
            'success': True,
            'config': config.to_dict()
        })

    except Exception as e:
        logger.error(f"Error getting config: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_integrated_bp.route('/api/config', methods=['PUT'])
def update_config():
    """Update AI agent configuration"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request must be JSON'
            }), 400

        data = request.get_json()
        config = AIAgentConfig.query.first()

        if not config:
            config = AIAgentConfig(name=data.get('name', 'Default Configuration'))
            db.session.add(config)

        # Update user-configurable fields only
        if 'enabled' in data:
            config.enabled = data['enabled']
        if 'system_prompt' in data:
            config.system_prompt = data['system_prompt']
        if 'greeting_template' in data:
            config.greeting_template = data['greeting_template']
        if 'company_name' in data:
            config.company_name = data['company_name']
        if 'max_turns' in data:
            config.max_turns = data['max_turns']
        if 'recording_duration' in data:
            config.recording_duration = data['recording_duration']
        if 'silence_duration' in data:
            config.silence_duration = data['silence_duration']
        if 'store_recordings' in data:
            config.store_recordings = data['store_recordings']
        if 'store_transcripts' in data:
            config.store_transcripts = data['store_transcripts']

        config.updated_at = datetime.utcnow()
        db.session.commit()

        log_action(
            action='update',
            resource_type='ai_agent_config',
            resource_id=str(config.id),
            details=data
        )

        return jsonify({
            'success': True,
            'message': 'Configuration updated successfully',
            'config': config.to_dict()
        })

    except Exception as e:
        logger.error(f"Error updating config: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_integrated_bp.route('/api/departments', methods=['GET'])
def get_departments():
    """Get all transfer departments"""
    try:
        departments = AIAgentDepartment.query.all()

        log_action(
            action='view',
            resource_type='ai_agent_departments',
            details={'count': len(departments)}
        )

        return jsonify({
            'success': True,
            'departments': [dept.to_dict() for dept in departments]
        })

    except Exception as e:
        logger.error(f"Error getting departments: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_integrated_bp.route('/api/departments', methods=['POST'])
def create_department():
    """Create new transfer department"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request must be JSON'
            }), 400

        data = request.get_json()

        department = AIAgentDepartment(
            name=data['name'],
            display_name=data['display_name'],
            extension=data['extension'],
            endpoint=data['endpoint'],
            context=data.get('context', 'from-internal'),
            enabled=data.get('enabled', True),
            description=data.get('description', '')
        )

        db.session.add(department)
        db.session.commit()

        log_action(
            action='create',
            resource_type='ai_agent_department',
            resource_id=str(department.id),
            details=data
        )

        return jsonify({
            'success': True,
            'message': 'Department created successfully',
            'department': department.to_dict()
        })

    except Exception as e:
        logger.error(f"Error creating department: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_integrated_bp.route('/api/departments/<int:dept_id>', methods=['DELETE'])
def delete_department(dept_id):
    """Delete a department"""
    try:
        department = AIAgentDepartment.query.get_or_404(dept_id)

        log_action(
            action='delete',
            resource_type='ai_agent_department',
            resource_id=str(dept_id),
            details={'name': department.name}
        )

        db.session.delete(department)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Department deleted successfully'
        })

    except Exception as e:
        logger.error(f"Error deleting department: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_integrated_bp.route('/api/service/status', methods=['GET'])
def service_status():
    """Get AI agent service status with detailed stats"""
    try:
        status = ai_service.get_status()

        return jsonify({
            'success': True,
            'status': status
        })

    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_integrated_bp.route('/api/service/start', methods=['POST'])
def start_service():
    """Start AI agent service"""
    try:
        logger.info("🔥 START SERVICE REQUEST received")

        config = AIAgentConfig.query.first()

        if not config:
            logger.error("❌ No configuration found")
            return jsonify({
                'success': False,
                'error': 'No configuration found. Please configure the AI agent first.'
            }), 400

        if not config.enabled:
            logger.error("❌ AI agent is disabled in configuration")
            return jsonify({
                'success': False,
                'error': 'AI agent is disabled in configuration'
            }), 400

        success, message = ai_service.start(config)

        if success:
            log_action(
                action='start',
                resource_type='ai_agent_service',
                details={'config_id': config.id}
            )

        return jsonify({
            'success': success,
            'message': message
        })

    except Exception as e:
        logger.error(f"❌ Error starting service: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500



@ai_agent_integrated_bp.route('/api/service/stop', methods=['POST'])
def stop_service():
    """Stop AI agent service"""
    try:
        logger.info("🔥 STOP SERVICE REQUEST received")

        success, message = ai_service.stop()

        if success:
            log_action(
                action='stop',
                resource_type='ai_agent_service',
                details='Service stopped manually'
            )

        return jsonify({
            'success': success,
            'message': message
        })

    except Exception as e:
        logger.error(f"❌ Error stopping service: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_integrated_bp.route('/api/calls', methods=['GET'])
def get_call_logs():
    """Get call logs with pagination"""
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))

        query = AIAgentCallLog.query.order_by(AIAgentCallLog.created_at.desc())

        # Filter by date range if provided
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if start_date:
            query = query.filter(AIAgentCallLog.created_at >= start_date)
        if end_date:
            query = query.filter(AIAgentCallLog.created_at <= end_date)

        # Filter by caller number if provided
        caller = request.args.get('caller')
        if caller:
            query = query.filter(AIAgentCallLog.caller_number.contains(caller))

        paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        return jsonify({
            'success': True,
            'calls': [call.to_dict() for call in paginated.items],
            'total': paginated.total,
            'pages': paginated.pages,
            'current_page': page
        })

    except Exception as e:
        logger.error(f"Error getting call logs: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_agent_integrated_bp.route('/api/calls/<int:call_id>', methods=['GET'])
def get_call_detail(call_id):
    """Get detailed call information including turns"""
    try:
        call = AIAgentCallLog.query.get_or_404(call_id)
        turns = AIAgentTurn.query.filter_by(call_log_id=call_id).order_by(AIAgentTurn.turn_number).all()

        log_action(
            action='view',
            resource_type='ai_agent_call',
            resource_id=str(call_id),
            details={'call_id': call.call_id}
        )

        return jsonify({
            'success': True,
            'call': call.to_dict(),
            'turns': [turn.to_dict() for turn in turns]
        })

    except Exception as e:
        logger.error(f"Error getting call detail: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500