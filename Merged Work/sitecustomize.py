# Auto-imported by Python at startup (this dir is on sys.path when running
# `python app.py` / `python seed_users.py`). Makes PyMySQL satisfy the
# `mysql+mysqldb://` driver the app's SQLAlchemy URI requests, so the mysqlclient
# C extension is not required.
try:
    import pymysql
    pymysql.install_as_MySQLdb()
except Exception:
    pass
