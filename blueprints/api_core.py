"""
FreePBX API Core - Shared API functionality
Handles authentication and GraphQL queries
"""

from flask import Blueprint, jsonify, current_app
import requests
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

api_core_bp = Blueprint('api_core', __name__)


class FreePBXAPI:
    """Singleton API handler for FreePBX GraphQL"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FreePBXAPI, cls).__new__(cls)
            cls._instance.access_token = None
            cls._instance.token_expires = None
            cls._instance.cache = {}
            cls._instance.cache_time = {}
        return cls._instance

    def get_access_token(self):
        """Get OAuth2 access token"""
        if self.access_token and self.token_expires and datetime.now() < self.token_expires:
            return self.access_token

        try:
            freepbx_host = current_app.config['FREEPBX_HOST']
            token_url = f"{freepbx_host}/admin/api/api/token"

            response = requests.post(
                token_url,
                data={
                    'grant_type': 'client_credentials',
                    'client_id': current_app.config['FREEPBX_CLIENT_ID'],
                    'client_secret': current_app.config['FREEPBX_CLIENT_SECRET']
                }
            )
            response.raise_for_status()
            data = response.json()

            self.access_token = data['access_token']
            self.token_expires = datetime.now() + timedelta(seconds=data.get('expires_in', 3600) - 300)

            return self.access_token
        except Exception as e:
            logger.error(f"Failed to get access token: {str(e)}")
            raise

    def graphql_query(self, query):
        """Execute GraphQL query"""
        try:
            token = self.get_access_token()

            freepbx_host = current_app.config['FREEPBX_HOST']
            graphql_url = f"{freepbx_host}/admin/api/api/gql"

            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            }

            response = requests.post(
                graphql_url,
                json={'query': query},
                headers=headers
            )
            response.raise_for_status()

            data = response.json()

            if 'errors' in data:
                logger.error(f"GraphQL errors: {data['errors']}")
                raise Exception(f"GraphQL error: {data['errors'][0]['message']}")

            return data['data']
        except Exception as e:
            logger.error(f"GraphQL query failed: {str(e)}")
            raise

    def clear_cache(self, pattern=None):
        """Clear cache entries"""
        if pattern:
            keys_to_delete = [k for k in self.cache.keys() if pattern in k]
            for key in keys_to_delete:
                del self.cache[key]
                del self.cache_time[key]
        else:
            self.cache.clear()
            self.cache_time.clear()


# Global API instance
api = FreePBXAPI()


@api_core_bp.route('/test')
def test_api():
    """Test FreePBX API connection"""
    try:
        token = api.get_access_token()
        return jsonify({
            'status': 'success',
            'message': 'API connection successful',
            'has_token': bool(token)
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@api_core_bp.route('/reload-config', methods=['POST'])
def reload_config():
    """Apply configuration changes (doreload)"""
    try:
        query = '''
        mutation {
            doreload {
                status
                message
            }
        }
        '''
        data = api.graphql_query(query)

        if data.get('doreload', {}).get('status'):
            return jsonify({
                'status': 'success',
                'message': 'Configuration reloaded successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': data.get('doreload', {}).get('message', 'Reload failed')
            }), 500
    except Exception as e:
        logger.error(f"Error reloading config: {str(e)}")
        return jsonify({'error': str(e)}), 500