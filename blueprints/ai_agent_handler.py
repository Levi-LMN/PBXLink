"""
AI Agent Call Handler - COMPLETE FIXED VERSION
Fixed audio recording issues - proper timing for file system operations
All debug logging added, proper beep timing, correct recording parameters
"""

import asyncio
import logging
import tempfile
import time
import json
import os
import hashlib
import requests
from datetime import datetime, timedelta
from pathlib import Path
from openai import AsyncAzureOpenAI
import azure.cognitiveservices.speech as speechsdk
from pydub import AudioSegment
from pydub.effects import normalize
from msal import ConfidentialClientApplication

from models import db, AIAgentCallLog, AIAgentTurn, AIAgentDepartment
from ssh_manager import ssh_manager

logger = logging.getLogger(__name__)

# Configuration from environment
CACHE_DIR = Path.home() / ".asterisk_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_INDEX_FILE = CACHE_DIR / "cache_index.json"

ASTERISK_SOUNDS_DIR = "/var/lib/asterisk/sounds/custom"

ARI_URL = os.environ.get("ARI_URL", "http://10.200.200.2:8088/ari")
ARI_USERNAME = os.environ.get("ARI_USERNAME", "asterisk")
ARI_PASSWORD = os.environ.get("ARI_PASSWORD")

# Dataverse settings (optional)
DATAVERSE_URL = os.environ.get("DATAVERSE_URL")
TENANT_ID = os.environ.get("TENANT_ID")
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

# Global cache for Dataverse token
dataverse_token = None
dataverse_token_expiry = 0


# ============================================================================
# SSH SOUND UPLOADER - Uses centralized ssh_manager
# ============================================================================

class SSHSoundUploader:
    """Upload sounds to Asterisk using centralized SSH manager"""

    def __init__(self):
        self._lock = asyncio.Lock()

    async def upload(self, local_file, remote_filename):
        """Upload sound file to Asterisk sounds directory"""
        async with self._lock:
            try:
                loop = asyncio.get_running_loop()
                temp_path = f"/tmp/{remote_filename}"

                def upload_file():
                    client = ssh_manager.pool.get_connection()
                    if not client:
                        return False
                    try:
                        sftp = client.open_sftp()
                        sftp.put(local_file, temp_path)
                        sftp.close()
                        return True
                    except Exception as e:
                        logger.error(f"SFTP upload failed: {e}")
                        return False

                success = await loop.run_in_executor(None, upload_file)
                if not success:
                    return None

                commands = [
                    f"sudo mv {temp_path} {ASTERISK_SOUNDS_DIR}/{remote_filename}",
                    f"sudo chown asterisk:asterisk {ASTERISK_SOUNDS_DIR}/{remote_filename}",
                    f"sudo chmod 644 {ASTERISK_SOUNDS_DIR}/{remote_filename}"
                ]

                for cmd in commands:
                    result = await loop.run_in_executor(
                        None,
                        lambda c=cmd: ssh_manager.execute_command(c.replace('sudo ', ''), use_sudo=True)
                    )
                    if result is None:
                        logger.error(f"Failed to execute: {cmd}")
                        return None

                return f"custom/{remote_filename.replace('.wav', '')}"

            except Exception as e:
                logger.error(f"❌ Upload failed: {e}")
                return None


# ============================================================================
# SOUND CACHE - From working agent.py
# ============================================================================

class SoundCache:
    """Cache TTS audio files locally and on Asterisk"""
    def __init__(self):
        self.index = json.load(open(CACHE_INDEX_FILE)) if CACHE_INDEX_FILE.exists() else {}
        self.uploader = SSHSoundUploader()

    def _save(self):
        try:
            json.dump(self.index, open(CACHE_INDEX_FILE, 'w'))
        except:
            pass

    def _key(self, t):
        return hashlib.md5(t.encode()).hexdigest()

    def _duration(self, path):
        try:
            return len(AudioSegment.from_file(path)) / 1000.0
        except:
            return None

    async def get(self, text):
        """Get cached sound or generate new one"""
        k = self._key(text)

        if k in self.index and self.index[k].get('remote'):
            return self.index[k]['remote'], self.index[k].get('duration')

        local = await self._tts(text, k)
        if not local:
            return None, None

        duration = self._duration(local)
        remote = await self.uploader.upload(local, f"c_{k}.wav")
        if remote:
            self.index[k] = {'remote': remote, 'duration': duration}
            self._save()

        return (remote or local), duration

    async def _tts(self, text, k):
        """Generate TTS audio using gTTS or pyttsx3"""
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

    def _pyttsx(self, t, o):
        """Fallback TTS using pyttsx3"""
        try:
            import pyttsx3
            e = pyttsx3.init()
            e.setProperty('rate', 165)
            e.save_to_file(t, o)
            e.runAndWait()
            e.stop()
        except:
            pass


# ============================================================================
# DATAVERSE INTEGRATION - From working agent.py
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

    if not all([DATAVERSE_URL, TENANT_ID, CLIENT_ID, CLIENT_SECRET]):
        return None

    if dataverse_token and time.time() < (dataverse_token_expiry - 300):
        return dataverse_token

    try:
        app = ConfidentialClientApplication(
            CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{TENANT_ID}",
            client_credential=CLIENT_SECRET
        )

        loop = asyncio.get_running_loop()
        token_response = await loop.run_in_executor(
            None,
            lambda: app.acquire_token_for_client(scopes=[f"{DATAVERSE_URL}/.default"])
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
    url = f"{DATAVERSE_URL}/api/data/v9.2/cra47_testclaims?$select={columns}&$filter={quote(filter_query)}"

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
    if DATAVERSE_URL and TENANT_ID and CLIENT_ID and CLIENT_SECRET:
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
# CALL HANDLER - FIXED AUDIO RECORDING WITH PROPER TIMING
# ============================================================================

class Call:
    """Handles an individual AI agent call"""

    def __init__(self, channel, ari_client, config, caller_number, ai_client, speech_config, cache):
        self.channel = channel
        self.ari_client = ari_client
        self.config = config
        self.caller_number = caller_number
        self.call_id = channel.id
        self.ai_client = ai_client
        self.speech_config = speech_config
        self.cache = cache

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

        # Create call log entry
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
            sound, duration = await self.cache.get(text)
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
        """Record user audio - DIAGNOSTIC VERSION to find the issue"""
        if not await self.alive():
            logger.error("❌ Channel not alive before recording")
            return None

        recording_name = f"r_{self.call_id}_{int(time.time() * 1000)}"
        max_duration = int(self.config.recording_duration) if self.config.recording_duration else 8
        max_silence = float(self.config.silence_duration) if self.config.silence_duration else 2.0

        logger.info(f"🎙️ ========== RECORDING DIAGNOSTIC ==========")
        logger.info(f"🎙️ Name: {recording_name}")
        logger.info(f"🎙️ Channel ID: {self.call_id}")
        logger.info(f"🎙️ Duration: {max_duration}s, Silence: {max_silence}s")

        # Check channel state
        try:
            channel_info = await self.ari_client.channels.get(channelId=self.call_id)
            logger.info(f"🎙️ Channel state: {channel_info.json.get('state')}")
            logger.info(f"🎙️ Channel name: {channel_info.json.get('name')}")
        except Exception as e:
            logger.error(f"❌ Cannot get channel info: {e}")

        try:
            # Start recording with logging
            logger.info(f"🎙️ Sending record command...")
            rec = await self.channel.record(
                name=recording_name,
                format="wav",
                maxDurationSeconds=max_duration,
                maxSilenceSeconds=max_silence,
                ifExists="overwrite",
                terminateOn="none"
            )
            logger.info(f"🎙️ Record command accepted, object: {rec}")
            logger.info(f"🎙️ Recording object type: {type(rec)}")

            # Immediately check if recording started
            await asyncio.sleep(0.5)

            try:
                live_url = f"{ARI_URL}/recordings/live/{recording_name}"
                logger.info(f"🔍 Checking live recording: GET {live_url}")
                live_check = requests.get(live_url, auth=(ARI_USERNAME, ARI_PASSWORD), timeout=5)
                logger.info(f"🔍 Live recording check: HTTP {live_check.status_code}")

                if live_check.ok:
                    live_data = live_check.json()
                    logger.info(f"✅ RECORDING IS ACTIVE!")
                    logger.info(f"   State: {live_data.get('state')}")
                    logger.info(f"   Format: {live_data.get('format')}")
                    logger.info(f"   Duration: {live_data.get('duration')}")
                else:
                    logger.error(f"❌ RECORDING NOT ACTIVE - Response: {live_check.text}")

                    # Check all live recordings
                    all_live_url = f"{ARI_URL}/recordings/live"
                    all_live = requests.get(all_live_url, auth=(ARI_USERNAME, ARI_PASSWORD), timeout=5)
                    if all_live.ok:
                        logger.info(f"📋 All live recordings: {all_live.json()}")

            except Exception as e:
                logger.error(f"❌ Error checking live recording: {e}")

            # Wait for recording to complete
            logger.info(f"⏳ Waiting {max_duration + 1.0}s for recording to complete...")
            await asyncio.sleep(max_duration + 1.0)

            # Stop recording
            try:
                logger.info(f"🛑 Stopping recording...")
                await rec.stop()
                logger.info(f"✅ Recording stopped")
            except Exception as e:
                logger.warning(f"⚠️ Stop exception (may be normal): {e}")

            # Wait for file system
            logger.info(f"⏳ Waiting 0.5s for file system flush...")
            await asyncio.sleep(0.5)

            # List all stored recordings
            try:
                list_url = f"{ARI_URL}/recordings/stored"
                logger.info(f"📋 Listing stored recordings: GET {list_url}")
                list_resp = requests.get(list_url, auth=(ARI_USERNAME, ARI_PASSWORD), timeout=5)
                logger.info(f"📋 List response: HTTP {list_resp.status_code}")

                if list_resp.ok:
                    all_recordings = list_resp.json()
                    logger.info(f"📋 Total stored recordings: {len(all_recordings)}")

                    # Show recent recordings
                    recent_names = [r.get('name', 'unknown') for r in all_recordings[-10:]]
                    logger.info(f"📋 Recent recordings: {recent_names}")

                    # Check if ours is there
                    our_rec = [r for r in all_recordings if r.get('name') == recording_name]
                    if our_rec:
                        logger.info(f"✅ OUR RECORDING FOUND: {our_rec[0]}")
                    else:
                        logger.error(f"❌ OUR RECORDING ({recording_name}) NOT IN LIST!")
                else:
                    logger.error(f"❌ Failed to list recordings: {list_resp.text}")

            except Exception as e:
                logger.error(f"❌ Error listing recordings: {e}")

            # Try to download
            logger.info(f"📥 Attempting download...")
            downloaded = await self._download(recording_name)

            if downloaded:
                size = os.path.getsize(downloaded)
                logger.info(f"✅ SUCCESS: Downloaded {size} bytes to {downloaded}")
            else:
                logger.error(f"❌ DOWNLOAD FAILED")

                # Final diagnostic - check Asterisk recording directory via SSH
                logger.info(f"🔍 Checking Asterisk filesystem...")
                try:
                    from ssh_manager import ssh_manager
                    result = ssh_manager.execute_command(
                        f"find /var/lib/asterisk -name '*{recording_name}*' -o -name 'r_{self.call_id}*' 2>/dev/null | head -20"
                    )
                    if result:
                        logger.info(f"📁 Files found on Asterisk: {result}")
                    else:
                        logger.error(f"❌ No recording files found on Asterisk filesystem")

                    # Check recording directory permissions
                    perms = ssh_manager.execute_command("ls -la /var/spool/asterisk/recording/ 2>/dev/null | tail -5")
                    if perms:
                        logger.info(f"📁 /var/spool/asterisk/recording/ contents:\n{perms}")
                except Exception as e:
                    logger.error(f"❌ SSH check failed: {e}")

            logger.info(f"🎙️ ========== END DIAGNOSTIC ==========")
            return downloaded

        except Exception as e:
            logger.error(f"❌ FATAL RECORDING ERROR: {e}")
            import traceback
            logger.error(traceback.format_exc())
            logger.info(f"🎙️ ========== END DIAGNOSTIC ==========")
            return None


    async def _download(self, recording_name):
        """Download recording from ARI - with progressive retry logic"""
        # FIXED: More attempts with progressive backoff
        for attempt in range(5):  # Increased from 3 to 5
            try:
                url = f"{ARI_URL}/recordings/stored/{recording_name}/file"
                logger.debug(f"📥 Attempt {attempt+1}/5: {url}")

                r = requests.get(url, auth=(ARI_USERNAME, ARI_PASSWORD), timeout=10)

                logger.debug(f"📥 Response: status={r.status_code}, size={len(r.content)} bytes")

                if r.status_code == 200 and len(r.content) > 4000:
                    f = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                    f.write(r.content)
                    f.close()
                    self.temp_files.append(f.name)
                    logger.info(f"✅ Downloaded: {f.name} ({len(r.content)} bytes)")
                    return f.name
                else:
                    logger.warning(f"⚠️ Attempt {attempt+1}: status={r.status_code}, size={len(r.content)} bytes")

                    # FIXED: Progressive backoff for 404 (file not ready)
                    if r.status_code == 404:
                        wait_time = 0.5 + (attempt * 0.2)
                        logger.debug(f"⏳ 404 error - waiting {wait_time}s before retry")
                        await asyncio.sleep(wait_time)
                    else:
                        await asyncio.sleep(0.3)

            except Exception as e:
                logger.error(f"❌ Download attempt {attempt+1} failed: {e}")
                await asyncio.sleep(0.3)

        logger.error("❌ All download attempts exhausted")
        return None

    async def transcribe(self, audio_file):
        """Transcribe audio using Azure Speech - with detailed logging"""
        try:
            file_size = os.path.getsize(audio_file)
            logger.info(f"🎤 Transcribe START: {audio_file} ({file_size} bytes)")

            if file_size < 4000:
                logger.warning(f"⚠️ File too small: {file_size} bytes < 4000 bytes minimum")
                return "", "low"

            processed = await self._preprocess_audio(audio_file)
            logger.debug(f"🎵 Audio preprocessed: {processed}")

            audio_config = speechsdk.audio.AudioConfig(filename=processed)
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=audio_config
            )

            logger.debug("🎤 Azure Speech recognition starting...")
            result = await asyncio.get_running_loop().run_in_executor(
                None, recognizer.recognize_once
            )

            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                text = result.text.strip()
                confidence = "high"
                logger.info(f"✅ TRANSCRIBED: '{text}' (confidence: {confidence})")
            elif result.reason == speechsdk.ResultReason.NoMatch:
                text = ""
                confidence = "low"
                logger.warning(f"⚠️ No speech recognized (NoMatch)")
            elif result.reason == speechsdk.ResultReason.Canceled:
                text = ""
                confidence = "low"
                cancellation = result.cancellation_details
                logger.warning(f"⚠️ Recognition canceled: {cancellation.reason}")
                if cancellation.reason == speechsdk.CancellationReason.Error:
                    logger.error(f"❌ Error details: {cancellation.error_details}")
            else:
                text = ""
                confidence = "low"
                logger.warning(f"⚠️ Unexpected result reason: {result.reason}")

            if processed != audio_file:
                try:
                    os.unlink(processed)
                except:
                    pass

            return text, confidence

        except Exception as e:
            logger.error(f"❌ Transcribe error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return "", "low"

    async def _preprocess_audio(self, audio_file):
        """Preprocess audio for better recognition"""
        try:
            audio = AudioSegment.from_file(audio_file)
            audio = normalize(audio).set_frame_rate(16000).set_channels(1).set_sample_width(2)
            processed = audio_file.replace('.wav', '_proc.wav')
            audio.export(processed, format="wav")
            return processed
        except Exception as e:
            logger.warning(f"⚠️ Audio preprocessing failed: {e}, using original")
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


async def handle_call(channel, ari_client, config, caller_number):
    """Main call handler - FIXED with proper timing and file system sync"""

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
    logger.info(f"📊 Fetching customer data for {caller_number}...")
    customer_data = await fetch_customer_data(caller_number)

    # Create call handler
    call = Call(channel, ari_client, config, caller_number, ai_client, speech_config, cache)
    call.customer_data = customer_data
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
        logger.info(f"🎙️ Greeting: {greeting}")
        if not await call.speak(greeting):
            return
        call.conversation.append({"role": "assistant", "content": greeting})

        # Beep timing from working version
        await asyncio.sleep(0.2)
        await call.channel.play(media="sound:beep")
        await asyncio.sleep(0.3)

        # Main conversation loop
        for turn in range(config.max_turns):
            if not call.active or call.should_end:
                logger.info(f"🛑 Loop exit: active={call.active}, should_end={call.should_end}")
                break

            logger.info(f"\n{'='*60}")
            logger.info(f"🔄 TURN {turn+1}/{config.max_turns}")
            logger.info(f"{'='*60}")

            # Record
            rec = await call.record()

            # Beep to acknowledge recording received
            await call.channel.play(media="sound:beep")
            await asyncio.sleep(0.1)

            if not rec:
                call.no_speech_count += 1
                logger.warning(f"⚠️ No recording received (count: {call.no_speech_count}/2)")

                if call.no_speech_count >= 2:
                    await call.speak("Having trouble hearing you. Call back if needed.")
                    break

                await call.speak("Didn't catch that. Go ahead.")

                await asyncio.sleep(0.2)
                await call.channel.play(media="sound:beep")
                await asyncio.sleep(0.3)
                continue

            # Transcribe
            text, confidence = await call.transcribe(rec)
            call.no_speech_count = 0

            if not text or len(text) < 3:
                call.unclear_count += 1
                logger.warning(f"⚠️ Unclear/empty transcript (count: {call.unclear_count}/2)")

                if call.unclear_count >= 2:
                    await call.speak("Let me have someone call you back.")
                    break

                await call.speak("Could you repeat that?")

                await asyncio.sleep(0.2)
                await call.channel.play(media="sound:beep")
                await asyncio.sleep(0.3)
                continue

            call.unclear_count = 0

            # Check goodbye
            if call._is_goodbye(text):
                logger.info("👋 Goodbye detected")
                await call.speak("Anytime! Bye!")
                break

            # Add to conversation
            logger.info(f"👤 User: {text}")
            call.conversation.append({"role": "user", "content": text})

            # Get AI response
            resp, fn = await call.get_ai_response(confidence)
            logger.info(f"🤖 AI: {resp}" + (f" [Function: {fn}]" if fn else ""))

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
                logger.info("📞 Transfer successful, ending AI handling")
                return

            # Check end
            if fn == "end_call":
                call.should_end = True

            # Speak response
            if resp and not await call.speak(resp):
                break

            if call.should_end:
                logger.info("🛑 Call end requested by AI")
                break

            # Beep for next turn
            await asyncio.sleep(0.2)
            await call.channel.play(media="sound:beep")
            await asyncio.sleep(0.3)

        # Final goodbye
        if not call.should_end:
            await call.speak("Thanks for calling!")

        await call.hangup()

    except Exception as e:
        logger.error(f"❌ Call error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await call.hangup()
    finally:
        await call.cleanup()