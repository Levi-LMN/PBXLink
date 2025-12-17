"""
Azure AD Authentication Blueprint
Handles Microsoft OAuth login and user session management
FIXED: Case-insensitive email lookup for user authentication
"""

from flask import Blueprint, redirect, url_for, session, request, jsonify, current_app, render_template, flash
import requests
import logging
from functools import wraps
from datetime import datetime
from models import db, User, UserRole
from urllib.parse import urlencode
import secrets
from sqlalchemy import func

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


def login_required(f):
    """Decorator to require authentication"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)

    return decorated_function


def permission_required(action):
    """Decorator to require specific permission"""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return jsonify({'error': 'Unauthorized'}), 401

            user = User.query.filter_by(email=session['user']['email']).first()
            if not user or not user.is_active:
                return jsonify({'error': 'Forbidden - user not active'}), 403

            if not user.has_permission(action):
                return jsonify({'error': 'Forbidden - insufficient permissions'}), 403

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def get_current_user():
    """Get current logged-in user"""
    if 'user' not in session:
        return None
    return User.query.filter_by(email=session['user']['email']).first()


@auth_bp.route('/login-page')
def login_page():
    """Show login page"""
    # If user is already logged in, redirect to dashboard
    if 'user' in session:
        return redirect(url_for('index'))

    # Get error from query params if redirected from callback
    error = request.args.get('error')
    return render_template('auth/login.html', error=error)


@auth_bp.route('/login')
def login():
    """Initiate Azure AD login flow"""
    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    session['auth_state'] = state

    # Build authorization URL
    params = {
        'client_id': current_app.config['AZURE_AD_CLIENT_ID'],
        'response_type': 'code',
        'redirect_uri': request.host_url.rstrip('/') + current_app.config['AZURE_AD_REDIRECT_PATH'],
        'response_mode': 'query',
        'scope': ' '.join(current_app.config['AZURE_AD_SCOPE']),
        'state': state,
        'prompt': 'select_account'  # Always show account selection
    }

    auth_url = f"{current_app.config['AZURE_AD_AUTH_ENDPOINT']}?{urlencode(params)}"
    return redirect(auth_url)


@auth_bp.route('/callback')
def callback():
    """Handle Azure AD callback"""
    # Verify state token
    if request.args.get('state') != session.get('auth_state'):
        logger.error("State token mismatch - possible CSRF attack")
        return redirect(url_for('auth.login_page', error='Security validation failed. Please try again.'))

    # Check for errors
    if 'error' in request.args:
        error = request.args.get('error')
        error_description = request.args.get('error_description', 'Unknown error')
        logger.error(f"Azure AD error: {error} - {error_description}")
        return redirect(url_for('auth.login_page', error=error_description))

    # Get authorization code
    code = request.args.get('code')
    if not code:
        return redirect(url_for('auth.login_page', error='No authorization code received'))

    try:
        # Exchange code for token
        token_data = {
            'client_id': current_app.config['AZURE_AD_CLIENT_ID'],
            'client_secret': current_app.config['AZURE_AD_CLIENT_SECRET'],
            'code': code,
            'redirect_uri': request.host_url.rstrip('/') + current_app.config['AZURE_AD_REDIRECT_PATH'],
            'grant_type': 'authorization_code',
            'scope': ' '.join(current_app.config['AZURE_AD_SCOPE'])
        }

        token_response = requests.post(
            current_app.config['AZURE_AD_TOKEN_ENDPOINT'],
            data=token_data,
            timeout=10
        )
        token_response.raise_for_status()
        tokens = token_response.json()

        # Get user info from Microsoft Graph
        headers = {'Authorization': f"Bearer {tokens['access_token']}"}
        user_response = requests.get(
            'https://graph.microsoft.com/v1.0/me',
            headers=headers,
            timeout=10
        )
        user_response.raise_for_status()
        user_info = user_response.json()

        # Get email from Azure AD
        email = user_info.get('mail') or user_info.get('userPrincipalName')
        if not email:
            logger.error("No email found in user info")
            return redirect(url_for('auth.login_page', error='Unable to retrieve email from Microsoft account'))

        # CRITICAL FIX: Normalize email to lowercase for case-insensitive comparison
        email = email.lower().strip()

        logger.info(f"Azure AD login attempt for email: {email}")

        # Check if user is superuser
        superuser_email = current_app.config.get('SUPERUSER_EMAIL')
        is_superuser = superuser_email and email == superuser_email.lower().strip()

        # CRITICAL FIX: Case-insensitive lookup using func.lower()
        user = User.query.filter(func.lower(User.email) == email).first()

        # DEBUG: Log user lookup result
        if user:
            logger.info(f"User found in database: {user.email}, is_active={user.is_active}, role={user.role.value}")
        else:
            logger.warning(f"User NOT found in database for email: {email}")
            # List all users for debugging
            all_users = User.query.all()
            logger.info(f"Database contains {len(all_users)} users:")
            for u in all_users:
                logger.info(f"  - {u.email} (active={u.is_active}, role={u.role.value})")

        # Authorization logic
        if is_superuser:
            logger.info(f"Superuser login detected: {email}")
            # Superuser can always log in
            if not user:
                # Create superuser account if it doesn't exist
                user = User(
                    email=email,
                    name=user_info.get('displayName', email),
                    azure_id=user_info.get('id'),
                    role=UserRole.ADMIN,
                    is_active=True
                )
                db.session.add(user)
                db.session.commit()
                logger.info(f"Created superuser account: {email}")
            else:
                # Ensure superuser always has admin role and is active
                user.role = UserRole.ADMIN
                user.is_active = True
                user.name = user_info.get('displayName', user.name)
                user.azure_id = user_info.get('id')
                db.session.commit()
                logger.info(f"Superuser {email} logged in")
        else:
            # Regular users must exist in database and be active
            if not user:
                logger.warning(f"Login rejected - user not in database: {email}")
                return redirect(url_for('auth.login_page',
                    error=f'Your account ({email}) has not been created yet. Please contact your administrator to request access.'))

            if not user.is_active:
                logger.warning(f"Login rejected - user inactive: {email}")
                return redirect(url_for('auth.login_page',
                    error='Your account has been deactivated. Please contact your administrator.'))

            # Update user information from Azure AD
            user.name = user_info.get('displayName', user.name)
            if not user.azure_id:
                user.azure_id = user_info.get('id')
            db.session.commit()
            logger.info(f"Regular user {email} logged in successfully")

        # Update last login
        user.last_login = datetime.utcnow()
        db.session.commit()

        # Store user in session
        session['user'] = {
            'id': user.id,
            'email': user.email,
            'name': user.name,
            'role': user.role.value,
            'is_superuser': is_superuser
        }
        session.permanent = True

        logger.info(f"User {email} logged in successfully with role {user.role.value}")
        flash('Successfully logged in!', 'success')
        return redirect(url_for('index'))

    except requests.exceptions.Timeout:
        logger.error("Request timeout during authentication")
        return redirect(url_for('auth.login_page',
            error='Authentication service is taking too long to respond. Please try again.'))
    except requests.exceptions.RequestException as e:
        logger.error(f"Error during authentication: {str(e)}")
        return redirect(url_for('auth.login_page',
            error='Authentication failed. Please try again later.'))
    except Exception as e:
        logger.error(f"Unexpected error during authentication: {str(e)}", exc_info=True)
        return redirect(url_for('auth.login_page',
            error='An unexpected error occurred. Please try again.'))


@auth_bp.route('/logout')
def logout():
    """Logout user - only clears app session, doesn't sign out of Microsoft"""
    user_email = session.get('user', {}).get('email', 'Unknown')
    session.clear()
    logger.info(f"User {user_email} logged out")

    flash('You have been successfully logged out.', 'info')

    # Just redirect to login page without signing out of Azure AD
    return redirect(url_for('auth.login_page'))


@auth_bp.route('/api/current-user')
def current_user():
    """Get current user info"""
    if 'user' not in session:
        return jsonify({'authenticated': False}), 401

    user = User.query.filter_by(email=session['user']['email']).first()
    if not user:
        return jsonify({'authenticated': False}), 401

    return jsonify({
        'authenticated': True,
        'user': user.to_dict()
    })


@auth_bp.route('/api/check-auth')
def check_auth():
    """Quick authentication check"""
    return jsonify({
        'authenticated': 'user' in session,
        'user': session.get('user') if 'user' in session else None
    })


# Error handlers for authentication blueprint
@auth_bp.errorhandler(401)
def unauthorized(error):
    """Handle unauthorized access"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Unauthorized', 'message': 'Please log in to access this resource'}), 401
    return redirect(url_for('auth.login_page', error='Please log in to continue'))


@auth_bp.errorhandler(403)
def forbidden(error):
    """Handle forbidden access"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Forbidden', 'message': 'You do not have permission to access this resource'}), 403
    flash('You do not have permission to access this resource.', 'danger')
    return redirect(url_for('index'))