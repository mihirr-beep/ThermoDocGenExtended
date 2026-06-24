#!/usr/bin/env python3
"""
Script to clear equipment table and import data from db-equipment.csv

This script:
1. Clears all existing rows from the equipment table
2. Reads db-equipment.csv
3. Parses and imports all equipment data into MySQL
"""

import sys
import os
import csv
import re
from datetime import datetime
from typing import Optional

# Add the parent directory to the path to import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pymysql
    from pymysql.cursors import DictCursor
    from pymysql.err import OperationalError, ProgrammingError
except ImportError:
    print("[ERROR] PyMySQL not installed. Please install it: pip install PyMySQL")
    sys.exit(1)

try:
    from mysql_config import MySQLConfig
except ImportError:
    try:
        from mysql_config import config as mysql_config_dict
        # Create a simple config class if only dict is available
        class MySQLConfig:
            MYSQL_HOST = mysql_config_dict.get('MYSQL_HOST', 'localhost')
            MYSQL_PORT = mysql_config_dict.get('MYSQL_PORT', 3306)
            MYSQL_USER = mysql_config_dict.get('MYSQL_USER', 'root')
            MYSQL_PASSWORD = mysql_config_dict.get('MYSQL_PASSWORD', '')
            MYSQL_DATABASE = mysql_config_dict.get('MYSQL_DATABASE', 'test_plan_generator')
    except ImportError:
        print("[ERROR] Could not import mysql_config")
        sys.exit(1)


def parse_date(date_str: Optional[str]) -> Optional[str]:
    """Parse various date formats to MySQL DATE format (YYYY-MM-DD)."""
    if not date_str or date_str.strip() in ['', 'NA', 'NULL', 'None']:
        return None
    
    date_str = date_str.strip()
    
    # Try different date formats
    date_formats = [
        '%d-%m-%Y',      # 21-03-2026
        '%d/%m/%Y',      # 12/3/2026
        '%m/%d/%Y',      # 4/11/2025
        '%Y-%m-%d',      # 2026-03-21
        '%d-%b-%y',      # 15-Sep-25
        '%d-%b-%Y',      # 15-Sep-2025
        '%Y-%m-%d %H:%M:%S',  # 2025-04-11 12:37:00
    ]
    
    for fmt in date_formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    
    # If all formats fail, try to extract date parts manually
    try:
        # Handle formats like "21-03-2026"
        if '-' in date_str and len(date_str.split('-')) == 3:
            parts = date_str.split('-')
            if len(parts[2]) == 4:  # Year is 4 digits
                day, month, year = parts
                return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        # Handle formats like "12/3/2026"
        elif '/' in date_str and len(date_str.split('/')) == 3:
            parts = date_str.split('/')
            if len(parts[2]) == 4:  # Year is 4 digits
                if len(parts[0]) <= 2:  # First part is day
                    month, day, year = parts
                else:  # First part is year
                    year, month, day = parts
                return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    except Exception:
        pass
    
    print(f"[WARNING] Could not parse date: {date_str}")
    return None


def parse_datetime(datetime_str: Optional[str]) -> Optional[str]:
    """Parse datetime string to MySQL DATETIME format."""
    if not datetime_str or datetime_str.strip() in ['', 'NA', 'NULL', 'None']:
        return None
    
    datetime_str = datetime_str.strip()
    
    # Try different datetime formats
    datetime_formats = [
        '%m/%d/%Y %H:%M',      # 4/11/2025 12:37
        '%d/%m/%Y %H:%M',      # 11/4/2025 12:37
        '%Y-%m-%d %H:%M:%S',   # 2025-04-11 12:37:00
        '%d-%m-%Y %H:%M',      # 11-04-2025 12:37
    ]
    
    for fmt in datetime_formats:
        try:
            dt = datetime.strptime(datetime_str, fmt)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    
    # If datetime parsing fails, try date parsing
    date_only = parse_date(datetime_str)
    if date_only:
        return f"{date_only} 00:00:00"
    
    return None


def clean_value(value: Optional[str]) -> Optional[str]:
    """Clean and normalize CSV values."""
    if not value:
        return None
    
    value = str(value).strip()
    
    if value in ['', 'NA', 'NULL', 'None', '#VALUE!']:
        return None
    
    return value


def normalize_calibration_frequency(value: Optional[str]) -> Optional[str]:
    """Normalize calibration_frequency values."""
    if not value:
        return None
    
    value = str(value).strip()
    
    # Normalize "Every Year" to "Annual"
    if value.lower() == 'every year':
        return 'Annual'
    
    # Normalize "For Every 2 years" to "Bi-Annual"
    if value.lower() == 'for every 2 years':
        return 'Bi-Annual'
    
    # Normalize "Bi-annual" to "Bi-Annual"
    if value.lower() == 'bi-annual':
        return 'Bi-Annual'
    
    return value


def clear_equipment_table(cursor):
    """Clear all rows from equipment table."""
    try:
        print("[INFO] Clearing all existing equipment records...")
        
        # First, delete from tables that reference equipment (foreign key constraints)
        try:
            print("[INFO] Deleting related records from equipment_history...")
            cursor.execute("DELETE FROM equipment_history")
            print("[OK] Cleared equipment_history records")
        except Exception as e:
            print(f"[WARNING] Could not clear equipment_history: {e}")
        
        try:
            print("[INFO] Deleting related records from maintenance...")
            cursor.execute("DELETE FROM maintenance")
            print("[OK] Cleared maintenance records")
        except Exception as e:
            print(f"[WARNING] Could not clear maintenance: {e}")
        
        # Now delete from equipment table
        cursor.execute("DELETE FROM equipment")
        print(f"[OK] Cleared all equipment records")
        return True
    except Exception as e:
        print(f"[ERROR] Error clearing equipment table: {e}")
        return False


def import_equipment_csv(csv_file_path: str):
    """Import equipment data from CSV file."""
    connection = None
    try:
        # Get database configuration
        db_config = MySQLConfig()
        host = db_config.MYSQL_HOST
        user = db_config.MYSQL_USER
        password = db_config.MYSQL_PASSWORD
        database = db_config.MYSQL_DATABASE
        
        print(f"[INFO] Connecting to MySQL database: {database} on {host}")
        
        # Connect to database
        connection = pymysql.connect(
            host=host,
            user=user,
            password=password,
            database=database,
            cursorclass=DictCursor,
            charset='utf8mb4'
        )
        
        print("[OK] Successfully connected to MySQL database")
        
        with connection.cursor() as cursor:
            # Clear existing equipment
            if not clear_equipment_table(cursor):
                return False
            
            # Ensure test_name column exists
            try:
                cursor.execute("""
                    SELECT COLUMN_NAME 
                    FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_SCHEMA = %s 
                    AND TABLE_NAME = 'equipment' 
                    AND COLUMN_NAME = 'test_name'
                """, (database,))
                if not cursor.fetchone():
                    print("[INFO] Adding test_name column...")
                    cursor.execute("""
                        ALTER TABLE equipment 
                        ADD COLUMN test_name VARCHAR(500) NULL 
                        COMMENT 'Test Name (comma-separated values or single value)'
                    """)
                    print("[OK] Added test_name column")
            except Exception as e:
                print(f"[WARNING] Error checking/adding test_name column: {e}")
            
            # Read CSV file
            if not os.path.exists(csv_file_path):
                print(f"[ERROR] CSV file not found: {csv_file_path}")
                return False
            
            print(f"[INFO] Reading CSV file: {csv_file_path}")
            
            # Read CSV with proper handling of multi-line fields
            equipment_records = []
            with open(csv_file_path, 'r', encoding='utf-8') as f:
                # Use csv.DictReader to handle quoted fields and multi-line values
                reader = csv.DictReader(f)
                
                for row_num, row in enumerate(reader, start=2):  # Start at 2 (row 1 is header)
                    try:
                        # Skip empty rows
                        if not row.get('id') or row.get('id').strip() == '':
                            continue
                        
                        # Parse and clean values
                        equipment_data = {
                            'sl_no': int(row['sl_no']) if clean_value(row.get('sl_no')) and row.get('sl_no').isdigit() else None,
                            'asset_id': clean_value(row.get('asset_id')),
                            'type': clean_value(row.get('type')),
                            'calibration_status_col': clean_value(row.get('calibration_status_col')),
                            'name': clean_value(row.get('name')),
                            'make': clean_value(row.get('make')),
                            'model_no': clean_value(row.get('model_no')),
                            'serial_no': clean_value(row.get('serial_no')),
                            'location': clean_value(row.get('location')),
                            'test_type': clean_value(row.get('test_type')),
                            'calibration_required': clean_value(row.get('calibration_required')),
                            'calibration_frequency': normalize_calibration_frequency(row.get('calibration_frequency')),
                            'calibration_date': parse_date(row.get('calibration_date')),
                            'calibration_due_date': parse_date(row.get('calibration_due_date')),
                            'ic_required': clean_value(row.get('ic_required')),
                            'ic_date': parse_date(row.get('ic_date')),
                            'ic_due_date': parse_date(row.get('ic_due_date')),
                            'maintenance_required': clean_value(row.get('maintenance_required')),
                            'maintenance_date': parse_date(row.get('maintenance_date')),
                            'maintenance_due_date': parse_date(row.get('maintenance_due_date')),
                            'manufacturer_calibration_params': clean_value(row.get('manufacturer_calibration_params')),
                            'calibration_agency_params': clean_value(row.get('calibration_agency_params')),
                            'document_link': clean_value(row.get('document_link')),
                            'test_name': clean_value(row.get('Test Name')),  # Map "Test Name" column to test_name
                            'status': clean_value(row.get('status')) or 'Active',
                            'created_at': parse_datetime(row.get('created_at')) or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'updated_at': parse_datetime(row.get('updated_at')) or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        }
                        
                        equipment_records.append(equipment_data)
                        
                    except Exception as e:
                        print(f"[WARNING] Error parsing row {row_num}: {e}")
                        print(f"  Row data: {row}")
                        continue
            
            print(f"[INFO] Parsed {len(equipment_records)} equipment records from CSV")
            
            # Insert records into database
            if equipment_records:
                print("[INFO] Inserting equipment records into database...")
                
                insert_sql = """
                    INSERT INTO equipment (
                        sl_no, asset_id, type, calibration_status_col, name, make, model_no,
                        serial_no, location, test_type, calibration_required, calibration_frequency,
                        calibration_date, calibration_due_date, ic_required, ic_date, ic_due_date,
                        maintenance_required, maintenance_date, maintenance_due_date,
                        manufacturer_calibration_params, calibration_agency_params, document_link,
                        test_name, status, created_at, updated_at
                    ) VALUES (
                        %(sl_no)s, %(asset_id)s, %(type)s, %(calibration_status_col)s, %(name)s,
                        %(make)s, %(model_no)s, %(serial_no)s, %(location)s, %(test_type)s,
                        %(calibration_required)s, %(calibration_frequency)s, %(calibration_date)s,
                        %(calibration_due_date)s, %(ic_required)s, %(ic_date)s, %(ic_due_date)s,
                        %(maintenance_required)s, %(maintenance_date)s, %(maintenance_due_date)s,
                        %(manufacturer_calibration_params)s, %(calibration_agency_params)s,
                        %(document_link)s, %(test_name)s, %(status)s, %(created_at)s, %(updated_at)s
                    )
                """
                
                inserted_count = 0
                for record in equipment_records:
                    try:
                        cursor.execute(insert_sql, record)
                        inserted_count += 1
                    except Exception as e:
                        print(f"[WARNING] Error inserting equipment {record.get('asset_id', 'unknown')}: {e}")
                        continue
                
                connection.commit()
                print(f"[OK] Successfully inserted {inserted_count} equipment records")
                print(f"\n[OK] Import completed successfully!")
                return True
            else:
                print("[WARNING] No equipment records to insert")
                return False
                
    except OperationalError as e:
        print(f"[ERROR] Database operational error: {e}")
        if connection:
            connection.rollback()
        return False
    except Exception as e:
        print(f"[ERROR] Error importing equipment data: {e}")
        import traceback
        traceback.print_exc()
        if connection:
            connection.rollback()
        return False
    finally:
        if connection:
            connection.close()
            print("[INFO] Database connection closed")


if __name__ == '__main__':
    csv_file = 'db-equipment.csv'
    
    if len(sys.argv) > 1:
        csv_file = sys.argv[1]
    
    print("=" * 60)
    print("Equipment CSV Import Script")
    print("=" * 60)
    print(f"CSV File: {csv_file}")
    print()
    
    success = import_equipment_csv(csv_file)
    sys.exit(0 if success else 1)

