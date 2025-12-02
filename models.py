"""
Database models for FreePBX Dashboard
Tracks users, audit logs, and AI agent call logs
"""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import enum

db = SQLAlchemy()


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


class AIAgentCallLog(db.Model):
    """Log of AI agent call interactions"""
    __tablename__ = 'ai_agent_call_logs'

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
        return f'<AIAgentCallLog {self.call_id} from {self.caller_number}>'

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


def init_db(app):
    """Initialize database"""
    db.init_app(app)

    with app.app_context():
        db.create_all()
        print("Database tables created successfully")