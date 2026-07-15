#!/usr/bin/env python
import sys
sys.path.insert(0, '.')
from mysql_config import MySQLConfig
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Create connection
cfg = MySQLConfig()
db_uri = f"mysql+pymysql://{cfg.MYSQL_USER}:{cfg.MYSQL_PASSWORD}@{cfg.MYSQL_HOST}:{cfg.MYSQL_PORT}/{cfg.MYSQL_DATABASE}"
engine = create_engine(db_uri)
Session = sessionmaker(bind=engine)
session = Session()

# Query for TCO-002
result = session.execute(text('SELECT id, tco_id, status FROM iec_emc_requests WHERE tco_id = :tco_id LIMIT 1'), {'tco_id': 'TCO-002'}).fetchone()
if result:
    print(f'Found TCO-002: ID={result[0]}, Status={result[2]}')
    tco_req_id = result[0]
    # Check planner entries
    entries = session.execute(text('SELECT id, test_name, status, start_date, end_date FROM planner_entries WHERE tco_id = :tco_id OR test_request_id = :req_id'), {'tco_id': 'TCO-002', 'req_id': tco_req_id}).fetchall()
    print(f'Planner entries: {len(entries)}')
    for e in entries:
        print(f'  Entry {e[0]}: {e[1]}, Status: {e[2]}, {e[3]} to {e[4]}')
else:
    print('TCO-002 not found')
