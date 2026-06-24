#!/usr/bin/env python3
"""
Improved upload routes with enhanced security and error handling.
"""

import logging
import time
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from werkzeug.exceptions import RequestEntityTooLarge

from .upload_handler import UploadHandler
from models import db, TestRequest
from utils.enhanced_document_processor import EnhancedDocumentProcessor

logger = logging.getLogger(__name__)

# Create Blueprint for upload routes
upload_bp = Blueprint('upload', __name__)


def create_upload_routes(app):
    """Create and register upload routes with the Flask app."""

    # Initialize upload handler
    upload_handler = UploadHandler(
        upload_folder=app.config['UPLOAD_FOLDER'],
        max_file_size=app.config.get('MAX_FILE_SIZE', 50 * 1024 * 1024)
    )

    # Initialize document processor
    document_processor = EnhancedDocumentProcessor(
        upload_folder=app.config['UPLOAD_FOLDER'],
        template_folder=app.config['TEMPLATE_FOLDER'],
        output_folder=app.config['OUTPUT_FOLDER']
    )

    @upload_bp.route('/upload', methods=['POST'])
    def upload_file():
        """Enhanced file upload with comprehensive validation and security."""
        try:
            # Check if file was uploaded
            if 'file' not in request.files:
                return jsonify({
                    'success': False,
                    'error': 'No file selected',
                    'error_code': 'NO_FILE'
                }), 400

            file = request.files['file']

            # Validate file using UploadHandler
            is_valid, error_message, file_info = upload_handler.validate_file(
                file)

            if not is_valid:
                return jsonify({
                    'success': False,
                    'error': error_message,
                    'error_code': 'VALIDATION_FAILED'
                }), 400

            # Generate unique filename
            unique_filename = upload_handler.generate_unique_filename(
                file_info['secure_filename'])

            # Save file
            save_success, save_error, file_path = upload_handler.save_file(
                file, unique_filename)

            if not save_success:
                return jsonify({
                    'success': False,
                    'error': save_error,
                    'error_code': 'SAVE_FAILED'
                }), 500

            # Create test request record
            test_request = TestRequest(
                filename=unique_filename,
                original_filename=file_info['original_filename'],
                file_size=file_info['size'],
                user_ip=request.remote_addr,
                status='uploaded'
            )

            try:
                db.session.add(test_request)
                db.session.commit()
            except Exception as db_error:
                logger.error(
                    f"Database error creating test request: {db_error}")
                # Clean up saved file if database operation fails
                upload_handler.delete_file(unique_filename)
                return jsonify({
                    'success': False,
                    'error': 'Database error occurred',
                    'error_code': 'DB_ERROR'
                }), 500

            # Process document with enhanced processor
            start_time = time.time()
            processing_result = document_processor.process_document_enhanced(
                unique_filename)
            processing_time = time.time() - start_time

            if processing_result['success']:
                # Update test request with extracted data
                try:
                    test_request.set_extracted_data(
                        processing_result['parsed_data'])
                    test_request.status = 'processed'
                    test_request.processing_time = processing_time
                    db.session.commit()
                except Exception as db_error:
                    logger.error(
                        f"Database error updating test request: {db_error}")
                    # Don't fail the request, but log the error

                return jsonify({
                    'success': True,
                    'message': 'File uploaded and processed successfully',
                    'data': {
                        'request_id': test_request.id,
                        'filename': file_info['original_filename'],
                        'saved_filename': unique_filename,
                        'file_size_mb': round(file_info['size'] / (1024 * 1024), 2),
                        'extracted_data': processing_result['parsed_data'],
                        'validation': processing_result['validation'],
                        'output_filename': processing_result.get('output_filename', ''),
                        'extraction_method': processing_result.get('extraction_method', ''),
                        'processing_time': round(processing_time, 2),
                        'upload_time': file_info['upload_time'].isoformat()
                    }
                })
            else:
                # Update test request with error
                try:
                    test_request.status = 'error'
                    test_request.error_message = processing_result['error']
                    test_request.processing_time = processing_time
                    db.session.commit()
                except Exception as db_error:
                    logger.error(
                        f"Database error updating test request with error: {db_error}")

                return jsonify({
                    'success': False,
                    'error': processing_result['error'],
                    'error_code': 'PROCESSING_FAILED',
                    'data': {
                        'request_id': test_request.id,
                        'filename': file_info['original_filename'],
                        'processing_time': round(processing_time, 2)
                    }
                }), 400

        except RequestEntityTooLarge:
            return jsonify({
                'success': False,
                'error': 'File size exceeds the maximum limit',
                'error_code': 'FILE_TOO_LARGE'
            }), 413
        except Exception as e:
            logger.error(f"Unexpected error in upload_file: {e}")
            return jsonify({
                'success': False,
                'error': 'An unexpected error occurred while processing the file',
                'error_code': 'UNEXPECTED_ERROR'
            }), 500

    @upload_bp.route('/upload/stats', methods=['GET'])
    def upload_stats():
        """Get upload statistics."""
        try:
            stats = upload_handler.get_upload_stats()
            return jsonify({
                'success': True,
                'data': stats
            })
        except Exception as e:
            logger.error(f"Error getting upload stats: {e}")
            return jsonify({
                'success': False,
                'error': 'Error retrieving upload statistics'
            }), 500

    @upload_bp.route('/upload/cleanup', methods=['POST'])
    def cleanup_uploads():
        """Clean up old uploaded files."""
        try:
            data = request.get_json() or {}
            max_age_hours = data.get('max_age_hours', 24)

            cleaned_count = upload_handler.cleanup_old_files(max_age_hours)

            return jsonify({
                'success': True,
                'message': f'Cleaned up {cleaned_count} old files',
                'data': {
                    'cleaned_count': cleaned_count,
                    'max_age_hours': max_age_hours
                }
            })
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return jsonify({
                'success': False,
                'error': 'Error during cleanup process'
            }), 500

    @upload_bp.route('/upload/file/<filename>', methods=['DELETE'])
    def delete_uploaded_file(filename):
        """Delete a specific uploaded file."""
        try:
            # Validate filename for security
            if not filename or '..' in filename or '/' in filename:
                return jsonify({
                    'success': False,
                    'error': 'Invalid filename'
                }), 400

            success = upload_handler.delete_file(filename)

            if success:
                return jsonify({
                    'success': True,
                    'message': f'File {filename} deleted successfully'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'File not found or could not be deleted'
                }), 404

        except Exception as e:
            logger.error(f"Error deleting file {filename}: {e}")
            return jsonify({
                'success': False,
                'error': 'Error deleting file'
            }), 500

    @upload_bp.route('/upload/file/<filename>/info', methods=['GET'])
    def get_file_info(filename):
        """Get information about a specific uploaded file."""
        try:
            # Validate filename for security
            if not filename or '..' in filename or '/' in filename:
                return jsonify({
                    'success': False,
                    'error': 'Invalid filename'
                }), 400

            file_info = upload_handler.get_file_info(filename)

            if file_info:
                return jsonify({
                    'success': True,
                    'data': file_info
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'File not found'
                }), 404

        except Exception as e:
            logger.error(f"Error getting file info for {filename}: {e}")
            return jsonify({
                'success': False,
                'error': 'Error retrieving file information'
            }), 500

    # Register the blueprint with the app
    app.register_blueprint(upload_bp, url_prefix='/api')

    return upload_bp
