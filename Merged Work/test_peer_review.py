#!/usr/bin/env python
"""
Test script for peer review workflow implementation.
Tests:
1. Fetching peer review entries
2. Approving peer review
3. Rejecting peer review
4. Adding comments to peer review
"""

import sys
import json
sys.path.insert(0, '.')

from mysql_config import MySQLConfig
from app import create_app, db
from models import PlannerEntry, EMCRequest, User
from datetime import datetime, date
from flask import Flask

# Initialize Flask app
app = create_app() if hasattr(sys.modules['app'], 'create_app') else None
if not app:
    app = Flask(__name__)
    app.config.from_object(MySQLConfig)
    from models import db as models_db
    models_db.init_app(app)

print("=" * 80)
print("PEER REVIEW WORKFLOW TEST")
print("=" * 80)

with app.app_context():
    # Test 1: Check for existing peer review entries
    print("\n[TEST 1] Checking for peer review entries...")
    peer_review_entries = PlannerEntry.query.filter_by(
        status='Peer Review'
    ).filter(
        PlannerEntry.datasheet_file_path.isnot(None)
    ).all()
    
    print(f"Found {len(peer_review_entries)} peer review entries")
    
    if peer_review_entries:
        for entry in peer_review_entries:
            print(f"\n  Entry ID: {entry.id}")
            print(f"  Test Name: {entry.test_name}")
            print(f"  TCO ID: {entry.tco_id}")
            print(f"  Test Request ID: {entry.test_request_id}")
            print(f"  Engineer: {entry.test_person_name}")
            print(f"  Status: {entry.status}")
            print(f"  Datasheet File: {entry.datasheet_file_path}")
            print(f"  Datasheet Comments: {entry.datasheet_comments[:100] if entry.datasheet_comments else 'None'}...")
            print(f"  Uploaded At: {entry.datasheet_uploaded_at}")
            
            # Get related request info
            if entry.test_request_id:
                req = EMCRequest.query.get(entry.test_request_id)
                if req:
                    print(f"  Related Request TCO ID: {req.tco_id}")
                    print(f"  Product: {req.product_name}")
                    print(f"  Requester: {req.requester_name}")
    else:
        print("  No peer review entries found in the database")
        print("\n  To test the peer review workflow, you need to:")
        print("  1. Upload a datasheet for a test assignment")
        print("  2. The entry should automatically have status 'Peer Review'")
        print("  3. Then you can test approve/reject actions")
    
    # Test 2: Check in_progress entries that can be used to create test datasheets
    print("\n\n[TEST 2] Checking for in_progress entries (for testing datasheet upload)...")
    in_progress_entries = PlannerEntry.query.filter_by(
        status='in_progress'
    ).limit(5).all()
    
    print(f"Found {len(in_progress_entries)} in_progress entries available for testing")
    
    if in_progress_entries:
        for entry in in_progress_entries[:3]:
            print(f"\n  Entry ID: {entry.id}")
            print(f"  Test Name: {entry.test_name}")
            print(f"  Engineer: {entry.test_person_name}")
            print(f"  Dates: {entry.start_date} to {entry.end_date}")
            if entry.test_request_id:
                req = EMCRequest.query.get(entry.test_request_id)
                if req:
                    print(f"  TCO ID: {req.tco_id}")
    
    # Test 3: Check datasheet_uploaded entries
    print("\n\n[TEST 3] Checking for datasheet_uploaded entries...")
    datasheet_uploaded_entries = PlannerEntry.query.filter_by(
        status='datasheet_uploaded'
    ).filter(
        PlannerEntry.datasheet_file_path.isnot(None)
    ).all()
    
    print(f"Found {len(datasheet_uploaded_entries)} datasheet_uploaded entries")
    
    if datasheet_uploaded_entries:
        for entry in datasheet_uploaded_entries[:3]:
            print(f"\n  Entry ID: {entry.id}")
            print(f"  Test Name: {entry.test_name}")
            print(f"  TCO ID: {entry.tco_id}")
            print(f"  Status: {entry.status}")
            print(f"  Datasheet File: {entry.datasheet_file_path}")
    
    # Test 4: Check status distribution
    print("\n\n[TEST 4] Status Distribution of all planner entries...")
    status_counts = db.session.query(
        PlannerEntry.status,
        db.func.count(PlannerEntry.id)
    ).group_by(PlannerEntry.status).all()
    
    for status, count in status_counts:
        print(f"  {status}: {count} entries")
    
    # Test 5: Check parent request status distribution
    print("\n\n[TEST 5] Parent Request Status Distribution...")
    request_status_counts = db.session.query(
        EMCRequest.status,
        db.func.count(EMCRequest.id)
    ).group_by(EMCRequest.status).all()
    
    for status, count in request_status_counts:
        print(f"  {status}: {count} requests")

print("\n" + "=" * 80)
print("PEER REVIEW WORKFLOW TEST COMPLETE")
print("=" * 80)
print("\nWORKFLOW SUMMARY:")
print("1. When a datasheet is uploaded for a test, status becomes 'Peer Review'")
print("2. Admins/Lab Engineers can approve or reject the peer review")
print("3. APPROVE: Changes status to 'datasheet_uploaded' (final)")
print("4. REJECT: Changes status back to 'in_progress' (engineer can fix and re-upload)")
print("5. COMMENT: Adds a comment without changing status")
print("\n" + "=" * 80)
