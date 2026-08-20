import hashlib
import os
from datetime import timedelta

# Load .env file if it exists in the app directory
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_path):
    with open(_env_path, 'r', encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _key, _val = _line.split('=', 1)
            _key = _key.strip()
            _val = _val.strip().strip("'\"")
            if _key:
                os.environ[_key] = _val

# Register PyMySQL as MySQLdb shim (works without PYTHONPATH)
try:
    import pymysql
    pymysql.install_as_MySQLdb()
except ImportError:
    pass


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
    # NB: kept as 'test_plan_generator'. Her tree defaults to _test because that is
    # the database she develops against; ours holds the live data and .env does not
    # pin MYSQL_DATABASE, so this default is what the app connects to.

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


# One configuration, as she reorganised it: the three environment classes only ever
# differed by DEBUG/LOG_LEVEL and the app used a single one. Exposed as a MAPPING rather
# than the bare class because four callers in this tree read config["default"] -
# nlp_search/build_catalog.py, nlp_search/semantics.py, migrate_iec_emc_to_relational.py
# and import_equipment_csv.py - and app.py resolves config[name]. Every key is the same
# class, so there is nothing left to diverge.
config = {
    'default': MySQLConfig,
    'development': MySQLConfig,
    'testing': MySQLConfig,
    'production': MySQLConfig,
}

# Kept as names for the callers that import a class directly (debug_tco.py, the
# peer-review scripts). They are the same object now, not three behaviours.
MySQLDevelopmentConfig = MySQLConfig
MySQLProductionConfig = MySQLConfig
MySQLTestingConfig = MySQLConfig
