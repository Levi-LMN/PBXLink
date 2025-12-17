"""
AI Agent Logging Blueprint - Receives logs from the AI agent service
"""

from flask import Blueprint, request, jsonify
import logging
from models import db, AIAgentCallLog, AIAgentTurn
from datetime import datetime
import json

logger = logging.getLogger(__name__)

ai_logging_bp = Blueprint('ai_logging', __name__)


@ai_logging_bp.route('/call/start', methods=['POST'])
def log_call_start():
    """Log the start of a new call"""
    try:
        data = request.get_json()

        call_log = AIAgentCallLog(
            call_id=data['call_id'],
            caller_number=data.get('caller_number'),
            caller_name=data.get('caller_name'),
            call_start=datetime.utcnow(),
            customer_data=json.dumps(data.get('customer_data', {}))
        )

        db.session.add(call_log)
        db.session.commit()

        return jsonify({
            'success': True,
            'call_log_id': call_log.id
        })

    except Exception as e:
        logger.error(f"Error logging call start: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ai_logging_bp.route('/call/turn', methods=['POST'])
def log_call_turn():
    """Log a conversation turn"""
    try:
        data = request.get_json()

        # Find the call log
        call_log = AIAgentCallLog.query.filter_by(
            call_id=data['call_id']
        ).first()

        if not call_log:
            return jsonify({
                'success': False,
                'error': 'Call log not found'
            }), 404

        # Create turn record
        turn = AIAgentTurn(
            call_log_id=call_log.id,
            turn_number=data['turn_number'],
            user_text=data.get('user_text'),
            user_confidence=data.get('user_confidence'),
            ai_text=data.get('ai_text'),
            function_called=data.get('function_called'),
            function_args=json.dumps(data.get('function_args')) if data.get('function_args') else None,
            function_result=json.dumps(data.get('function_result')) if data.get('function_result') else None
        )

        db.session.add(turn)
        db.session.commit()

        return jsonify({
            'success': True,
            'turn_id': turn.id
        })

    except Exception as e:
        logger.error(f"Error logging turn: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ai_logging_bp.route('/call/end', methods=['POST'])
def log_call_end():
    """Log the end of a call"""
    try:
        data = request.get_json()

        call_log = AIAgentCallLog.query.filter_by(
            call_id=data['call_id']
        ).first()

        if not call_log:
            return jsonify({
                'success': False,
                'error': 'Call log not found'
            }), 404

        # Update call log with end data
        call_log.call_end = datetime.utcnow()
        call_log.call_duration = data.get('call_duration')
        call_log.transcript = data.get('transcript')
        call_log.summary = data.get('summary')
        call_log.intent = data.get('intent')
        call_log.sentiment = data.get('sentiment')
        call_log.functions_called = json.dumps(data.get('functions_called', []))
        call_log.transferred_to = data.get('transferred_to')
        call_log.tickets_created = json.dumps(data.get('tickets_created', []))
        call_log.turns_count = data.get('turns_count', 0)
        call_log.no_speech_count = data.get('no_speech_count', 0)
        call_log.unclear_count = data.get('unclear_count', 0)
        call_log.avg_confidence = data.get('avg_confidence')

        db.session.commit()

        return jsonify({
            'success': True,
            'call_log_id': call_log.id
        })

    except Exception as e:
        logger.error(f"Error logging call end: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ai_logging_bp.route('/call/error', methods=['POST'])
def log_call_error():
    """Log a call error"""
    try:
        data = request.get_json()

        call_log = AIAgentCallLog.query.filter_by(
            call_id=data['call_id']
        ).first()

        if call_log:
            call_log.call_end = datetime.utcnow()
            call_log.summary = f"Error: {data.get('error', 'Unknown error')}"
            db.session.commit()

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Error logging call error: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500