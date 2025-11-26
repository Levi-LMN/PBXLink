"""
Configuration file for FreePBX Dashboard
Save this as config.py in your application root directory
"""

import os


class Config:
    # Flask settings
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'change-this-to-a-random-secret-key-in-production'

    # Flask app settings
    DEBUG = False
    TESTING = False

    # FreePBX API settings
    FREEPBX_HOST = os.environ.get('FREEPBX_HOST') or 'http://10.200.200.2:80'
    FREEPBX_CLIENT_ID = os.environ.get('FREEPBX_CLIENT_ID') or 'your-client-id-here'
    FREEPBX_CLIENT_SECRET = os.environ.get('FREEPBX_CLIENT_SECRET') or 'your-client-secret-here'

    # SSH settings for Asterisk commands (using password authentication)
    FREEPBX_SSH_USER = os.environ.get('FREEPBX_SSH_USER') or 'root'
    FREEPBX_SSH_PASSWORD = os.environ.get('FREEPBX_SSH_PASSWORD') or 'your-ssh-password-here'
    FREEPBX_SSH_KEY = None  # Not using SSH key, using password instead

    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL') or 'INFO'
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    LOG_LEVEL = 'DEBUG'


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    # In production, all sensitive values should come from environment variables
    SECRET_KEY = os.environ.get('SECRET_KEY')
    FREEPBX_HOST = os.environ.get('FREEPBX_HOST')
    FREEPBX_CLIENT_ID = os.environ.get('FREEPBX_CLIENT_ID')
    FREEPBX_CLIENT_SECRET = os.environ.get('FREEPBX_CLIENT_SECRET')
    FREEPBX_SSH_USER = os.environ.get('FREEPBX_SSH_USER')
    FREEPBX_SSH_PASSWORD = os.environ.get('FREEPBX_SSH_PASSWORD')


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}


# Quick config function
def get_config(config_name=None):
    """Get configuration object"""
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')
    return config.get(config_name, DevelopmentConfig)