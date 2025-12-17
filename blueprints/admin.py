"""
Admin Blueprint - COMPLETE WORKING VERSION
Manages users, roles, audit logs, log cleanup, and service monitoring
Only accessible by admin users
FIXED: User deletion, case-insensitive email lookups, application context issues
"""

from flask import Blueprint, render_template, jsonify, request, session, current_app
import logging
from models import db, User, UserRole, AuditLog, AIAgentCallLog
from blueprints.auth import login_required, permission_required, get_current_user
from audit_utils import (
    log_action,
    get_audit_logs,
    get_ai_agent_call_logs,
    cleanup_old_audit_logs,
    cleanup_old_ai_agent_logs,
    cleanup_all_logs,
    get_log_statistics,
    AUDIT_LOG_RETENTION_DAYS,
    AI_AGENT_LOG_RETENTION_DAYS,
    CLEANUP_CHECK_HOURS
)
# FIX: Import from blueprints directory
from blueprints.service_monitor import service_monitor
from blueprints.teams_notifier import teams_notifier
from datetime import datetime, timedelta
from sqlalchemy import func

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)


# ============================================================================
# DASHBOARD
# ============================================================================

@admin_bp.route('/')
@login_required
@permission_required('view')
def index():
    """Admin dashboard"""
    log_action(
        action='view',
        resource_type='admin_page',
        details='Accessed admin dashboard'
    )
    return render_template('admin/index.html')


@admin_bp.route('/monitoring')
@login_required
@permission_required('view')
def monitoring():
    """Service monitoring page"""
    log_action(
        action='view',
        resource_type='monitoring_page',
        details='Accessed service monitoring page'
    )
    return render_template('admin/monitoring.html')


# ============================================================================
# USER MANAGEMENT ROUTES
# ============================================================================

@admin_bp.route('/api/users')
@login_required
@permission_required('view')
def list_users():
    """List all users"""
    try:
        users = User.query.all()

        log_action(
            action='view',
            resource_type='users_list',
            details={'total_users': len(users)}
        )

        return jsonify({
            'success': True,
            'users': [user.to_dict() for user in users]
        })
    except Exception as e:
        logger.error(f"Error listing users: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/users', methods=['POST'])
@login_required
@permission_required('create')
def create_user():
    """Create a new user"""
    try:
        data = request.get_json()

        # Validate required fields
        # CRITICAL FIX: Normalize email to lowercase
        email = data.get('email', '').strip().lower()
        name = data.get('name', '').strip()
        role = data.get('role', 'VIEWER').upper()

        if not email:
            return jsonify({
                'success': False,
                'error': 'Email is required'
            }), 400

        if not name:
            return jsonify({
                'success': False,
                'error': 'Name is required'
            }), 400

        # Validate role
        if role not in [r.name for r in UserRole]:
            return jsonify({
                'success': False,
                'error': f'Invalid role. Must be one of: {", ".join([r.name for r in UserRole])}'
            }), 400

        # CRITICAL FIX: Case-insensitive check if user already exists
        existing_user = User.query.filter(func.lower(User.email) == email).first()
        if existing_user:
            return jsonify({
                'success': False,
                'error': f'A user with this email already exists (ID: {existing_user.id}, Active: {existing_user.is_active})'
            }), 400

        # Create new user
        new_user = User(
            email=email,
            name=name,
            role=UserRole[role],
            is_active=True,  # CRITICAL FIX: Explicitly set to True
            azure_id=data.get('azure_id')  # Optional Azure AD ID
        )

        db.session.add(new_user)
        db.session.commit()

        # Log the action
        log_action(
            'create',
            'user',
            new_user.id,
            {
                'email': email,
                'name': name,
                'role': role,
                'is_active': True
            }
        )

        logger.info(f"✅ Created new user: {email} with role {role}, is_active=True, ID={new_user.id}")

        return jsonify({
            'success': True,
            'message': f'User {email} created successfully and is ready to log in',
            'user': new_user.to_dict()
        }), 201

    except Exception as e:
        logger.error(f"❌ Error creating user: {str(e)}", exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>')
@login_required
@permission_required('view')
def get_user(user_id):
    """Get user details"""
    try:
        user = User.query.get_or_404(user_id)

        log_action(
            action='view',
            resource_type='user',
            resource_id=user_id,
            details={'email': user.email}
        )

        return jsonify({
            'success': True,
            'user': user.to_dict()
        })
    except Exception as e:
        logger.error(f"Error getting user: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
@permission_required('edit')
def update_user(user_id):
    """Update user details (name, email)"""
    try:
        data = request.get_json()
        user = User.query.get_or_404(user_id)

        changes = {}

        # Update name if provided
        if 'name' in data and data['name'].strip():
            old_name = user.name
            user.name = data['name'].strip()
            changes['name'] = {'old': old_name, 'new': user.name}

        # Update email if provided
        if 'email' in data and data['email'].strip():
            # CRITICAL FIX: Normalize email to lowercase
            new_email = data['email'].strip().lower()

            # CRITICAL FIX: Case-insensitive check if email is already taken by another user
            existing = User.query.filter(
                func.lower(User.email) == new_email,
                User.id != user_id
            ).first()
            if existing:
                return jsonify({
                    'success': False,
                    'error': 'Email already in use by another user'
                }), 400

            old_email = user.email
            user.email = new_email
            changes['email'] = {'old': old_email, 'new': user.email}

        if changes:
            db.session.commit()

            # Log the action
            log_action('update', 'user', user_id, changes)
            logger.info(f"✅ Updated user {user_id}: {changes}")

            return jsonify({
                'success': True,
                'message': 'User updated successfully',
                'user': user.to_dict()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No valid fields to update'
            }), 400

    except Exception as e:
        logger.error(f"❌ Error updating user: {str(e)}", exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>/role', methods=['PUT'])
@login_required
@permission_required('edit')
def update_user_role(user_id):
    """Update user role"""
    try:
        data = request.get_json()
        new_role = data.get('role')

        if not new_role or new_role not in [r.value for r in UserRole]:
            return jsonify({
                'success': False,
                'error': 'Invalid role'
            }), 400

        user = User.query.get_or_404(user_id)
        old_role = user.role.value
        user.role = UserRole[new_role.upper()]

        db.session.commit()

        # Log the action
        log_action(
            'update_role',
            'user',
            user_id,
            {'old_role': old_role, 'new_role': new_role}
        )

        logger.info(f"✅ Updated user {user.email} role from {old_role} to {new_role}")

        return jsonify({
            'success': True,
            'message': f'User role updated to {new_role}',
            'user': user.to_dict()
        })

    except Exception as e:
        logger.error(f"❌ Error updating user role: {str(e)}", exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>/active', methods=['PUT'])
@login_required
@permission_required('edit')
def update_user_active(user_id):
    """Activate or deactivate user"""
    try:
        data = request.get_json()
        is_active = data.get('is_active')

        if is_active is None:
            return jsonify({
                'success': False,
                'error': 'is_active field is required'
            }), 400

        user = User.query.get_or_404(user_id)

        # Prevent admin from deactivating themselves
        current_user = get_current_user()
        if current_user and current_user.id == user_id and not is_active:
            return jsonify({
                'success': False,
                'error': 'You cannot deactivate your own account'
            }), 400

        old_status = user.is_active
        user.is_active = bool(is_active)

        db.session.commit()

        # Log the action
        log_action(
            'deactivate' if not is_active else 'activate',
            'user',
            user_id,
            {'old_status': old_status, 'new_status': is_active}
        )

        status = 'activated' if is_active else 'deactivated'
        logger.info(f"✅ User {user.email} {status}")

        return jsonify({
            'success': True,
            'message': f'User {status} successfully',
            'user': user.to_dict()
        })

    except Exception as e:
        logger.error(f"❌ Error updating user status: {str(e)}", exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
@permission_required('delete')
def delete_user(user_id):
    """Delete a user (actually deletes from database)"""
    try:
        user = User.query.get_or_404(user_id)

        # Prevent admin from deleting themselves
        current_user = get_current_user()
        if current_user and current_user.id == user_id:
            return jsonify({
                'success': False,
                'error': 'You cannot delete your own account'
            }), 400

        # Store user info before deletion for logging
        user_email = user.email
        user_name = user.name

        # CRITICAL FIX: Actually delete the user from database
        db.session.delete(user)
        db.session.commit()

        # Log the action - note: we can't use user_id as resource_id since user is deleted
        # Use current_user context for logging
        if current_user:
            log_entry = AuditLog(
                user_id=current_user.id,
                action='delete',
                resource_type='user',
                resource_id=str(user_id),
                details=f'Deleted user: {user_email} ({user_name})',
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent', '')[:500]
            )
            db.session.add(log_entry)
            db.session.commit()

        logger.info(f"✅ DELETED user ID {user_id}: {user_email} ({user_name})")

        return jsonify({
            'success': True,
            'message': f'User {user_email} deleted successfully'
        })

    except Exception as e:
        logger.error(f"❌ Error deleting user: {str(e)}", exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# AUDIT LOG ROUTES
# ============================================================================

@admin_bp.route('/api/audit-logs')
@login_required
@permission_required('view')
def list_audit_logs():
    """List audit logs with filtering"""
    try:
        resource_type = request.args.get('resource_type')
        resource_id = request.args.get('resource_id')
        user_id = request.args.get('user_id', type=int)
        limit = request.args.get('limit', 100, type=int)

        logs = get_audit_logs(
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            limit=limit
        )

        log_action(
            action='view',
            resource_type='audit_logs',
            details={
                'filters': {
                    'resource_type': resource_type,
                    'resource_id': resource_id,
                    'user_id': user_id
                },
                'count': len(logs)
            }
        )

        return jsonify({
            'success': True,
            'logs': logs,
            'count': len(logs)
        })

    except Exception as e:
        logger.error(f"Error listing audit logs: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/audit-logs/stats')
@login_required
@permission_required('view')
def audit_log_stats():
    """Get audit log statistics"""
    try:
        days = request.args.get('days', 7, type=int)
        start_date = datetime.utcnow() - timedelta(days=days)

        # Total actions
        total_actions = AuditLog.query.filter(
            AuditLog.timestamp >= start_date
        ).count()

        # Actions by user
        actions_by_user = db.session.query(
            User.email,
            User.name,
            func.count(AuditLog.id).label('count')
        ).join(AuditLog).filter(
            AuditLog.timestamp >= start_date
        ).group_by(User.id).all()

        # Actions by type
        actions_by_type = db.session.query(
            AuditLog.action,
            func.count(AuditLog.id).label('count')
        ).filter(
            AuditLog.timestamp >= start_date
        ).group_by(AuditLog.action).all()

        log_action(
            action='view',
            resource_type='audit_log_stats',
            details={'days': days, 'total_actions': total_actions}
        )

        return jsonify({
            'success': True,
            'stats': {
                'total_actions': total_actions,
                'actions_by_user': [
                    {'email': email, 'name': name, 'count': count}
                    for email, name, count in actions_by_user
                ],
                'actions_by_type': [
                    {'action': action, 'count': count}
                    for action, count in actions_by_type
                ]
            }
        })

    except Exception as e:
        logger.error(f"Error getting audit log stats: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# AI AGENT CALL LOG ROUTES
# ============================================================================

@admin_bp.route('/api/ai-agent-calls')
@login_required
@permission_required('view')
def list_ai_agent_calls():
    """List AI agent call logs"""
    try:
        limit = request.args.get('limit', 100, type=int)
        caller_number = request.args.get('caller_number')

        logs = get_ai_agent_call_logs(limit=limit, caller_number=caller_number)

        log_action(
            action='view',
            resource_type='ai_agent_call_logs',
            details={
                'caller_number': caller_number,
                'count': len(logs)
            }
        )

        return jsonify({
            'success': True,
            'calls': logs,
            'count': len(logs)
        })

    except Exception as e:
        logger.error(f"Error listing AI agent calls: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/ai-agent-calls/<call_id>')
@login_required
@permission_required('view')
def get_ai_agent_call(call_id):
    """Get specific AI agent call details"""
    try:
        call = AIAgentCallLog.query.filter_by(call_id=call_id).first_or_404()

        log_action(
            action='view',
            resource_type='ai_agent_call',
            resource_id=call_id,
            details={'caller_number': call.caller_number}
        )

        return jsonify({
            'success': True,
            'call': call.to_dict()
        })

    except Exception as e:
        logger.error(f"Error getting AI agent call: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/ai-agent-calls/stats')
@login_required
@permission_required('view')
def ai_agent_call_stats():
    """Get AI agent call statistics"""
    try:
        days = request.args.get('days', 7, type=int)
        start_date = datetime.utcnow() - timedelta(days=days)

        # Total calls
        total_calls = AIAgentCallLog.query.filter(
            AIAgentCallLog.call_start >= start_date
        ).count()

        # Calls by intent
        calls_by_intent = db.session.query(
            AIAgentCallLog.intent,
            func.count(AIAgentCallLog.id).label('count')
        ).filter(
            AIAgentCallLog.call_start >= start_date
        ).group_by(AIAgentCallLog.intent).all()

        # Average duration
        avg_duration = db.session.query(
            func.avg(AIAgentCallLog.call_duration)
        ).filter(
            AIAgentCallLog.call_start >= start_date
        ).scalar()

        # Sentiment distribution
        sentiment_dist = db.session.query(
            AIAgentCallLog.sentiment,
            func.count(AIAgentCallLog.id).label('count')
        ).filter(
            AIAgentCallLog.call_start >= start_date
        ).group_by(AIAgentCallLog.sentiment).all()

        log_action(
            action='view',
            resource_type='ai_agent_call_stats',
            details={'days': days, 'total_calls': total_calls}
        )

        return jsonify({
            'success': True,
            'stats': {
                'total_calls': total_calls,
                'avg_duration': round(avg_duration, 2) if avg_duration else 0,
                'calls_by_intent': [
                    {'intent': intent or 'Unknown', 'count': count}
                    for intent, count in calls_by_intent
                ],
                'sentiment_distribution': [
                    {'sentiment': sentiment or 'Unknown', 'count': count}
                    for sentiment, count in sentiment_dist
                ]
            }
        })

    except Exception as e:
        logger.error(f"Error getting AI agent call stats: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# LOG CLEANUP MANAGEMENT ROUTES
# ============================================================================

@admin_bp.route('/api/logs/statistics')
@login_required
@permission_required('view')
def get_log_stats():
    """Get log storage statistics"""
    try:
        stats = get_log_statistics()

        # Log statistics view
        log_action(
            action='view',
            resource_type='log_statistics',
            details='Viewed log storage statistics'
        )

        return jsonify({
            'status': 'success',
            'statistics': stats
        })

    except Exception as e:
        logger.error(f"Error getting log statistics: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_bp.route('/api/logs/cleanup/audit', methods=['POST'])
@login_required
@permission_required('delete')
def manual_cleanup_audit():
    """Manually trigger audit log cleanup"""
    try:
        data = request.get_json() or {}
        retention_days = data.get('retention_days', AUDIT_LOG_RETENTION_DAYS)

        deleted = cleanup_old_audit_logs(retention_days)

        # Log cleanup action
        log_action(
            action='cleanup',
            resource_type='audit_logs',
            details={
                'retention_days': retention_days,
                'deleted_count': deleted,
                'trigger': 'manual'
            }
        )

        return jsonify({
            'status': 'success',
            'message': f'Deleted {deleted} audit logs older than {retention_days} days',
            'deleted_count': deleted
        })

    except Exception as e:
        logger.error(f"Error in manual audit log cleanup: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_bp.route('/api/logs/cleanup/ai-agent', methods=['POST'])
@login_required
@permission_required('delete')
def manual_cleanup_ai_agent():
    """Manually trigger AI agent log cleanup"""
    try:
        data = request.get_json() or {}
        retention_days = data.get('retention_days', AI_AGENT_LOG_RETENTION_DAYS)

        deleted = cleanup_old_ai_agent_logs(retention_days)

        # Log cleanup action
        log_action(
            action='cleanup',
            resource_type='ai_agent_logs',
            details={
                'retention_days': retention_days,
                'deleted_count': deleted,
                'trigger': 'manual'
            }
        )

        return jsonify({
            'status': 'success',
            'message': f'Deleted {deleted} AI agent logs older than {retention_days} days',
            'deleted_count': deleted
        })

    except Exception as e:
        logger.error(f"Error in manual AI agent log cleanup: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_bp.route('/api/logs/cleanup/all', methods=['POST'])
@login_required
@permission_required('delete')
def manual_cleanup_all():
    """Manually trigger cleanup for all log types"""
    try:
        stats = cleanup_all_logs()

        # Log cleanup action
        log_action(
            action='cleanup',
            resource_type='all_logs',
            details={
                'audit_logs_deleted': stats['audit_logs_deleted'],
                'ai_agent_logs_deleted': stats['ai_agent_logs_deleted'],
                'total_deleted': stats['total_deleted'],
                'trigger': 'manual'
            }
        )

        return jsonify({
            'status': 'success',
            'message': f"Deleted {stats['total_deleted']} logs total",
            'statistics': stats
        })

    except Exception as e:
        logger.error(f"Error in manual log cleanup: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_bp.route('/api/logs/retention-config')
@login_required
@permission_required('view')
def get_retention_config():
    """Get current log retention configuration"""
    try:
        config = {
            'audit_log_retention_days': AUDIT_LOG_RETENTION_DAYS,
            'ai_agent_log_retention_days': AI_AGENT_LOG_RETENTION_DAYS,
            'cleanup_check_hours': CLEANUP_CHECK_HOURS
        }

        log_action(
            action='view',
            resource_type='log_retention_config',
            details='Viewed log retention configuration'
        )

        return jsonify({
            'status': 'success',
            'config': config
        })

    except Exception as e:
        logger.error(f"Error getting retention config: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ============================================================================
# SERVICE MONITORING & TEAMS NOTIFICATIONS
# ============================================================================

@admin_bp.route('/api/monitoring/status')
@login_required
@permission_required('view')
def get_monitoring_status():
    """Get status of all monitored services"""
    try:
        status = service_monitor.get_all_status()

        log_action(
            action='view',
            resource_type='service_monitoring_status',
            details={
                'total_services': len(status),
                'services': list(status.keys())
            }
        )

        return jsonify({
            'status': 'success',
            'monitoring_active': service_monitor.running,
            'services': status
        })

    except Exception as e:
        logger.error(f"Error getting monitoring status: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_bp.route('/api/monitoring/test-notification', methods=['POST'])
@login_required
@permission_required('edit')
def test_teams_notification():
    """Send a test notification to Teams"""
    try:
        data = request.get_json() or {}
        message = data.get('message', 'This is a test notification from FreePBX Dashboard')

        success = teams_notifier.send_notification(
            title="Test Notification",
            message=message,
            severity="info",
            service_name="FreePBX Dashboard",
            additional_info={
                'Test': 'Yes',
                'Sent By': session.get('user', {}).get('email', 'Unknown')
            }
        )

        log_action(
            action='test',
            resource_type='teams_notification',
            details={
                'success': success,
                'message': message
            }
        )

        if success:
            return jsonify({
                'status': 'success',
                'message': 'Test notification sent successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to send test notification. Check webhook URL.'
            }), 500

    except Exception as e:
        logger.error(f"Error sending test notification: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_bp.route('/api/monitoring/webhook-config')
@login_required
@permission_required('view')
def get_webhook_config():
    """Get Teams webhook configuration status"""
    try:
        webhook_configured = bool(current_app.config.get('TEAMS_WEBHOOK_URL'))
        notifications_enabled = current_app.config.get('ENABLE_TEAMS_NOTIFICATIONS', False)

        log_action(
            action='view',
            resource_type='teams_webhook_config',
            details='Viewed Teams webhook configuration'
        )

        return jsonify({
            'status': 'success',
            'webhook_configured': webhook_configured,
            'notifications_enabled': notifications_enabled,
            'monitoring_active': service_monitor.running
        })

    except Exception as e:
        logger.error(f"Error getting webhook config: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500