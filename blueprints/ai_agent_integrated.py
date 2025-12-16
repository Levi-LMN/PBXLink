"""
Integrated AI Agent Blueprint - FIXED AUDIO RECORDING ISSUES
Fixed by implementing standalone SSH connection in background thread
"""

from flask import Blueprint, render_template, jsonify, request, current_app
import logging
import asyncio
import threading
import json
import os
import traceback
import tempfile
import time
import hashlib
import requests
import paramiko
from datetime import datetime, timedelta
from pathlib import Path
from audit_utils import log_action
from models import db, AIAgentConfig, AIAgentDepartment, AIAgentCallLog, AIAgentTurn

# =============================================================================
# CLEAR FOCUSED LOGGING SETUP
# =============================================================================

logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('ssh_manager').setLevel(logging.WARNING)
logging.getLogger('blueprints').setLevel(logging.WARNING)
logging.getLogger('gtts.tts').setLevel(logging.WARNING)
logging.getLogger('aioswagger11.client').setLevel(logging.WARNING)
logging.getLogger('aioari.client').setLevel(logging.WARNING)
logging.getLogger('aioari.model').setLevel(logging.WARNING)
logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)

ai_logger = logging.getLogger('AI_AGENT')
ai_logger.setLevel(logging.INFO)
ai_logger.handlers = []

console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('\n%(asctime)s 🤖 [AI-AGENT] %(message)s', datefmt='%H:%M:%S')
console.setFormatter(formatter)
ai_logger.addHandler(console)
ai_logger.propagate = False

logger = ai_logger

ai_agent_integrated_bp = Blueprint('ai_agent_integrated', __name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

CACHE_DIR = Path.home() / ".asterisk_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_INDEX_FILE = CACHE_DIR / "cache_index.json"

ASTERISK_SOUNDS_DIR = "/var/lib/asterisk/sounds/custom"

# Global cache for Dataverse token
dataverse_token = None
dataverse_token_expiry = 0


# ============================================================================
# CONFIGURATION CONTAINER
# ============================================================================

class AIAgentConfigContainer:
    """Thread-safe configuration container"""

    def __init__(self, config_dict):
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

        # SSH credentials from environment
        freepbx_host = os.environ.get('FREEPBX_HOST', 'http://10.200.200.2:80')
        self.ssh_host = freepbx_host.replace('http://', '').replace('https://', '').split(':')[0]
        self.ssh_user = os.environ.get('FREEPBX_SSH_USER', 'root')
        self.ssh_password = os.environ.get('FREEPBX_SSH_PASSWORD')

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
# STANDALONE SSH FOR BACKGROUND THREAD - NO FLASK CONTEXT NEEDED
# ============================================================================

class StandaloneSSH:
    """Standalone SSH connection for background thread - doesn't need Flask context"""

    def __init__(self, host, user, password):
        self.host = host
        self.user = user
        self.password = password
        self.client = None
        self.sftp = None
        self._lock = asyncio.Lock()

    async def connect(self):
        """Connect to SSH server"""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.connect(
                    self.host,
                    username=self.user,
                    password=self.password,
                    timeout=10,
                    look_for_keys=False,
                    allow_agent=False
                )
            )

            self.sftp = self.client.open_sftp()
            logger.info(f"✅ SSH connected to {self.host}")
            return True
        except Exception as e:
            logger.error(f"❌ SSH connection failed: {e}")
            return False

    async def _cmd(self, command):
        """Execute command"""
        try:
            _, stdout, _ = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.exec_command(command)
            )
            return stdout.channel.recv_exit_status() == 0
        except:
            return False

    async def upload(self, local_file, remote_filename):
        """Upload file to Asterisk sounds directory"""
        async with self._lock:
            try:
                if not self.sftp and not await self.connect():
                    return None

                temp_path = f"/tmp/{remote_filename}"

                # Upload to temp
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self.sftp.put(local_file, temp_path)
                )

                # Move to sounds directory with proper permissions
                await self._cmd(f"sudo mv {temp_path} {ASTERISK_SOUNDS_DIR}/{remote_filename}")
                await self._cmd(f"sudo chown asterisk:asterisk {ASTERISK_SOUNDS_DIR}/{remote_filename}")
                await self._cmd(f"sudo chmod 644 {ASTERISK_SOUNDS_DIR}/{remote_filename}")

                return f"custom/{remote_filename.replace('.wav', '')}"
            except Exception as e:
                logger.error(f"❌ Upload failed: {e}")
                return None

    def close(self):
        """Close SSH connection"""
        try:
            if self.sftp:
                self.sftp.close()
            if self.client:
                self.client.close()
        except:
            pass


# ============================================================================
# SOUND CACHE
# ============================================================================

class SoundCache:
    """Cache TTS audio files locally and on Asterisk"""

    def __init__(self):
        self.index = json.load(open(CACHE_INDEX_FILE)) if CACHE_INDEX_FILE.exists() else {}

    def _save(self):
        try:
            json.dump(self.index, open(CACHE_INDEX_FILE, 'w'))
        except:
            pass

    def _key(self, text):
        return hashlib.md5(text.encode()).hexdigest()

    def _duration(self, path):
        try:
            from pydub import AudioSegment
            return len(AudioSegment.from_file(path)) / 1000.0
        except:
            return None

    async def get(self, text, ssh):
        """Get cached sound or generate new one"""
        k = self._key(text)

        # Check if already cached on Asterisk
        if k in self.index and self.index[k].get('remote'):
            return self.index[k]['remote'], self.index[k].get('duration')

        # Generate TTS locally
        local = await self._tts(text, k)
        if not local:
            return None, None

        duration = self._duration(local)

        # Upload to Asterisk
        remote = await ssh.upload(local, f"c_{k}.wav")
        if remote:
            self.index[k] = {'remote': remote, 'duration': duration}
            self._save()

        return (remote or local), duration

    async def _tts(self, text, k):
        """Generate TTS audio"""
        try:
            f = CACHE_DIR / f"{k}.wav"
            if f.exists():
                return str(f)

            tmp = CACHE_DIR / f"{k}_t.wav"

            try:
                from gtts import gTTS
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: gTTS(text=text, lang='en', slow=False).save(str(tmp))
                )
            except:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._pyttsx, text, str(tmp)
                )

            # Normalize audio
            from pydub import AudioSegment
            from pydub.effects import normalize
            audio = AudioSegment.from_file(str(tmp))
            audio = normalize(audio).set_frame_rate(8000).set_channels(1).set_sample_width(2)
            audio.export(str(f), format="wav")

            try:
                tmp.unlink()
            except:
                pass

            return str(f)
        except Exception as e:
            logger.error(f"❌ TTS failed: {e}")
            return None

    def _pyttsx(self, text, output):
        """Fallback TTS"""
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty('rate', 165)
            engine.save_to_file(text, output)
            engine.runAndWait()
            engine.stop()
        except:
            pass


# ============================================================================
# DATAVERSE INTEGRATION
# ============================================================================

def normalize_phone(phone):
    """Normalize Kenyan phone to +254 format"""
    if not phone:
        return None
    cleaned = ''.join(c for c in phone if c.isdigit() or c == '+')
    if cleaned.startswith('+'):
        cleaned = cleaned[1:]

    if cleaned.startswith('0') and len(cleaned) == 10:
        cleaned = '254' + cleaned[1:]
    elif (cleaned.startswith('7') or cleaned.startswith('1')) and len(cleaned) == 9:
        cleaned = '254' + cleaned

    if cleaned.startswith('254') and len(cleaned) == 12:
        return '+' + cleaned
    return phone


async def get_dataverse_token():
    """Get Dataverse access token with caching"""
    global dataverse_token, dataverse_token_expiry

    dataverse_url = os.environ.get('DATAVERSE_URL')
    tenant_id = os.environ.get('TENANT_ID')
    client_id = os.environ.get('CLIENT_ID')
    client_secret = os.environ.get('CLIENT_SECRET')

    if not all([dataverse_url, tenant_id, client_id, client_secret]):
        return None

    if dataverse_token and time.time() < (dataverse_token_expiry - 300):
        return dataverse_token

    try:
        from msal import ConfidentialClientApplication

        app = ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret
        )

        loop = asyncio.get_running_loop()
        token_response = await loop.run_in_executor(
            None,
            lambda: app.acquire_token_for_client(scopes=[f"{dataverse_url}/.default"])
        )

        if "access_token" not in token_response:
            logger.error(f"❌ Token failed: {token_response.get('error_description', 'Unknown')}")
            return None

        dataverse_token = token_response["access_token"]
        dataverse_token_expiry = time.time() + token_response.get("expires_in", 3600)
        return dataverse_token
    except Exception as e:
        logger.error(f"❌ Token error: {e}")
        return None


async def fetch_customer_claims(phone):
    """Fetch customer claims from Dataverse"""
    token = await get_dataverse_token()
    if not token:
        return None

    dataverse_url = os.environ.get('DATAVERSE_URL')
    normalized = normalize_phone(phone)
    variants = list(set([
        phone,
        normalized,
        normalized[1:] if normalized and normalized.startswith('+') else None,
        '0' + normalized[4:] if normalized and normalized.startswith('+254') else None,
        normalized[4:] if normalized and normalized.startswith('+254') else None
    ]))
    variants = [v for v in variants if v]

    logger.info(f"📊 Searching: {normalized} with {len(variants)} variants")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Prefer": "odata.include-annotations=*"
    }

    columns = "cra47_testclaimid,cra47_claimnumber,cra47_claimstatus,cra47_estimatedresolution,cra47_amountapproved,cra47_useremail,cra47_username,cra47_newcolumn,cra47_userphone"

    filter_parts = [f"cra47_userphone eq '{v.replace(chr(39), chr(39)+chr(39))}'" for v in variants]
    filter_query = " or ".join(filter_parts)

    from urllib.parse import quote
    url = f"{dataverse_url}/api/data/v9.2/cra47_testclaims?$select={columns}&$filter={quote(filter_query)}"

    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: requests.get(url, headers=headers, timeout=10)
    )

    if not response.ok:
        logger.error(f"❌ Query failed: {response.status_code}")
        return None

    claims_data = response.json().get("value", [])

    if not claims_data:
        logger.warning(f"⚠️ No claims found for {normalized}")
        return None

    formatted_claims = []
    customer_name = customer_email = customer_phone = None

    for claim in claims_data:
        formatted_claims.append({
            "claim_id": claim.get("cra47_claimnumber", "N/A"),
            "type": claim.get("cra47_newcolumn", "N/A"),
            "status": claim.get("cra47_claimstatus", "Pending"),
            "amount": claim.get("cra47_amountapproved", "N/A"),
            "estimated_resolution": claim.get("cra47_estimatedresolution", "N/A")
        })

        if not customer_name:
            customer_name = claim.get("cra47_username", "Valued Customer")
            customer_email = claim.get("cra47_useremail", "")
            customer_phone = claim.get("cra47_userphone", normalized)

    clean = ''.join(filter(str.isdigit, normalized or phone))
    logger.info(f"✅ Found {len(formatted_claims)} claim(s) for {customer_name}")

    return {
        "phone": customer_phone or normalized or phone,
        "name": customer_name,
        "email": customer_email,
        "account_number": f"ACC{clean[-6:]}" if clean else "ACC000000",
        "account_status": "Active",
        "claims": formatted_claims,
        "services": ["Claims Management", "Insurance Services"],
        "last_contact": time.strftime("%Y-%m-%d")
    }


async def fetch_customer_data(phone):
    """Fetch customer data - Dataverse first, fallback to defaults"""
    dataverse_url = os.environ.get('DATAVERSE_URL')
    tenant_id = os.environ.get('TENANT_ID')
    client_id = os.environ.get('CLIENT_ID')
    client_secret = os.environ.get('CLIENT_SECRET')

    if dataverse_url and tenant_id and client_id and client_secret:
        data = await fetch_customer_claims(phone)
        if data:
            return data

    clean = ''.join(filter(str.isdigit, phone))
    return {
        "phone": phone,
        "name": "Valued Customer",
        "email": "",
        "account_number": f"ACC{clean[-6:]}" if clean else "ACC000000",
        "account_status": "Active",
        "claims": [],
        "services": ["Business Consulting", "Cloud Solutions"],
        "last_contact": time.strftime("%Y-%m-%d")
    }


# ============================================================================
# CALL HANDLER
# ============================================================================

class Call:
    """Handles an individual AI agent call"""

    def __init__(self, channel, ari_client, config, caller_number, ai_client, speech_config, cache, ssh, app_instance):
        self.channel = channel
        self.ari_client = ari_client
        self.config = config
        self.caller_number = caller_number
        self.call_id = channel.id
        self.ai_client = ai_client
        self.speech_config = speech_config
        self.cache = cache
        self.ssh = ssh
        self.app = app_instance

        # Call state
        self.active = True
        self.conversation = []
        self.customer_data = {}
        self.temp_files = []
        self.functions_called = []
        self.start_time = datetime.utcnow()
        self.should_end = False

        # Metrics
        self.turn_count = 0
        self.no_speech_count = 0
        self.unclear_count = 0

        # Create call log entry (with app context)
        with self.app.app_context():
            self.call_log = AIAgentCallLog(
                call_id=self.call_id,
                caller_number=caller_number,
                call_start=self.start_time
            )
            db.session.add(self.call_log)
            db.session.commit()

    async def alive(self):
        """Check if call is still active"""
        if not self.active:
            return False
        try:
            await self.ari_client.channels.get(channelId=self.call_id)
            return True
        except:
            self.active = False
            return False

    async def speak(self, text):
        """Speak text using cached TTS"""
        if not await self.alive():
            return False

        try:
            sound, duration = await self.cache.get(text, self.ssh)
            if not sound:
                return False

            await self.channel.play(media=f"sound:{sound}")
            await asyncio.sleep((duration or len(text.split()) * 0.4) + 0.5)
            return True
        except Exception as e:
            if "404" not in str(e):
                logger.error(f"❌ Speak error: {e}")
            self.active = False
            return False

    async def record(self):
        """Record user audio"""
        if not await self.alive():
            return None

        recording_name = f"r_{self.call_id}_{int(time.time() * 1000)}"

        try:
            rec = await self.channel.record(
                name=recording_name,
                format="wav",
                maxDurationSeconds=self.config.recording_duration,
                maxSilenceSeconds=self.config.silence_duration,
                ifExists="overwrite",
                terminateOn="none"
            )

            await asyncio.sleep(self.config.recording_duration + 0.5)

            try:
                await rec.stop()
            except:
                pass

            await asyncio.sleep(0.2)
            return await self._download(recording_name)
        except Exception as e:
            logger.error(f"❌ Record error: {e}")
            return None

    async def _download(self, recording_name):
        """Download recording from ARI"""
        ari_url = os.environ.get("ARI_URL", "http://10.200.200.2:8088/ari")
        ari_username = os.environ.get("ARI_USERNAME", "asterisk")
        ari_password = os.environ.get("ARI_PASSWORD")

        for _ in range(3):
            try:
                url = f"{ari_url}/recordings/stored/{recording_name}/file"
                r = requests.get(url, auth=(ari_username, ari_password), timeout=10)

                if r.status_code == 200 and len(r.content) > 4000:
                    f = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                    f.write(r.content)
                    f.close()
                    self.temp_files.append(f.name)
                    return f.name
            except:
                pass
            await asyncio.sleep(0.15)
        return None

    async def transcribe(self, audio_file):
        """Transcribe audio using Azure Speech"""
        try:
            if os.path.getsize(audio_file) < 4000:
                return "", "low"

            processed = await self._preprocess_audio(audio_file)

            import azure.cognitiveservices.speech as speechsdk
            audio_config = speechsdk.audio.AudioConfig(filename=processed)
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=audio_config
            )

            result = await asyncio.get_running_loop().run_in_executor(
                None, recognizer.recognize_once
            )

            text = result.text.strip() if result.reason == speechsdk.ResultReason.RecognizedSpeech else ""
            confidence = "high" if text else "low"

            if processed != audio_file:
                try:
                    os.unlink(processed)
                except:
                    pass

            return text, confidence
        except Exception as e:
            logger.error(f"❌ Transcribe error: {e}")
            return "", "low"

    async def _preprocess_audio(self, audio_file):
        """Preprocess audio for better recognition"""
        try:
            from pydub import AudioSegment
            from pydub.effects import normalize

            audio = AudioSegment.from_file(audio_file)
            audio = normalize(audio).set_frame_rate(16000).set_channels(1).set_sample_width(2)
            processed = audio_file.replace('.wav', '_proc.wav')
            audio.export(processed, format="wav")
            return processed
        except:
            return audio_file

    async def get_ai_response(self, confidence):
        """Get AI response with function calling"""
        messages = self.conversation.copy()

        if confidence == "low":
            messages.append({
                "role": "system",
                "content": "Poor audio. Ask once for clarification, then offer callback."
            })

        try:
            tools = self._get_ai_tools()

            r = await self.ai_client.chat.completions.create(
                model=self.config.azure_deployment,
                messages=messages,
                max_tokens=200,
                temperature=0.9,
                tools=tools,
                tool_choice="auto"
            )

            msg = r.choices[0].message

            if msg.tool_calls:
                tc = msg.tool_calls[0]
                fn = tc.function.name
                args = json.loads(tc.function.arguments)

                result = await self.execute_function(fn, args)

                self.functions_called.append({
                    'name': fn,
                    'args': args,
                    'result': result,
                    'time': datetime.utcnow().isoformat()
                })

                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": fn,
                            "arguments": tc.function.arguments
                        }
                    }]
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn,
                    "content": json.dumps(result)
                })

                r2 = await self.ai_client.chat.completions.create(
                    model=self.config.azure_deployment,
                    messages=messages,
                    max_tokens=200,
                    temperature=0.9
                )

                resp = r2.choices[0].message.content.strip()

                if fn == "transfer_to_agent" and result.get('success'):
                    return "", fn

                return resp, fn

            return msg.content.strip(), None
        except Exception as e:
            logger.error(f"❌ AI error: {e}")
            return "Technical issue. Repeat that?", None

    async def execute_function(self, fn, args):
        """Execute AI function call"""
        if fn == "create_ticket":
            ticket_id = f"TKT-{int(time.time())}"
            logger.info(f"🎫 Ticket: {ticket_id} - {args.get('subject')}")

            with self.app.app_context():
                if not self.call_log.tickets_created:
                    self.call_log.tickets_created = json.dumps([])

                tickets = json.loads(self.call_log.tickets_created)
                tickets.append({
                    'ticket_id': ticket_id,
                    'subject': args.get('subject'),
                    'priority': args.get('priority'),
                    'description': args.get('description')
                })
                self.call_log.tickets_created = json.dumps(tickets)
                db.session.commit()

            return {"status": "success", "ticket_id": ticket_id, "estimated_response": "24 hours"}

        elif fn == "transfer_to_agent":
            dept_name = args.get('department')

            with self.app.app_context():
                department = AIAgentDepartment.query.filter_by(name=dept_name, enabled=True).first()

                if not department:
                    logger.error(f"Department not found: {dept_name}")
                    return {"status": "failed", "success": False}

                logger.info(f"📞 Transfer to {department.display_name}")

                msg = f"Connecting you to {department.display_name} now."
                await self.speak(msg)

                self.call_log.transferred_to = department.display_name
                db.session.commit()

                try:
                    await self.channel.continueInDialplan(
                        context=department.context,
                        extension=department.extension,
                        priority=1
                    )
                    return {"status": "transferred", "department": dept_name, "success": True}
                except Exception as e:
                    logger.error(f"❌ Transfer failed: {e}")
                    return {"status": "failed", "error": str(e), "success": False}

        elif fn == "schedule_callback":
            cb_id = f"CB-{int(time.time())}"
            logger.info(f"📅 Callback: {cb_id} at {args.get('preferred_time')}")
            return {"status": "scheduled", "callback_id": cb_id}

        elif fn == "end_call":
            self.should_end = True
            return {"status": "ending", "reason": args.get('reason')}

        return {"status": "unknown"}

    def _get_ai_tools(self):
        """Get AI function tools"""
        with self.app.app_context():
            departments = AIAgentDepartment.query.filter_by(enabled=True).all()
            dept_names = [dept.name for dept in departments]

        tools = [
            {"type": "function", "function": {"name": "end_call", "description": "End call when done", "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
            {"type": "function", "function": {"name": "create_ticket", "description": "Create support ticket", "parameters": {"type": "object", "properties": {"subject": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]}, "description": {"type": "string"}}, "required": ["subject", "priority", "description"]}}},
            {"type": "function", "function": {"name": "schedule_callback", "description": "Schedule callback", "parameters": {"type": "object", "properties": {"preferred_time": {"type": "string"}, "reason": {"type": "string"}}, "required": ["preferred_time", "reason"]}}}
        ]

        if dept_names:
            tools.append({"type": "function", "function": {"name": "transfer_to_agent", "description": "Transfer to human agent", "parameters": {"type": "object", "properties": {"department": {"type": "string", "enum": dept_names}, "reason": {"type": "string"}}, "required": ["department", "reason"]}}})

        return tools

    def _build_system_prompt(self):
        """Build system prompt with customer context"""
        prompt = self.config.system_prompt + "\n\n"

        if self.customer_data:
            prompt += "CUSTOMER CONTEXT:\n"
            prompt += f"- Name: {self.customer_data.get('name')}\n"
            prompt += f"- Account: {self.customer_data.get('account_number')}\n"
            prompt += f"- Phone: {self.customer_data.get('phone')}\n"

            claims = self.customer_data.get('claims', [])
            if claims:
                prompt += f"\nCLAIMS ({len(claims)} found):\n"
                for i, c in enumerate(claims, 1):
                    prompt += f"{i}. #{c['claim_id']} - {c['type']} - {c['status']} - {c['amount']}\n"
                prompt += "\nProvide claim details when asked."
            else:
                prompt += "\nNO CLAIMS found. Offer to create ticket if asked about claims.\n"

        return prompt

    def _build_greeting(self):
        """Build time-appropriate greeting"""
        eat_time = datetime.utcnow() + timedelta(hours=3)
        hour = eat_time.hour

        if hour < 12:
            time_greeting = 'Good morning'
        elif hour < 17:
            time_greeting = 'Good afternoon'
        else:
            time_greeting = 'Good evening'

        return self.config.greeting_template.format(
            time_greeting=time_greeting,
            company_name=self.config.company_name
        )

    def _is_goodbye(self, text):
        """Check if text is goodbye"""
        if len(text.split()) <= 4:
            text_lower = text.lower()
            return any(p in text_lower for p in ["bye", "goodbye", "thanks", "thank you", "that's all", "done"])
        return False

    async def _save_turn(self, turn_num, user_text, confidence, ai_text, function_name, audio_path):
        """Save conversation turn"""
        try:
            with self.app.app_context():
                turn = AIAgentTurn(
                    call_log_id=self.call_log.id,
                    turn_number=turn_num,
                    user_text=user_text,
                    user_confidence=confidence,
                    ai_text=ai_text,
                    function_called=function_name,
                    user_audio_path=audio_path if self.config.store_recordings else None
                )
                db.session.add(turn)
                db.session.commit()
        except Exception as e:
            logger.error(f"Failed to save turn: {e}")
            with self.app.app_context():
                db.session.rollback()

    async def hangup(self):
        """Hangup call"""
        try:
            if self.active:
                await self.channel.hangup()
        except:
            pass
        self.active = False

    async def cleanup(self):
        """Cleanup and finalize call log"""
        for f in self.temp_files:
            try:
                os.unlink(f)
            except:
                pass

        with self.app.app_context():
            self.call_log.call_end = datetime.utcnow()
            self.call_log.call_duration = (self.call_log.call_end - self.call_log.call_start).seconds

            if self.config.store_transcripts:
                transcript = "\n\n".join([
                    f"{'Customer' if m['role'] == 'user' else 'AI'}: {m.get('content', '')}"
                    for m in self.conversation if m['role'] in ['user', 'assistant'] and m.get('content')
                ])
                self.call_log.transcript = transcript

            self.call_log.functions_called = json.dumps(self.functions_called)
            self.call_log.turns_count = self.turn_count
            self.call_log.no_speech_count = self.no_speech_count
            self.call_log.unclear_count = self.unclear_count

            db.session.commit()


async def handle_call(channel, ari_client, config, caller_number, ssh, app_instance):
    """Main call handler"""

    from openai import AsyncAzureOpenAI
    import azure.cognitiveservices.speech as speechsdk

    # Initialize AI client
    ai_client = AsyncAzureOpenAI(
        api_key=config.azure_api_key,
        api_version="2024-08-01-preview",
        azure_endpoint=config.azure_endpoint
    )

    # Initialize speech config
    speech_config = speechsdk.SpeechConfig(
        subscription=config.azure_speech_key,
        region=config.azure_speech_region
    )
    speech_config.speech_recognition_language = "en-US"

    # Initialize cache
    cache = SoundCache()

    # Fetch customer data
    customer_data = await fetch_customer_data(caller_number)

    # Create call handler
    call = Call(channel, ari_client, config, caller_number, ai_client, speech_config, cache, ssh, app_instance)
    call.customer_data = customer_data

    with app_instance.app_context():
        call.call_log.caller_name = customer_data.get('name', 'Valued Customer')
        call.call_log.customer_data = json.dumps(customer_data)
        db.session.commit()

    try:
        await channel.answer()
        await asyncio.sleep(0.2)

        # Print customer context
        print(f"\n{'='*60}")
        print(f"📞 CALL FROM: {customer_data.get('name')} ({customer_data.get('phone')})")
        print(f"📋 Account: {customer_data.get('account_number')}")
        claims = customer_data.get('claims', [])
        if claims:
            print(f"🎫 Claims: {len(claims)} found")
            for i, claim in enumerate(claims, 1):
                print(f"   {i}. #{claim['claim_id']} - {claim['status']} - {claim['amount']}")
        else:
            print(f"🎫 Claims: None")
        print(f"{'='*60}\n")

        # Build system context
        system_prompt = call._build_system_prompt()
        call.conversation.append({"role": "system", "content": system_prompt})

        # Greeting
        greeting = call._build_greeting()
        if not await call.speak(greeting):
            return
        call.conversation.append({"role": "assistant", "content": greeting})

        # Beep
        await asyncio.sleep(config.beep_delay)
        await call.channel.play(media="sound:beep")
        await asyncio.sleep(config.beep_pause)

        # Main conversation loop
        for turn_num in range(config.max_turns):
            if not call.active or call.should_end:
                break

            # Record
            rec = await call.record()

            # Beep acknowledge
            await call.channel.play(media="sound:beep")
            await asyncio.sleep(0.1)

            if not rec:
                call.no_speech_count += 1
                if call.no_speech_count >= 2:
                    await call.speak("Having trouble hearing you. Call back if needed.")
                    break
                await call.speak("Didn't catch that. Go ahead.")
                await asyncio.sleep(config.beep_delay)
                await call.channel.play(media="sound:beep")
                await asyncio.sleep(config.beep_pause)
                continue

            # Transcribe
            text, confidence = await call.transcribe(rec)
            call.no_speech_count = 0

            if not text or len(text) < 3:
                call.unclear_count += 1
                if call.unclear_count >= 2:
                    await call.speak("Let me have someone call you back.")
                    break
                await call.speak("Could you repeat that?")
                await asyncio.sleep(config.beep_delay)
                await call.channel.play(media="sound:beep")
                await asyncio.sleep(config.beep_pause)
                continue

            call.unclear_count = 0

            # Check goodbye
            if call._is_goodbye(text):
                await call.speak("Anytime! Bye!")
                break

            # Add to conversation
            call.conversation.append({"role": "user", "content": text})

            # Get AI response
            resp, fn = await call.get_ai_response(confidence)

            # Save turn
            call.turn_count += 1
            await call._save_turn(
                call.turn_count,
                text,
                confidence,
                resp,
                fn,
                rec
            )

            # Add response
            call.conversation.append({"role": "assistant", "content": resp})

            # Handle transfer
            if fn == "transfer_to_agent" and call.functions_called and call.functions_called[-1].get('result', {}).get('success'):
                return

            # Check end
            if fn == "end_call":
                call.should_end = True

            # Speak response
            if resp and not await call.speak(resp):
                break

            if call.should_end:
                break

            # Beep for next turn
            await asyncio.sleep(config.beep_delay)
            await call.channel.play(media="sound:beep")
            await asyncio.sleep(config.beep_pause)

        # Final goodbye
        if not call.should_end:
            await call.speak("Thanks for calling!")

        await call.hangup()

    except Exception as e:
        logger.error(f"❌ Call error: {e}")
        await call.hangup()
    finally:
        await call.cleanup()


# ============================================================================
# AI AGENT SERVICE MANAGER
# ============================================================================

class AIAgentService:
    """Manages the AI agent service lifecycle"""

    def __init__(self):
        self.running = False
        self.thread = None
        self.loop = None
        self.active_calls = {}
        self.app = None
        self.config_container = None
        self.ssh = None

        self._status_lock = threading.Lock()
        self._service_state = "stopped"

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
        """Start AI agent service"""
        with self._status_lock:
            if self.running or self._service_state in ["starting", "running"]:
                return False, "Service already running or starting"

            try:
                from flask import current_app
                self.app = current_app._get_current_object()

                # Get credentials
                azure_endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
                azure_api_key = os.environ.get('AZURE_OPENAI_API_KEY')
                azure_deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o-mini')
                azure_speech_key = os.environ.get('AZURE_SPEECH_KEY')
                azure_speech_region = os.environ.get('AZURE_SPEECH_REGION', 'eastus')

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
                    return False, error

                # Create thread-safe config container
                self.config_container = AIAgentConfigContainer.from_db_config(config)
                self.stats['config_loaded'] = True

                self._service_state = "starting"

                # Start service in background thread
                self.thread = threading.Thread(target=self._run_service, daemon=True)
                self.thread.start()

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

                # Close SSH
                if self.ssh:
                    self.ssh.close()
                    self.ssh = None

                # Wait for thread
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
        """Main service loop"""
        import aioari
        from openai import AsyncAzureOpenAI
        import azure.cognitiveservices.speech as speechsdk

        # Get ARI connection details
        ari_url = os.environ.get('ARI_URL', 'http://10.200.200.2:8088')
        ari_user = os.environ.get('ARI_USERNAME', 'asterisk')
        ari_pass = os.environ.get('ARI_PASSWORD')
        ari_app = os.environ.get('ARI_APPLICATION', 'ai-agent')

        if not ari_pass:
            logger.error("❌ ARI_PASSWORD not configured")
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
            logger.info("🧠 Testing Azure OpenAI...")
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

            # Initialize standalone SSH
            logger.info("🔌 Connecting SSH...")
            self.ssh = StandaloneSSH(
                self.config_container.ssh_host,
                self.config_container.ssh_user,
                self.config_container.ssh_password
            )

            if await self.ssh.connect():
                with self._status_lock:
                    self.stats['ssh_connected'] = True
                logger.info("   ✅ SSH: CONNECTED")
            else:
                logger.warning("   ⚠️ SSH: FAILED (TTS may not work)")

            # Test Dataverse
            dataverse_url = os.environ.get('DATAVERSE_URL')
            if dataverse_url:
                logger.info("📊 Testing Dataverse...")
                try:
                    token = await get_dataverse_token()
                    if token:
                        with self._status_lock:
                            self.stats['dataverse_connected'] = True
                        logger.info("   ✅ Dataverse: CONNECTED")
                    else:
                        logger.warning("   ⚠️ Dataverse: AUTH FAILED (optional)")
                except Exception as e:
                    logger.warning(f"   ⚠️ Dataverse: ERROR - {e} (optional)")

            # Pre-cache TTS
            logger.info("📊 Pre-caching TTS phrases...")
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
                    await cache.get(phrase, self.ssh)
                    cached_count += 1
                except Exception as e:
                    logger.warning(f"   ⚠️ Cache warning: {e}")

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

            with self._status_lock:
                self._service_state = "running"

            logger.info("=" * 80)
            logger.info("🎙️ AI AGENT READY - WAITING FOR CALLS...")
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
        """Handle incoming call"""
        try:
            channel = await ari_client.channels.get(channelId=channel_id)
            caller_number = event.get('channel', {}).get('caller', {}).get('number', 'Unknown')

            with self._status_lock:
                self.active_calls[channel_id] = {
                    'start_time': datetime.utcnow(),
                    'caller_number': caller_number,
                    'channel': channel
                }

            logger.info(f"🎯 Processing call from {caller_number}...")

            # Use standalone SSH that doesn't need Flask context
            await handle_call(channel, ari_client, self.config_container, caller_number, self.ssh, self.app)

            logger.info(f"✅ Call from {caller_number} completed")

        except Exception as e:
            logger.error(f"❌ CALL HANDLING ERROR: {e}")
            logger.error(traceback.format_exc())
            with self._status_lock:
                self.stats['last_error'] = f"Call handling: {str(e)}"
        finally:
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
        """Get comprehensive service status"""
        with self._status_lock:
            is_running = self._service_state == "running"

            uptime = None
            if self.stats['start_time'] and is_running:
                uptime = (datetime.utcnow() - self.stats['start_time']).seconds

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
                'service_state': self._service_state,
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
    """AI Agent configuration page"""
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

        # Update fields
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
    """Get AI agent service status"""
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

        # Filter by date range
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if start_date:
            query = query.filter(AIAgentCallLog.created_at >= start_date)
        if end_date:
            query = query.filter(AIAgentCallLog.created_at <= end_date)

        # Filter by caller
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
    """Get detailed call information"""
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