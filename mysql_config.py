import hashlib
import os
from datetime import timedelta


def _build_default_secret_key() -> str:
    """Create a stable, app-scoped development secret key.

    This avoids cookie/session collisions with other local Flask apps that may
    still be using Flask's default cookie names and a shared fallback secret.
    """
    scope = os.path.abspath(__file__)
    digest = hashlib.sha256(scope.encode('utf-8')).hexdigest()
    return f'dev-secret-{digest}'


class MySQLConfig:
    """MySQL configuration class for the Flask application."""

    # Flask Configuration
    SECRET_KEY = os.environ.get('SECRET_KEY') or _build_default_secret_key()
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'

    # MySQL Database Configuration
    MYSQL_HOST = os.environ.get('MYSQL_HOST', 'localhost')
    MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 3306))
    MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', 'Thermo@123')
    MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE', 'test_plan_generator')

    # SQLAlchemy MySQL URI will be constructed dynamically
    SQLALCHEMY_DATABASE_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True
    }

    # File Upload Configuration
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max file size
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB max file size for enhanced upload handler
    UPLOAD_FOLDER = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), 'uploads')
    OUTPUT_FOLDER = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), 'outputs')
    TEMPLATE_FOLDER = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), 'templates')
    EQUIPMENT_FOLDER = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), 'equipment')

    # Allowed file extensions
    ALLOWED_EXTENSIONS = {'docx', 'doc'}

    # Session Configuration
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)
    SESSION_COOKIE_NAME = os.environ.get(
        'SESSION_COOKIE_NAME', 'test_workflow_final_session')
    REMEMBER_COOKIE_NAME = os.environ.get(
        'REMEMBER_COOKIE_NAME', 'test_workflow_final_remember')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    SESSION_REFRESH_EACH_REQUEST = True

    # Logging Configuration
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FILE = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), 'logs', 'app.log')

    # Document Processing Configuration
    MAX_PROCESSING_TIME = 300  # 5 minutes
    TEMP_FILE_CLEANUP_INTERVAL = 3600  # 1 hour

    # Equipment Database Configuration
    EQUIPMENT_DATA_FILE = os.path.join(EQUIPMENT_FOLDER, 'equipment_data.json')

    # Template Configuration
    TEST_PLAN_TEMPLATE = os.path.join(
        TEMPLATE_FOLDER, 'test_plan_template.docx')
    DATASHEET_TEMPLATE = os.path.join(
        TEMPLATE_FOLDER, 'datasheet_template.docx')

    @staticmethod
    def init_app(app):
        """Initialize application with configuration."""
        # Create necessary directories
        for folder in [app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER'],
                       app.config['TEMPLATE_FOLDER'], app.config['EQUIPMENT_FOLDER']]:
            os.makedirs(folder, exist_ok=True)

        # Set up MySQL URI using mysqlclient (mysql+mysqldb)
        from urllib.parse import quote_plus
        mysql_config = app.config
        encoded_password = quote_plus(
            mysql_config['MYSQL_PASSWORD']) if mysql_config['MYSQL_PASSWORD'] else ''
        app.config['SQLALCHEMY_DATABASE_URI'] = (
            f"mysql+mysqldb://{mysql_config['MYSQL_USER']}:{encoded_password}"
            f"@{mysql_config['MYSQL_HOST']}:{mysql_config['MYSQL_PORT']}"
            f"/{mysql_config['MYSQL_DATABASE']}"
        )


class MySQLDevelopmentConfig(MySQLConfig):
    """Development configuration for MySQL."""
    DEBUG = True
    LOG_LEVEL = 'DEBUG'


class MySQLProductionConfig(MySQLConfig):
    """Production configuration for MySQL."""
    DEBUG = False
    LOG_LEVEL = 'WARNING'


class MySQLTestingConfig(MySQLConfig):
    """Testing configuration for MySQL."""
    TESTING = True
    MYSQL_DATABASE = 'test_plan_generator_test'
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+mysqldb://{MySQLConfig.MYSQL_USER}:{MySQLConfig.MYSQL_PASSWORD}"
        f"@{MySQLConfig.MYSQL_HOST}:{MySQLConfig.MYSQL_PORT}/test_plan_generator_test"
    )


# Configuration dictionary
config = {
    'development': MySQLDevelopmentConfig,
    'production': MySQLProductionConfig,
    'testing': MySQLTestingConfig,
    'default': MySQLDevelopmentConfig
}
