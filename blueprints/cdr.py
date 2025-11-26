"""
CDR (Call Detail Records) Blueprint
Handles all CDR-related routes and analytics
"""

from flask import Blueprint, render_template, jsonify, request
from datetime import datetime, timedelta
import logging
from collections import defaultdict
from blueprints.api_core import api

logger = logging.getLogger(__name__)

cdr_bp = Blueprint('cdr', __name__, template_folder='../templates/cdr')


def format_duration(seconds):
    """Format seconds to human readable duration"""
    if not seconds:
        return "0s"
    seconds = int(seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


def fetch_cdrs(days=7, limit=1000, offset=0):
    """Fetch CDR records with caching"""
    cache_key = f"cdrs_{days}_{limit}_{offset}"

    # Check cache (5 minute TTL)
    if cache_key in api.cache:
        cache_age = datetime.now() - api.cache_time[cache_key]
        if cache_age.total_seconds() < 300:  # 5 minutes
            return api.cache[cache_key]

    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')

    query = f'''
    {{
        fetchAllCdrs(
            first: {limit}
            after: {offset}
            orderby: date
            startDate: "{start_date}"
            endDate: "{end_date}"
        ) {{
            cdrs {{
                id
                uniqueid
                calldate
                clid
                src
                dst
                disposition
                duration
                billsec
                cnum
                outbound_cnum
                dcontext
            }}
        }}
    }}
    '''

    data = api.graphql_query(query)
    cdrs = data.get('fetchAllCdrs', {}).get('cdrs', [])

    # Cache the result
    api.cache[cache_key] = cdrs
    api.cache_time[cache_key] = datetime.now()

    return cdrs


@cdr_bp.route('/')
def index():
    """CDR dashboard page"""
    return render_template('cdr_index.html')


@cdr_bp.route('/api/calls')
def get_calls():
    """Get paginated call records"""
    try:
        days = request.args.get('days', 7, type=int)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        # Fetch more records than needed for pagination
        all_cdrs = fetch_cdrs(days=days, limit=10000)

        # Calculate pagination
        total_records = len(all_cdrs)
        total_pages = (total_records + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page

        paginated_cdrs = all_cdrs[start_idx:end_idx]

        calls = []
        for cdr in paginated_cdrs:
            calls.append({
                'id': cdr.get('id'),
                'calldate': cdr.get('calldate', ''),
                'caller_id': cdr.get('clid') or cdr.get('cnum') or '-',
                'source': cdr.get('src') or '-',
                'destination': cdr.get('dst') or '-',
                'duration': cdr.get('duration', 0),
                'billsec': cdr.get('billsec', 0),
                'disposition': cdr.get('disposition', 'UNKNOWN'),
                'duration_formatted': format_duration(cdr.get('duration')),
                'billsec_formatted': format_duration(cdr.get('billsec'))
            })

        return jsonify({
            'calls': calls,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_records': total_records,
                'total_pages': total_pages
            }
        })
    except Exception as e:
        logger.error(f"Error fetching calls: {str(e)}")
        return jsonify({'error': str(e)}), 500


@cdr_bp.route('/api/stats')
def get_stats():
    """Get overall statistics"""
    try:
        days = request.args.get('days', 7, type=int)
        cdrs = fetch_cdrs(days=days, limit=10000)

        total_calls = len(cdrs)
        answered_calls = sum(1 for c in cdrs if c.get('disposition') == 'ANSWERED')
        missed_calls = sum(1 for c in cdrs if c.get('disposition') in ['NO ANSWER', 'BUSY', 'FAILED'])

        answered_durations = [c.get('billsec', 0) for c in cdrs if c.get('disposition') == 'ANSWERED']
        avg_duration = sum(answered_durations) / len(answered_durations) if answered_durations else 0
        total_duration = sum(answered_durations)

        return jsonify({
            'total_calls': total_calls,
            'answered_calls': answered_calls,
            'missed_calls': missed_calls,
            'avg_duration': round(avg_duration, 2),
            'avg_duration_formatted': format_duration(int(avg_duration)),
            'total_duration': total_duration,
            'total_duration_formatted': format_duration(int(total_duration))
        })
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}")
        return jsonify({'error': str(e)}), 500


@cdr_bp.route('/api/hourly-stats')
def get_hourly():
    """Get hourly call distribution"""
    try:
        days = request.args.get('days', 7, type=int)
        cdrs = fetch_cdrs(days=days, limit=10000)
        hourly = defaultdict(int)

        for cdr in cdrs:
            try:
                calldate = datetime.strptime(cdr.get('calldate', ''), '%Y-%m-%d %H:%M:%S')
                hour = calldate.hour
                hourly[hour] += 1
            except:
                continue

        data = [{'hour': h, 'calls': hourly.get(h, 0)} for h in range(24)]
        return jsonify({'data': data})
    except Exception as e:
        logger.error(f"Error fetching hourly stats: {str(e)}")
        return jsonify({'error': str(e)}), 500


@cdr_bp.route('/api/daily-stats')
def get_daily():
    """Get daily call distribution"""
    try:
        days = request.args.get('days', 30, type=int)
        cdrs = fetch_cdrs(days=days, limit=10000)
        daily = defaultdict(lambda: {'total': 0, 'answered': 0, 'missed': 0})

        for cdr in cdrs:
            try:
                calldate = datetime.strptime(cdr.get('calldate', ''), '%Y-%m-%d %H:%M:%S')
                date_key = calldate.strftime('%Y-%m-%d')
                daily[date_key]['total'] += 1

                if cdr.get('disposition') == 'ANSWERED':
                    daily[date_key]['answered'] += 1
                elif cdr.get('disposition') in ['NO ANSWER', 'BUSY', 'FAILED']:
                    daily[date_key]['missed'] += 1
            except:
                continue

        # Sort by date
        sorted_daily = sorted(daily.items())
        data = [
            {
                'date': date,
                'total': stats['total'],
                'answered': stats['answered'],
                'missed': stats['missed']
            }
            for date, stats in sorted_daily
        ]
        return jsonify({'data': data})
    except Exception as e:
        logger.error(f"Error fetching daily stats: {str(e)}")
        return jsonify({'error': str(e)}), 500


@cdr_bp.route('/api/top-callers')
def get_top_callers():
    """Get top callers"""
    try:
        days = request.args.get('days', 7, type=int)
        limit = request.args.get('limit', 10, type=int)
        cdrs = fetch_cdrs(days=days, limit=10000)

        caller_stats = defaultdict(lambda: {'count': 0, 'answered': 0, 'total_duration': 0})

        for cdr in cdrs:
            src = cdr.get('src') or 'Unknown'
            caller_stats[src]['count'] += 1
            if cdr.get('disposition') == 'ANSWERED':
                caller_stats[src]['answered'] += 1
                caller_stats[src]['total_duration'] += cdr.get('billsec', 0)

        # Sort by call count
        sorted_callers = sorted(
            caller_stats.items(),
            key=lambda x: x[1]['count'],
            reverse=True
        )[:limit]

        data = [
            {
                'number': number,
                'total_calls': stats['count'],
                'answered_calls': stats['answered'],
                'total_duration': stats['total_duration'],
                'total_duration_formatted': format_duration(stats['total_duration'])
            }
            for number, stats in sorted_callers
        ]
        return jsonify({'data': data})
    except Exception as e:
        logger.error(f"Error fetching top callers: {str(e)}")
        return jsonify({'error': str(e)}), 500


@cdr_bp.route('/api/top-destinations')
def get_top_destinations():
    """Get top destinations"""
    try:
        days = request.args.get('days', 7, type=int)
        limit = request.args.get('limit', 10, type=int)
        cdrs = fetch_cdrs(days=days, limit=10000)

        dest_stats = defaultdict(lambda: {'count': 0, 'answered': 0, 'total_duration': 0})

        for cdr in cdrs:
            dst = cdr.get('dst') or 'Unknown'
            dest_stats[dst]['count'] += 1
            if cdr.get('disposition') == 'ANSWERED':
                dest_stats[dst]['answered'] += 1
                dest_stats[dst]['total_duration'] += cdr.get('billsec', 0)

        sorted_dests = sorted(
            dest_stats.items(),
            key=lambda x: x[1]['count'],
            reverse=True
        )[:limit]

        data = [
            {
                'number': number,
                'total_calls': stats['count'],
                'answered_calls': stats['answered'],
                'total_duration': stats['total_duration'],
                'total_duration_formatted': format_duration(stats['total_duration'])
            }
            for number, stats in sorted_dests
        ]
        return jsonify({'data': data})
    except Exception as e:
        logger.error(f"Error fetching top destinations: {str(e)}")
        return jsonify({'error': str(e)}), 500