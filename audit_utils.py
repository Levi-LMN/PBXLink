"""
Audit logging utilities
Provides functions to log user actions throughout the application
"""

from flask import request, session
from models import db, AuditLog, User
import logging
import json

logger = logging.getLogger(__name__)


def log_action(action, resource_type, resource_id=None, details=None):
    """
    Log a user action to the audit log

    Args:
        action: Action performed (e.g., 'create', 'update', 'delete', 'view')
        resource_type: Type of resource (e.g., 'extension', 'wireguard_user', 'config')
        resource_id: ID of the resource (e.g., extension number)
        details: Additional details as dict or string
    """
    try:
        # Get current user
        if 'user' not in session:
            logger.warning("Attempted to log action without authenticated user")
            return

        user = User.query.filter_by(email=session['user']['email']).first()
        if not user:
            logger.warning(f"User not found: {session['user']['email']}")
            return

        # Convert details to JSON string if dict
        if isinstance(details, dict):
            details = json.dumps(details)

        # Create audit log entry
        audit_entry = AuditLog(
            user_id=user.id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            details=details,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent', '')[:500]
        )

        db.session.add(audit_entry)
        db.session.commit()

        logger.info(f"Audit: {user.email} {action} {resource_type} {resource_id or ''}")

    except Exception as e:
        logger.error(f"Error logging audit action: {str(e)}")
        db.session.rollback()


def get_audit_logs(resource_type=None, resource_id=None, user_id=None, limit=100):
    """
    Retrieve audit logs with optional filtering

    Args:
        resource_type: Filter by resource type
        resource_id: Filter by resource ID
        user_id: Filter by user ID
        limit: Maximum number of logs to return

    Returns:
        List of audit log dictionaries
    """
    try:
        query = AuditLog.query

        if resource_type:
            query = query.filter_by(resource_type=resource_type)

        if resource_id:
            query = query.filter_by(resource_id=str(resource_id))

        if user_id:
            query = query.filter_by(user_id=user_id)

        logs = query.order_by(AuditLog.timestamp.desc()).limit(limit).all()
        return [log.to_dict() for log in logs]

    except Exception as e:
        logger.error(f"Error retrieving audit logs: {str(e)}")
        return []


def log_ai_agent_call(call_id, caller_number, intent, summary, **kwargs):
    """
    Log an AI agent call interaction

    Args:
        call_id: Unique call identifier
        caller_number: Caller's phone number
        intent: What the caller wanted
        summary: Summary of the conversation
        **kwargs: Additional fields (transcript, sentiment, actions_taken, etc.)
    """
    from models import AIAgentCallLog

    try:
        call_log = AIAgentCallLog(
            call_id=call_id,
            caller_number=caller_number,
            intent=intent,
            summary=summary,
            transcript=kwargs.get('transcript'),
            sentiment=kwargs.get('sentiment'),
            actions_taken=json.dumps(kwargs.get('actions_taken')) if isinstance(kwargs.get('actions_taken'),
                                                                                dict) else kwargs.get('actions_taken'),
            transferred_to=kwargs.get('transferred_to'),
            call_duration=kwargs.get('call_duration'),
            confidence_score=kwargs.get('confidence_score')
        )

        db.session.add(call_log)
        db.session.commit()

        logger.info(f"Logged AI agent call: {call_id} from {caller_number}")
        return call_log

    except Exception as e:
        logger.error(f"Error logging AI agent call: {str(e)}")
        db.session.rollback()
        return None


def get_ai_agent_call_logs(limit=100, caller_number=None):
    """
    Retrieve AI agent call logs

    Args:
        limit: Maximum number of logs to return
        caller_number: Filter by caller number

    Returns:
        List of call log dictionaries
    """
    from models import AIAgentCallLog

    try:
        query = AIAgentCallLog.query

        if caller_number:
            query = query.filter_by(caller_number=caller_number)

        logs = query.order_by(AIAgentCallLog.call_date.desc()).limit(limit).all()
        return [log.to_dict() for log in logs]

    except Exception as e:
        logger.error(f"Error retrieving AI agent call logs: {str(e)}")
        return []