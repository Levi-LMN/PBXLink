"""
Database models for FreePBX Dashboard
Tracks users, audit logs, AI agent call logs, and AI agent configuration
"""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import enum
import json

db = SQLAlchemy()


# ============================================================================
# USER AND AUTHENTICATION MODELS
# ============================================================================

class UserRole(enum.Enum):
    """User roles for access control"""
    ADMIN = 'admin'
    OPERATOR = 'operator'
    VIEWER = 'viewer'


class User(db.Model):
    """User model for authentication and authorization"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255))
    azure_id = db.Column(db.String(255), unique=True)
    role = db.Column(db.Enum(UserRole), default=UserRole.VIEWER, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login = db.Column(db.DateTime)

    # Relationships
    audit_logs = db.relationship('AuditLog', back_populates='user', lazy='dynamic')

    def __repr__(self):
        return f'<User {self.email} ({self.role.value})>'

    def has_permission(self, action):
        """Check if user has permission for an action"""
        permissions = {
            UserRole.ADMIN: ['view', 'edit', 'delete', 'create'],
            UserRole.OPERATOR: ['view', 'edit', 'create'],
            UserRole.VIEWER: ['view']
        }
        return action in permissions.get(self.role, [])

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'role': self.role.value,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }


class AuditLog(db.Model):
    """Audit log for tracking all system changes"""
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    action = db.Column(db.String(50), nullable=False, index=True)
    resource_type = db.Column(db.String(50), nullable=False, index=True)
    resource_id = db.Column(db.String(100))
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    user = db.relationship('User', back_populates='audit_logs')

    def __repr__(self):
        return f'<AuditLog {self.action} on {self.resource_type} by user {self.user_id}>'

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'user_email': self.user.email if self.user else 'Unknown',
            'user_name': self.user.name if self.user else 'Unknown',
            'action': self.action,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
            'details': self.details,
            'ip_address': self.ip_address,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


# ============================================================================
# AI AGENT MODELS - Configuration and Logging
# ============================================================================

class AIAgentConfig(db.Model):
    """AI Agent configuration - user-configurable settings only"""
    __tablename__ = 'ai_agent_config'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    enabled = db.Column(db.Boolean, default=True)

    # System prompt
    system_prompt = db.Column(db.Text, default="""You are a professional phone assistant for Sibasi Limited.

RULES:
- Keep responses 15-35 words (phone call!)
- Use customer data naturally when available
- Never say "I'm an AI"

COMPANY: Sibasi Limited - Business consulting, tech solutions
HOURS: Mon-Fri 9 AM - 5 PM EAT""")

    # Greeting settings
    greeting_template = db.Column(db.Text, default="{time_greeting}, thank you for calling {company_name}. How can I help you today?")
    company_name = db.Column(db.String(200), default="Sibasi Limited")

    # Call settings
    max_turns = db.Column(db.Integer, default=6)
    recording_duration = db.Column(db.Integer, default=8)
    silence_duration = db.Column(db.Float, default=2.0)
    beep_delay = db.Column(db.Float, default=0.1)
    beep_pause = db.Column(db.Float, default=0.15)

    # Recording settings
    store_recordings = db.Column(db.Boolean, default=True)
    store_transcripts = db.Column(db.Boolean, default=True)

    # Azure settings (populated from environment at runtime, not stored)
    azure_endpoint = None
    azure_api_key = None
    azure_deployment = None
    azure_speech_key = None
    azure_speech_region = None

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<AIAgentConfig {self.name} ({"Enabled" if self.enabled else "Disabled"})>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'enabled': self.enabled,
            'system_prompt': self.system_prompt,
            'greeting_template': self.greeting_template,
            'company_name': self.company_name,
            'max_turns': self.max_turns,
            'recording_duration': self.recording_duration,
            'silence_duration': self.silence_duration,
            'store_recordings': self.store_recordings,
            'store_transcripts': self.store_transcripts,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AIAgentDepartment(db.Model):
    """Transfer departments configuration"""
    __tablename__ = 'ai_agent_departments'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)  # sales, support, etc.
    display_name = db.Column(db.String(200), nullable=False)
    extension = db.Column(db.String(20), nullable=False)
    endpoint = db.Column(db.String(100), nullable=False)  # PJSIP/1000
    context = db.Column(db.String(100), default='from-internal')
    enabled = db.Column(db.Boolean, default=True)
    description = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<AIAgentDepartment {self.name} -> {self.extension}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'display_name': self.display_name,
            'extension': self.extension,
            'endpoint': self.endpoint,
            'context': self.context,
            'enabled': self.enabled,
            'description': self.description
        }


class AIAgentCallLog(db.Model):
    """Detailed call logs with recordings and transcripts"""
    __tablename__ = 'ai_agent_call_logs'

    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    caller_number = db.Column(db.String(50), index=True)
    caller_name = db.Column(db.String(200))

    # Call metadata
    call_start = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    call_end = db.Column(db.DateTime)
    call_duration = db.Column(db.Integer)  # seconds

    # Conversation data
    transcript = db.Column(db.Text)  # Full conversation transcript
    summary = db.Column(db.Text)  # AI-generated summary
    intent = db.Column(db.String(200))  # What the caller wanted
    sentiment = db.Column(db.String(50))  # positive, negative, neutral

    # Actions taken
    functions_called = db.Column(db.Text)  # JSON array of function calls
    transferred_to = db.Column(db.String(100))
    tickets_created = db.Column(db.Text)  # JSON array of ticket IDs

    # Customer data used
    customer_data = db.Column(db.Text)  # JSON of customer info

    # Recordings
    recording_path = db.Column(db.String(500))  # Path to full call recording

    # Quality metrics
    turns_count = db.Column(db.Integer, default=0)
    no_speech_count = db.Column(db.Integer, default=0)
    unclear_count = db.Column(db.Integer, default=0)
    avg_confidence = db.Column(db.Float)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<AIAgentCallLog {self.call_id} from {self.caller_number}>'

    def to_dict(self):
        return {
            'id': self.id,
            'call_id': self.call_id,
            'caller_number': self.caller_number,
            'caller_name': self.caller_name,
            'call_start': self.call_start.isoformat() if self.call_start else None,
            'call_end': self.call_end.isoformat() if self.call_end else None,
            'call_duration': self.call_duration,
            'transcript': self.transcript,
            'summary': self.summary,
            'intent': self.intent,
            'sentiment': self.sentiment,
            'functions_called': json.loads(self.functions_called) if self.functions_called else [],
            'transferred_to': self.transferred_to,
            'tickets_created': json.loads(self.tickets_created) if self.tickets_created else [],
            'turns_count': self.turns_count,
            'no_speech_count': self.no_speech_count,
            'unclear_count': self.unclear_count,
            'avg_confidence': self.avg_confidence,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class AIAgentTurn(db.Model):
    """Individual conversation turns for detailed analysis"""
    __tablename__ = 'ai_agent_turns'

    id = db.Column(db.Integer, primary_key=True)
    call_log_id = db.Column(db.Integer, db.ForeignKey('ai_agent_call_logs.id'), nullable=False, index=True)
    turn_number = db.Column(db.Integer, nullable=False)

    # User input
    user_audio_path = db.Column(db.String(500))  # Path to recording
    user_text = db.Column(db.Text)  # Transcribed text
    user_confidence = db.Column(db.String(20))  # high, medium, low

    # AI response
    ai_text = db.Column(db.Text)
    ai_audio_path = db.Column(db.String(500))

    # Function calls in this turn
    function_called = db.Column(db.String(100))
    function_args = db.Column(db.Text)  # JSON
    function_result = db.Column(db.Text)  # JSON

    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<AIAgentTurn {self.turn_number} of call {self.call_log_id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'turn_number': self.turn_number,
            'user_text': self.user_text,
            'user_confidence': self.user_confidence,
            'ai_text': self.ai_text,
            'function_called': self.function_called,
            'function_args': json.loads(self.function_args) if self.function_args else None,
            'function_result': json.loads(self.function_result) if self.function_result else None,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


# ============================================================================
# LEGACY AI AGENT CALL LOG (kept for backwards compatibility)
# ============================================================================

class AIAgentCallLogLegacy(db.Model):
    """Log of AI agent call interactions (legacy model)"""
    __tablename__ = 'ai_agent_call_logs_legacy'

    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    caller_number = db.Column(db.String(50), index=True)
    call_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    call_duration = db.Column(db.Integer)  # in seconds

    # AI Agent details
    intent = db.Column(db.String(100))  # What the caller wanted
    summary = db.Column(db.Text)  # Summary of the conversation
    transcript = db.Column(db.Text)  # Full transcript if available
    sentiment = db.Column(db.String(20))  # positive, negative, neutral

    # Actions taken
    actions_taken = db.Column(db.Text)  # JSON string of actions
    transferred_to = db.Column(db.String(50))  # Extension if transferred

    # Metadata
    confidence_score = db.Column(db.Float)  # AI confidence in understanding
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<AIAgentCallLogLegacy {self.call_id} from {self.caller_number}>'

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'call_id': self.call_id,
            'caller_number': self.caller_number,
            'call_date': self.call_date.isoformat() if self.call_date else None,
            'call_duration': self.call_duration,
            'intent': self.intent,
            'summary': self.summary,
            'sentiment': self.sentiment,
            'actions_taken': self.actions_taken,
            'transferred_to': self.transferred_to,
            'confidence_score': self.confidence_score,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_db(app):
    """Initialize database"""
    db.init_app(app)

    with app.app_context():
        db.create_all()
        print("✅ Database tables created successfully")

        # Create default AI Agent config if it doesn't exist
        if not AIAgentConfig.query.first():
            default_config = AIAgentConfig(
                name='Default Configuration',
                enabled=True
            )
            db.session.add(default_config)
            db.session.commit()
            print("✅ Created default AI Agent configuration")