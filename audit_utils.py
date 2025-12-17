"""
Audit logging with automatic cleanup
audit_utils.py
Provides functions to log user actions and manage log retention
"""

from flask import request, session
from models import db, AuditLog, User
from datetime import datetime, timedelta
import logging
import json
import threading
import time

logger = logging.getLogger(__name__)

# Configuration for log retention
AUDIT_LOG_RETENTION_DAYS = 90  # Keep logs for 90 days
AI_AGENT_LOG_RETENTION_DAYS = 180  # Keep AI agent logs for 180 days
CLEANUP_CHECK_HOURS = 24  # Run cleanup every 24 hours


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
            logger.debug("Attempted to log action without authenticated user")
            return

        user = User.query.filter_by(email=session['user']['email']).first()
        if not user:
            logger.debug(f"User not found: {session['user']['email']}")
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

        logger.debug(f"Audit: {user.email} {action} {resource_type} {resource_id or ''}")

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


def cleanup_old_audit_logs(retention_days=AUDIT_LOG_RETENTION_DAYS):
    """
    Delete audit logs older than retention period

    Args:
        retention_days: Number of days to keep logs (default: 90)

    Returns:
        Number of logs deleted
    """
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)

        # Query old logs
        old_logs = AuditLog.query.filter(AuditLog.timestamp < cutoff_date).all()
        count = len(old_logs)

        if count > 0:
            # Delete in batches to avoid memory issues
            batch_size = 1000
            deleted = 0

            for i in range(0, count, batch_size):
                batch = old_logs[i:i+batch_size]
                for log in batch:
                    db.session.delete(log)

                db.session.commit()
                deleted += len(batch)
                logger.debug(f"Deleted {deleted}/{count} old audit logs")

            logger.info(f"Cleanup complete: Deleted {count} audit logs older than {retention_days} days")
            return count
        else:
            logger.debug(f"No audit logs older than {retention_days} days found")
            return 0

    except Exception as e:
        logger.error(f"Error cleaning up audit logs: {str(e)}")
        db.session.rollback()
        return 0


def cleanup_old_ai_agent_logs(retention_days=AI_AGENT_LOG_RETENTION_DAYS):
    """
    Delete AI agent call logs older than retention period

    Args:
        retention_days: Number of days to keep logs (default: 180)

    Returns:
        Number of logs deleted
    """
    from models import AIAgentCallLog

    try:
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)

        # FIXED: Use call_start instead of call_date
        old_logs = AIAgentCallLog.query.filter(AIAgentCallLog.call_start < cutoff_date).all()
        count = len(old_logs)

        if count > 0:
            # Delete in batches
            batch_size = 1000
            deleted = 0

            for i in range(0, count, batch_size):
                batch = old_logs[i:i+batch_size]
                for log in batch:
                    db.session.delete(log)

                db.session.commit()
                deleted += len(batch)
                logger.debug(f"Deleted {deleted}/{count} old AI agent call logs")

            logger.info(f"Cleanup complete: Deleted {count} AI agent logs older than {retention_days} days")
            return count
        else:
            logger.debug(f"No AI agent logs older than {retention_days} days found")
            return 0

    except Exception as e:
        logger.error(f"Error cleaning up AI agent logs: {str(e)}")
        db.session.rollback()
        return 0


def cleanup_all_logs():
    """
    Run cleanup for all log types

    Returns:
        Dictionary with cleanup statistics
    """
    logger.info("Starting periodic log cleanup...")

    audit_deleted = cleanup_old_audit_logs()
    ai_agent_deleted = cleanup_old_ai_agent_logs()

    stats = {
        'timestamp': datetime.utcnow().isoformat(),
        'audit_logs_deleted': audit_deleted,
        'ai_agent_logs_deleted': ai_agent_deleted,
        'total_deleted': audit_deleted + ai_agent_deleted
    }

    logger.info(f"Log cleanup complete: {stats}")
    return stats


def get_log_statistics():
    """
    Get statistics about current log storage

    Returns:
        Dictionary with log counts and oldest entries
    """
    from models import AIAgentCallLog

    try:
        stats = {
            'audit_logs': {
                'total_count': AuditLog.query.count(),
                'oldest_entry': None,
                'retention_days': AUDIT_LOG_RETENTION_DAYS
            },
            'ai_agent_logs': {
                'total_count': AIAgentCallLog.query.count(),
                'oldest_entry': None,
                'retention_days': AI_AGENT_LOG_RETENTION_DAYS
            }
        }

        # Get oldest audit log
        oldest_audit = AuditLog.query.order_by(AuditLog.timestamp.asc()).first()
        if oldest_audit:
            stats['audit_logs']['oldest_entry'] = oldest_audit.timestamp.isoformat()
            stats['audit_logs']['oldest_age_days'] = (datetime.utcnow() - oldest_audit.timestamp).days

        # Get oldest AI agent log
        # FIXED: Use call_start instead of call_date
        oldest_ai = AIAgentCallLog.query.order_by(AIAgentCallLog.call_start.asc()).first()
        if oldest_ai:
            stats['ai_agent_logs']['oldest_entry'] = oldest_ai.call_start.isoformat()
            stats['ai_agent_logs']['oldest_age_days'] = (datetime.utcnow() - oldest_ai.call_start).days

        return stats

    except Exception as e:
        logger.error(f"Error getting log statistics: {str(e)}")
        return {}


# ============================================================================
# BACKGROUND CLEANUP SCHEDULER
# ============================================================================

class LogCleanupScheduler:
    """Background scheduler for periodic log cleanup"""

    def __init__(self, app=None):
        self.app = app
        self.running = False
        self.thread = None
        self.check_interval = CLEANUP_CHECK_HOURS * 3600  # Convert to seconds

    def init_app(self, app):
        """Initialize scheduler with Flask app"""
        self.app = app
        self.start()

    def start(self):
        """Start the background cleanup scheduler"""
        if self.running:
            logger.debug("Log cleanup scheduler already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()
        logger.info(f"Log cleanup scheduler started (running every {CLEANUP_CHECK_HOURS} hours)")

    def stop(self):
        """Stop the background cleanup scheduler"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Log cleanup scheduler stopped")

    def _run_scheduler(self):
        """Background thread that runs periodic cleanup"""
        # Wait a bit before first cleanup (give app time to start)
        time.sleep(300)  # 5 minutes

        while self.running:
            try:
                if self.app:
                    with self.app.app_context():
                        cleanup_all_logs()
                else:
                    logger.warning("App context not available for log cleanup")

            except Exception as e:
                logger.error(f"Error in log cleanup scheduler: {str(e)}")

            # Sleep until next check
            time.sleep(self.check_interval)


# Global scheduler instance
log_cleanup_scheduler = LogCleanupScheduler()


def init_log_cleanup(app):
    """
    Initialize log cleanup scheduler
    Call this from your app factory

    Args:
        app: Flask application instance
    """
    log_cleanup_scheduler.init_app(app)
    logger.info("Log cleanup system initialized")


# ============================================================================
# AI AGENT CALL LOGGING
# ============================================================================

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

        logger.debug(f"Logged AI agent call: {call_id} from {caller_number}")
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
            query = query.filter(AIAgentCallLog.caller_number.contains(caller_number))

        # FIXED: Use call_start instead of call_date
        logs = query.order_by(AIAgentCallLog.call_start.desc()).limit(limit).all()
        return [log.to_dict() for log in logs]

    except Exception as e:
        logger.error(f"Error retrieving AI agent call logs: {str(e)}")
        return []