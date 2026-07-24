import os
import pymysql

# Custom lightweight .env parser to avoid external dependencies
def load_env():
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    parts = line.split("=", 1)
                    k = parts[0].strip()
                    v = parts[1].strip()
                    os.environ[k] = v

load_env()

host = os.getenv("MYSQL_HOST", "localhost")
port = int(os.getenv("MYSQL_PORT", 3306))
user = os.getenv("MYSQL_USER", "root")
password = os.getenv("MYSQL_PASSWORD", "root")
db = "test_plan_generator"

# SQL DDL to create datasheet_records table
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS datasheet_records (
  id INT AUTO_INCREMENT PRIMARY KEY,
  planner_entry_id INT NULL,
  test_request_id INT NULL,
  test_code VARCHAR(20) NULL,
  tco_id VARCHAR(50) NULL,
  job_number VARCHAR(100) NULL,
  eut_name VARCHAR(255) NULL,
  eut_model_sku VARCHAR(100) NULL,
  eut_serial_number VARCHAR(100) NULL,
  test_date DATE NULL,
  result VARCHAR(30) NULL,
  tested_by_name VARCHAR(200) NULL,
  tested_by_user_id INT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'Not Submitted',
  form_json LONGTEXT NULL,
  images_json TEXT NULL,
  generated_file_path VARCHAR(500) NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NULL,
  updated_at DATETIME NULL,
  UNIQUE KEY uq_ds_planner (planner_entry_id),
  KEY idx_ds_tco (tco_id),
  KEY idx_ds_status (status),
  KEY idx_ds_testcode (test_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

try:
    print(f"Connecting to database '{db}' on {host}:{port}...")
    connection = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db,
        cursorclass=pymysql.cursors.DictCursor
    )
    
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        tables = [list(t.values())[0] for t in cursor.fetchall()]
        
        if "datasheet_records" in tables:
            print("Table 'datasheet_records' already exists in the database. No action needed.")
        else:
            print("Table 'datasheet_records' is missing. Creating table now...")
            cursor.execute(CREATE_TABLE_SQL)
            connection.commit()
            print("Table 'datasheet_records' successfully created!")
            
    connection.close()

except Exception as e:
    print(f"Database error occurred: {e}")
