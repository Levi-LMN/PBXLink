import asyncio, aioari, os, tempfile, time, requests, pyttsx3, logging, paramiko, hashlib, json, signal, sys
import threading
from pydub import AudioSegment
from pydub.effects import normalize
from pathlib import Path
from openai import AsyncAzureOpenAI
import azure.cognitiveservices.speech as speechsdk
from msal import ConfidentialClientApplication
from dotenv import load_dotenv
from urllib.parse import quote

load_dotenv()


class ColoredFormatter(logging.Formatter):
    COLORS = {'DEBUG': '\033[36m', 'INFO': '\033[32m', 'WARNING': '\033[33m', 'ERROR': '\033[31m',
              'CRITICAL': '\033[35m', 'RESET': '\033[0m'}

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        record.levelname = f"{log_color}{record.levelname}{self.COLORS['RESET']}"
        return super().format(record)


handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)

# Configuration
ARI_URL, ARI_BASE = "http://10.200.200.2:8088/ari", "http://10.200.200.2:8088"
USERNAME, PASSWORD, APPLICATION = "asterisk", "cb7c18cce4839d9533ce845f7586b62a", "ai-agent"
SSH_HOST, SSH_USER, SSH_PASSWORD = "10.200.200.2", "sangoma", "sangoma"
ASTERISK_SOUNDS_DIR = "/var/lib/asterisk/sounds/custom"

AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus")

TENANT_ID = os.environ.get("TENANT_ID")
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
DATAVERSE_URL = os.environ.get("DATAVERSE_URL")

TRANSFER_CONFIG = {
    "sales": {"endpoint": "PJSIP/1000", "context": "from-internal", "extension": "1000"},
    "support": {"endpoint": "PJSIP/1001", "context": "from-internal", "extension": "1001"},
    "billing": {"endpoint": "PJSIP/1002", "context": "from-internal", "extension": "1002"},
    "technical": {"endpoint": "PJSIP/1003", "context": "from-internal", "extension": "1003"}
}

CACHE_DIR = Path.home() / ".asterisk_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_INDEX_FILE = CACHE_DIR / "cache_index.json"

# OPTIMIZED TIMING CONSTANTS
BEEP_DELAY_AFTER_SPEAK = 0.1
BEEP_PAUSE_AFTER = 0.15

# Heartbeat configuration
HEARTBEAT_FILE = os.environ.get('AI_AGENT_HEARTBEAT_FILE')
heartbeat_stop_event = threading.Event()
shutdown_flag = asyncio.Event()
active_calls = set()
total_calls_processed = 0
dataverse_token = None
dataverse_token_expiry = 0

SYSTEM_PROMPT = """You are a professional phone assistant for Sibasi Limited.

RULES:
- Keep responses 15-35 words (phone call!)
- Use customer data naturally when available
- Never say "I'm an AI"

FUNCTIONS - Use when needed:
- end_call: User says goodbye/thanks/done
- transfer_to_agent: Needs human help (system announces transfer)
- create_ticket: Issue reported or support needed
- schedule_callback: User wants callback

After function executes, respond naturally:
- Tickets: "Created ticket [ID]. Team responds in 24 hours."
- Transfers: Don't respond - system handles it
- Callbacks: "Scheduled for [time]."

COMPANY: Sibasi Limited - Business consulting, tech solutions
HOURS: Mon-Fri 9 AM - 5 PM EAT"""

AI_TOOLS = [
    {"type": "function", "function": {"name": "end_call", "description": "End call when done",
                                      "parameters": {"type": "object", "properties": {"reason": {"type": "string"}},
                                                     "required": ["reason"]}}},
    {"type": "function", "function": {"name": "transfer_to_agent", "description": "Transfer to human agent",
                                      "parameters": {"type": "object", "properties": {"department": {"type": "string",
                                                                                                     "enum": ["sales",
                                                                                                              "support",
                                                                                                              "billing",
                                                                                                              "technical"]},
                                                                                      "reason": {"type": "string"}},
                                                     "required": ["department", "reason"]}}},
    {"type": "function", "function": {"name": "create_ticket", "description": "Create support ticket",
                                      "parameters": {"type": "object", "properties": {"subject": {"type": "string"},
                                                                                      "priority": {"type": "string",
                                                                                                   "enum": ["low",
                                                                                                            "medium",
                                                                                                            "high",
                                                                                                            "urgent"]},
                                                                                      "description": {
                                                                                          "type": "string"}},
                                                     "required": ["subject", "priority", "description"]}}},
    {"type": "function", "function": {"name": "schedule_callback", "description": "Schedule callback",
                                      "parameters": {"type": "object",
                                                     "properties": {"preferred_time": {"type": "string"},
                                                                    "reason": {"type": "string"}},
                                                     "required": ["preferred_time", "reason"]}}}
]


def heartbeat_writer():
    """Write heartbeat file every 5 seconds to prove we're alive"""
    if not HEARTBEAT_FILE:
        logger.debug("No heartbeat file configured")
        return

    logger.info(f"💓 Heartbeat writer started: {HEARTBEAT_FILE}")

    while not heartbeat_stop_event.is_set():
        try:
            heartbeat_data = {
                'timestamp': time.time(),
                'pid': os.getpid(),
                'active_calls': len(active_calls),
                'total_calls': total_calls_processed
            }

            # Write atomically
            temp_file = f"{HEARTBEAT_FILE}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(heartbeat_data, f)

            # Atomic rename
            os.replace(temp_file, HEARTBEAT_FILE)

        except Exception as e:
            logger.error(f"Heartbeat write failed: {e}")

        # Sleep for 5 seconds
        heartbeat_stop_event.wait(5)

    logger.info("💓 Heartbeat writer stopped")


def normalize_phone(phone):
    """Normalize Kenyan phone to +254 format"""
    if not phone:
        return None
    cleaned = ''.join(c for c in phone if c.isdigit() or c == '+')
    if cleaned.startswith('+'): cleaned = cleaned[1:]

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
    if dataverse_token and time.time() < (dataverse_token_expiry - 300):
        return dataverse_token

    try:
        app = ConfidentialClientApplication(CLIENT_ID, authority=f"https://login.microsoftonline.com/{TENANT_ID}",
                                            client_credential=CLIENT_SECRET)
        loop = asyncio.get_running_loop()
        token_response = await loop.run_in_executor(None, lambda: app.acquire_token_for_client(
            scopes=[f"{DATAVERSE_URL}/.default"]))

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
    """Fetch claims from Dataverse - optimized query"""
    token = await get_dataverse_token()
    if not token:
        return None

    normalized = normalize_phone(phone)
    variants = list(set([phone, normalized, normalized[1:] if normalized.startswith('+') else None,
                         '0' + normalized[4:] if normalized and normalized.startswith('+254') else None,
                         normalized[4:] if normalized and normalized.startswith('+254') else None]))
    variants = [v for v in variants if v]

    logger.info(f"📊 Searching: {normalized} with {len(variants)} variants")

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "Prefer": "odata.include-annotations=*"}
    columns = "cra47_testclaimid,cra47_claimnumber,cra47_claimstatus,cra47_estimatedresolution,cra47_amountapproved,cra47_useremail,cra47_username,cra47_newcolumn,cra47_userphone"

    filter_parts = [f"cra47_userphone eq '{v.replace(chr(39), chr(39) + chr(39))}'" for v in variants]
    filter_query = " or ".join(filter_parts)
    url = f"{DATAVERSE_URL}/api/data/v9.2/cra47_testclaims?$select={columns}&$filter={quote(filter_query)}"

    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, lambda: requests.get(url, headers=headers, timeout=10))

    if not response.ok:
        logger.error(f"❌ Query failed: {response.status_code}")
        all_url = f"{DATAVERSE_URL}/api/data/v9.2/cra47_testclaims?$select={columns}"
        all_resp = await loop.run_in_executor(None, lambda: requests.get(all_url, headers=headers, timeout=10))
        if all_resp.ok:
            claims_data = [c for c in all_resp.json().get("value", []) if c.get('cra47_userphone') in variants]
        else:
            return None
    else:
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


async def store_function_call(function_name, parameters, result, phone, status="completed"):
    """Store function call in Dataverse"""
    token = await get_dataverse_token()
    if not token:
        return False

    record_data = {
        "cra47_name": function_name,
        "cra47_description": f"AI: {function_name}",
        "cra47_parameters": json.dumps(parameters),
        "cra47_status": status,
        "cra47_result": json.dumps(result),
        "cra47_phone": phone
    }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{DATAVERSE_URL}/api/data/v9.2/cra47_testtickets"

    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None,
                                          lambda: requests.post(url, headers=headers, json=record_data, timeout=10))

    if response.ok:
        logger.info(f"✅ Stored: {function_name}")
        return True
    logger.error(f"❌ Store failed: {response.status_code}")
    return False


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


def build_context(customer_data):
    """Build system context with customer data"""
    context = f"""{SYSTEM_PROMPT}

CUSTOMER:
- Name: {customer_data.get('name')}
- Account: {customer_data.get('account_number')} ({customer_data.get('account_status')})
- Phone: {customer_data.get('phone')}
- Email: {customer_data.get('email')}
"""
    claims = customer_data.get('claims', [])
    if claims:
        context += f"\nCLAIMS ({len(claims)} found):\n"
        for i, c in enumerate(claims, 1):
            context += f"{i}. #{c['claim_id']} - {c['type']} - Status: {c['status']} - Amount: {c['amount']}\n"
        context += "\nProvide claim details when asked."
    else:
        context += "\nNO CLAIMS found. Offer to create ticket if asked about claims.\n"
    return context


async def execute_action(fn, args, customer_data, call_channel, ari_client, call_instance):
    """Execute AI function"""
    phone = customer_data.get('phone') if customer_data else 'Unknown'

    if fn == "create_ticket":
        ticket_id = f"TKT-{int(time.time())}"
        print(
            f"\n{'=' * 70}\n🎫 TICKET CREATED: {ticket_id}\n📌 {args.get('subject')}\n⚠️ {args.get('priority').upper()}\n📝 {args.get('description')}\n{'=' * 70}\n")
        result = {"status": "success", "ticket_id": ticket_id, "estimated_response": "24 hours"}
        await store_function_call("create_ticket", args, result, phone, "completed")
        return result

    elif fn == "transfer_to_agent":
        dept = args.get('department')
        print(f"\n{'=' * 70}\n📞 TRANSFER: {dept.upper()}\n📋 {args.get('reason')}\n{'=' * 70}\n")

        if not call_channel or dept not in TRANSFER_CONFIG:
            result = {"status": "failed", "success": False}
            await store_function_call("transfer_to_agent", args, result, phone, "failed")
            return result

        config = TRANSFER_CONFIG[dept]
        msg = f"Connecting you to {dept} now."

        if call_instance:
            try:
                await call_instance.speak(msg)
            except:
                pass

        try:
            await call_channel.continueInDialplan(context=config['context'], extension=config['extension'], priority=1)
            result = {"status": "transferred", "department": dept, "success": True}
            await store_function_call("transfer_to_agent", args, result, phone, "completed")
            return result
        except Exception as e:
            logger.error(f"❌ Transfer failed: {e}")
            result = {"status": "failed", "error": str(e), "success": False}
            await store_function_call("transfer_to_agent", args, result, phone, "failed")
            return result

    elif fn == "schedule_callback":
        cb_id = f"CB-{int(time.time())}"
        print(
            f"\n{'=' * 70}\n📅 CALLBACK: {cb_id}\n⏰ {args.get('preferred_time')}\n📋 {args.get('reason')}\n{'=' * 70}\n")
        result = {"status": "scheduled", "callback_id": cb_id}
        await store_function_call("schedule_callback", args, result, phone, "completed")
        return result

    elif fn == "end_call":
        result = {"status": "ending", "reason": args.get('reason')}
        await store_function_call("end_call", args, result, phone, "completed")
        return result

    return {"status": "unknown"}


class ConversationContext:
    def __init__(self, customer_data, call_channel, ari_client):
        self.messages = [{"role": "system", "content": build_context(customer_data)}]
        self.customer_data = customer_data
        self.call_channel = call_channel
        self.ari_client = ari_client
        self.action_log = []
        self.last_was_clarification = False

    def add_user(self, t):
        self.messages.append({"role": "user", "content": t})

    def add_assistant(self, t):
        self.messages.append({"role": "assistant", "content": t})

    def add_tool_call(self, tool, args, result):
        self.action_log.append({"tool": tool, "args": args, "result": result, "time": time.time()})


class AIAssistant:
    def __init__(self, client):
        self.client = client

    async def respond(self, ctx, confidence="high", call_instance=None):
        messages = ctx.messages.copy()
        if confidence == "low" and not ctx.last_was_clarification:
            messages.append(
                {"role": "system", "content": "Poor audio. Ask once for clarification, then offer callback."})
            ctx.last_was_clarification = True
        else:
            ctx.last_was_clarification = False

        try:
            r = await self.client.chat.completions.create(model=AZURE_OPENAI_DEPLOYMENT, messages=messages,
                                                          max_tokens=200, temperature=0.9, tools=AI_TOOLS,
                                                          tool_choice="auto")
            msg = r.choices[0].message

            if msg.tool_calls:
                tc = msg.tool_calls[0]
                fn = tc.function.name
                args = json.loads(tc.function.arguments)

                result = await execute_action(fn, args, ctx.customer_data, ctx.call_channel, ctx.ari_client,
                                              call_instance)
                ctx.add_tool_call(fn, args, result)

                messages.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": fn, "arguments": tc.function.arguments}}]})
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": fn, "content": json.dumps(result)})

                r2 = await self.client.chat.completions.create(model=AZURE_OPENAI_DEPLOYMENT, messages=messages,
                                                               max_tokens=200, temperature=0.9)
                resp = r2.choices[0].message.content.strip()

                if fn == "transfer_to_agent" and result.get('success'):
                    return "", fn
                return resp, fn

            return msg.content.strip(), None
        except Exception as e:
            logger.error(f"❌ AI error: {e}")
            return "Technical issue. Repeat that?", None


class AzureSpeechTranscriber:
    def __init__(self):
        self.config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
        self.config.speech_recognition_language = "en-US"

    async def transcribe(self, audio_file):
        try:
            if os.path.getsize(audio_file) < 4000:
                return "", "low"

            processed = await self._preprocess(audio_file)
            audio_config = speechsdk.audio.AudioConfig(filename=processed)
            recognizer = speechsdk.SpeechRecognizer(speech_config=self.config, audio_config=audio_config)

            result = await asyncio.get_running_loop().run_in_executor(None, recognizer.recognize_once)

            text = result.text.strip() if result.reason == speechsdk.ResultReason.RecognizedSpeech else ""
            confidence = "high" if text else "low"

            try:
                if processed != audio_file: os.unlink(processed)
            except:
                pass

            return text, confidence
        except Exception as e:
            logger.error(f"❌ Transcribe error: {e}")
            return "", "low"

    async def _preprocess(self, audio_file):
        try:
            audio = AudioSegment.from_file(audio_file)
            audio = normalize(audio).set_frame_rate(16000).set_channels(1).set_sample_width(2)
            processed = audio_file.replace('.wav', '_proc.wav')
            audio.export(processed, format="wav")
            return processed
        except:
            return audio_file


class SoundCache:
    def __init__(self):
        self.index = json.load(open(CACHE_INDEX_FILE)) if CACHE_INDEX_FILE.exists() else {}

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

    async def get(self, text, ssh):
        k = self._key(text)
        if k in self.index and self.index[k].get('remote'):
            return self.index[k]['remote'], self.index[k].get('duration')

        local = await self._tts(text, k)
        if not local: return None, None

        duration = self._duration(local)
        remote = await ssh.upload(local, f"c_{k}.wav")
        if remote:
            self.index[k] = {'remote': remote, 'duration': duration}
            self._save()

        return (remote or local), duration

    async def _tts(self, text, k):
        try:
            f = CACHE_DIR / f"{k}.wav"
            if f.exists(): return str(f)
            tmp = CACHE_DIR / f"{k}_t.wav"

            try:
                from gtts import gTTS
                await asyncio.get_running_loop().run_in_executor(None,
                                                                 lambda: gTTS(text=text, lang='en', slow=False).save(
                                                                     str(tmp)))
            except:
                await asyncio.get_running_loop().run_in_executor(None, self._pyttsx, text, str(tmp))

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
        try:
            e = pyttsx3.init()
            e.setProperty('rate', 165)
            e.save_to_file(t, o)
            e.runAndWait()
            e.stop()
        except:
            pass


class SSH:
    def __init__(self):
        self.client = self.sftp = None
        self._lock = asyncio.Lock()

    async def connect(self):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            await asyncio.get_running_loop().run_in_executor(None,
                                                             lambda: self.client.connect(SSH_HOST, username=SSH_USER,
                                                                                         password=SSH_PASSWORD,
                                                                                         timeout=10,
                                                                                         look_for_keys=False))
            self.sftp = self.client.open_sftp()
            logger.info("✅ SSH connected")
            return True
        except Exception as e:
            logger.error(f"❌ SSH error: {e}")
            return False

    async def _cmd(self, c):
        try:
            _, o, _ = await asyncio.get_running_loop().run_in_executor(None, lambda: self.client.exec_command(c))
            return o.channel.recv_exit_status() == 0
        except:
            return False

    async def upload(self, local, fn):
        async with self._lock:
            try:
                if not self.sftp and not await self.connect(): return None
                await asyncio.get_running_loop().run_in_executor(None, lambda: self.sftp.put(local, f"/tmp/{fn}"))
                await self._cmd(f"sudo mv /tmp/{fn} {ASTERISK_SOUNDS_DIR}/{fn}")
                await self._cmd(f"sudo chown asterisk:asterisk {ASTERISK_SOUNDS_DIR}/{fn}")
                await self._cmd(f"sudo chmod 644 {ASTERISK_SOUNDS_DIR}/{fn}")
                return f"custom/{fn.replace('.wav', '')}"
            except Exception as e:
                logger.error(f"❌ Upload failed: {e}")
                return None

    def close(self):
        try:
            if self.sftp: self.sftp.close()
            if self.client: self.client.close()
        except:
            pass


class Call:
    def __init__(self, ch, cl, ai, cache, ssh, transcriber, customer_data, ari_client):
        self.ch, self.cl, self.ai, self.cache, self.ssh = ch, cl, ai, cache, ssh
        self.transcriber = transcriber
        self.id, self.active, self.tmp = ch.id, True, []
        self.conv = ConversationContext(customer_data, ch, ari_client)
        self.customer_data = customer_data
        self.should_end = False
        active_calls.add(self)

    async def alive(self):
        if not self.active or shutdown_flag.is_set():
            self.active = False
            return False
        try:
            await self.cl.channels.get(channelId=self.id)
            return True
        except:
            self.active = False
            return False

    async def speak(self, text):
        if not await self.alive(): return False
        try:
            sound, duration = await self.cache.get(text, self.ssh)
            if not sound: return False
            await self.ch.play(media=f"sound:{sound}")
            await asyncio.sleep((duration or len(text.split()) * 0.4) + 0.3)
            return True
        except Exception as e:
            if "404" not in str(e): logger.error(f"❌ Speak error: {e}")
            self.active = False
            return False

    async def record(self, dur=8, sil=2.0):
        if not await self.alive(): return None
        n = f"r_{self.id}_{int(time.time() * 1000)}"
        try:
            rec = await self.ch.record(name=n, format="wav", maxDurationSeconds=dur, maxSilenceSeconds=sil,
                                       ifExists="overwrite", terminateOn="none")
            await asyncio.sleep(dur + 0.5)
            try:
                await rec.stop()
            except:
                pass
            await asyncio.sleep(0.2)
            return await self._download(n)
        except Exception as e:
            logger.error(f"❌ Record error: {e}")
            return None

    async def _download(self, n):
        for _ in range(3):
            try:
                r = requests.get(f"{ARI_URL}/recordings/stored/{n}/file", auth=(USERNAME, PASSWORD), timeout=10)
                if r.status_code == 200 and len(r.content) > 4000:
                    f = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                    f.write(r.content)
                    f.close()
                    self.tmp.append(f.name)
                    return f.name
            except:
                pass
            await asyncio.sleep(0.15)
        return None

    async def cleanup(self):
        for f in self.tmp:
            try:
                os.unlink(f)
            except:
                pass
        active_calls.discard(self)

    async def hangup(self):
        try:
            if self.active: await self.ch.hangup()
        except:
            pass
        self.active = False


async def handle(ch, cl, ai, cache, ssh, transcriber, ari_client):
    """Main call handler with optimized timing"""
    global total_calls_processed

    caller_id = ch.json.get('caller', {}).get('number', 'Unknown')
    customer_data = await fetch_customer_data(caller_id)
    c = Call(ch, cl, ai, cache, ssh, transcriber, customer_data, ari_client)

    total_calls_processed += 1

    try:
        await ch.answer()
        await asyncio.sleep(0.2)

        # Print customer context to terminal
        print(f"\n{'=' * 60}")
        print(f"📞 CALL FROM: {customer_data.get('name')} ({customer_data.get('phone')})")
        print(f"📋 Account: {customer_data.get('account_number')}")
        claims = customer_data.get('claims', [])
        if claims:
            print(f"🎫 Claims: {len(claims)} found")
            for i, claim in enumerate(claims, 1):
                print(f"   {i}. #{claim['claim_id']} - {claim['status']} - {claim['amount']}")
        else:
            print(f"🎫 Claims: None")
        print(f"{'=' * 60}\n")

        # Time-appropriate greeting
        import datetime
        eat_time = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
        hour = eat_time.hour

        if hour < 12:
            time_greeting = 'Good morning'
        elif hour < 17:
            time_greeting = 'Good afternoon'
        else:
            time_greeting = 'Good evening'

        print(f"🕐 EAT Time: {eat_time.strftime('%H:%M')} → Using: {time_greeting}")

        generic = f"{time_greeting}, thank you for calling Sibasi Limited. How can I help you today?"
        if not await c.speak(generic): return
        c.conv.add_assistant(generic)

        await asyncio.sleep(BEEP_DELAY_AFTER_SPEAK)
        await c.ch.play(media="sound:beep")
        await asyncio.sleep(BEEP_PAUSE_AFTER)

        no_speech = consecutive_unclear = 0
        first_interaction = True

        for turn in range(6):
            if not await c.alive() or c.should_end: break

            rec = await c.record()
            await c.ch.play(media="sound:beep")
            await asyncio.sleep(0.1)

            if not rec:
                no_speech += 1
                if no_speech >= 2:
                    await c.speak("Having trouble hearing you. Call back if needed.")
                    break
                await c.speak("Didn't catch that. Go ahead.")
                await asyncio.sleep(BEEP_DELAY_AFTER_SPEAK)
                await c.ch.play(media="sound:beep")
                await asyncio.sleep(BEEP_PAUSE_AFTER)
                continue

            text, confidence = await c.transcriber.transcribe(rec)
            no_speech = 0

            if not text or len(text) < 3:
                consecutive_unclear += 1
                if consecutive_unclear >= 2:
                    await c.speak("Let me have someone call you back.")
                    break
                await c.speak("Could you repeat that?")
                await asyncio.sleep(BEEP_DELAY_AFTER_SPEAK)
                await c.ch.play(media="sound:beep")
                await asyncio.sleep(BEEP_PAUSE_AFTER)
                continue

            consecutive_unclear = 0

            if len(text.split()) <= 4 and any(
                    p in text.lower() for p in ["bye", "goodbye", "thanks", "thank you", "that's all", "done"]):
                await c.speak("Anytime! Bye!")
                break

            if first_interaction and customer_data.get('name') and customer_data['name'] != "Valued Customer":
                name_ack = f"Thanks, {customer_data['name'].split()[0]}."
                c.conv.add_assistant(name_ack)
                first_interaction = False

            c.conv.add_user(text)
            resp, fn = await ai.respond(c.conv, confidence, c)
            c.conv.add_assistant(resp)

            if fn == "transfer_to_agent" and c.conv.action_log and c.conv.action_log[-1].get('result', {}).get(
                    'success'):
                return

            if fn == "end_call":
                c.should_end = True

            if resp and not await c.speak(resp):
                return

            if c.should_end:
                break

            await asyncio.sleep(BEEP_DELAY_AFTER_SPEAK)
            await c.ch.play(media="sound:beep")
            await asyncio.sleep(BEEP_PAUSE_AFTER)

        if not c.should_end:
            await c.speak("Thanks for calling!")

        await c.hangup()

    except Exception as e:
        logger.error(f"❌ Call error: {e}")
        await c.hangup()
    finally:
        await c.cleanup()


async def shutdown_handler(ssh, ari_client=None):
    """Graceful shutdown"""
    shutdown_flag.set()
    heartbeat_stop_event.set()

    # Clean up heartbeat file
    if HEARTBEAT_FILE and os.path.exists(HEARTBEAT_FILE):
        try:
            os.unlink(HEARTBEAT_FILE)
            logger.info("💓 Heartbeat file removed")
        except Exception as e:
            logger.warning(f"Could not remove heartbeat file: {e}")

    for call in list(active_calls):
        try:
            await call.hangup()
        except:
            pass
    if ari_client:
        try:
            await ari_client.close()
        except:
            pass
    ssh.close()


async def main():
    print("\n" + "=" * 60)
    print("   🤖 AI Voice Agent - Sibasi Limited")
    print("   💓 WITH HEARTBEAT MONITORING")
    print("   📞 Production Ready")
    print("=" * 60 + "\n")

    if not all([AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_SPEECH_KEY]):
        logger.error("❌ Missing Azure credentials")
        return

    if DATAVERSE_URL and TENANT_ID and CLIENT_ID and CLIENT_SECRET:
        token = await get_dataverse_token()
        if token:
            logger.info("✅ Dataverse connected")
        else:
            logger.warning("⚠️ Dataverse auth failed")

    client = AsyncAzureOpenAI(api_key=AZURE_OPENAI_API_KEY, api_version="2024-08-01-preview",
                              azure_endpoint=AZURE_OPENAI_ENDPOINT)

    try:
        await client.chat.completions.create(model=AZURE_OPENAI_DEPLOYMENT,
                                             messages=[{"role": "user", "content": "Hi"}], max_tokens=5)
        logger.info("✅ AI ready")
    except Exception as e:
        logger.error(f"❌ AI failed: {e}")
        return

    transcriber = AzureSpeechTranscriber()
    ai, cache, ssh = AIAssistant(client), SoundCache(), SSH()

    if not await ssh.connect(): return

    # START HEARTBEAT WRITER
    if HEARTBEAT_FILE:
        heartbeat_thread = threading.Thread(target=heartbeat_writer, daemon=True)
        heartbeat_thread.start()
        logger.info(f"✅ Heartbeat thread started → {HEARTBEAT_FILE}")
    else:
        logger.warning("⚠️ No heartbeat file configured (AI_AGENT_HEARTBEAT_FILE not set)")

    # Cache common phrases
    phrases = [
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

    for p in phrases:
        await cache.get(p, ssh)

    logger.info(f"✅ Cached {len(phrases)} phrases")
    logger.info(f"⚡ Timing optimized: Beep delay {BEEP_DELAY_AFTER_SPEAK}s, Pause {BEEP_PAUSE_AFTER}s")

    ari_client = None
    loop = asyncio.get_running_loop()

    def signal_handler():
        asyncio.create_task(shutdown_handler(ssh, ari_client))
        loop.call_later(5, lambda: os._exit(0))

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
    except:
        pass

    try:
        ari_client = await aioari.connect(ARI_BASE, USERNAME, PASSWORD)
        logger.info("✅ ARI connected")

        ari_client.on_event("StasisStart",
                            lambda e: asyncio.create_task(wrap(e, ari_client, ai, cache, ssh, transcriber)))

        print("\n" + "=" * 60)
        print("   🎙️ SYSTEM READY")
        print("   💓 Heartbeat active")
        print("   Press Ctrl+C to shutdown")
        print("=" * 60 + "\n")

        await ari_client.run(apps=APPLICATION)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"❌ System error: {e}")
    finally:
        await shutdown_handler(ssh, ari_client)
        os._exit(0)


async def wrap(e, ari_cl, ai, cache, ssh, transcriber):
    """Call wrapper"""
    cid = e.get("channel", {}).get("id")
    if not cid: return
    try:
        ch = await ari_cl.channels.get(channelId=cid)
        await handle(ch, ari_cl, ai, cache, ssh, transcriber, ari_cl)
    except Exception as ex:
        logger.error(f"❌ Wrapper error: {ex}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    finally:
        sys.exit(0)