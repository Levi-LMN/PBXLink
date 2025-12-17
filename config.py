"""
Configuration file for FreePBX Dashboard with Azure AD Authentication
"""

import os
from datetime import timedelta


class Config:
    """Base configuration"""

    # Flask settings
    SECRET_KEY = os.environ.get('SECRET_KEY', '')

    # Flask app settings
    DEBUG = False
    TESTING = False

    # Session configuration
    SESSION_TYPE = 'filesystem'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=1)

    # Database settings
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///freepbx_dashboard.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # FreePBX API settings
    FREEPBX_HOST = os.environ.get('FREEPBX_HOST', '')
    FREEPBX_CLIENT_ID = os.environ.get('FREEPBX_CLIENT_ID', '')
    FREEPBX_CLIENT_SECRET = os.environ.get('FREEPBX_CLIENT_SECRET', '')

    # SSH settings - Handle None/empty values properly
    FREEPBX_SSH_USER = os.environ.get('FREEPBX_SSH_USER', '')
    FREEPBX_SSH_PASSWORD = os.environ.get('FREEPBX_SSH_PASSWORD', '')

    # SSH Key - Convert 'None' string or empty to actual None
    _ssh_key = os.environ.get('FREEPBX_SSH_KEY', None)
    if _ssh_key and _ssh_key.strip() and _ssh_key.strip().lower() != 'none':
        FREEPBX_SSH_KEY = _ssh_key.strip()
    else:
        FREEPBX_SSH_KEY = None

    # Azure AD OAuth Settings
    AZURE_AD_TENANT_ID = os.environ.get('AZURE_AD_TENANT_ID', '')
    AZURE_AD_CLIENT_ID = os.environ.get('AZURE_AD_CLIENT_ID', '')
    AZURE_AD_CLIENT_SECRET = os.environ.get('AZURE_AD_CLIENT_SECRET', '')
    AZURE_AD_REDIRECT_PATH = '/auth/callback'

    # Azure AD endpoints
    def __init__(self):
        # Build the authority and endpoints based on tenant ID
        tenant_id = self.AZURE_AD_TENANT_ID or os.environ.get('AZURE_AD_TENANT_ID', '')
        self.AZURE_AD_AUTHORITY = f'https://login.microsoftonline.com/{tenant_id}'
        self.AZURE_AD_AUTH_ENDPOINT = f'{self.AZURE_AD_AUTHORITY}/oauth2/v2.0/authorize'
        self.AZURE_AD_TOKEN_ENDPOINT = f'{self.AZURE_AD_AUTHORITY}/oauth2/v2.0/token'

    AZURE_AD_SCOPE = ['User.Read', 'email', 'profile', 'openid']

    # Superuser email
    SUPERUSER_EMAIL = os.environ.get('SUPERUSER_EMAIL', '')

    # Microsoft Teams Notifications
    TEAMS_WEBHOOK_URL = os.environ.get('TEAMS_WEBHOOK_URL', '')
    ENABLE_TEAMS_NOTIFICATIONS = os.environ.get('ENABLE_TEAMS_NOTIFICATIONS', 'false').lower() == 'true'

    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    LOG_LEVEL = 'DEBUG'


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}


def get_config(config_name=None):
    """Get configuration object"""
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    config_class = config.get(config_name, DevelopmentConfig)
    # Return an instance of the config class
    return config_class()