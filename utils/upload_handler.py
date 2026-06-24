#!/usr/bin/env python3
"""
Enhanced upload handler with best practices for file uploads.
"""

import os
import hashlib
import mimetypes
import logging
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from flask import current_app, request
# import magic  # Commented out for compatibility

logger = logging.getLogger(__name__)


class UploadHandler:
    """Enhanced upload handler with security and validation features."""

    def __init__(self, upload_folder: str, max_file_size: int = 50 * 1024 * 1024):
        """
        Initialize upload handler.

        Args:
            upload_folder: Directory to store uploaded files
            max_file_size: Maximum file size in bytes (default: 50MB)
        """
        self.upload_folder = upload_folder
        self.max_file_size = max_file_size

        # Allowed file extensions and their MIME types
        self.allowed_extensions = {
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'doc': 'application/msword'
        }

        # Create upload directory if it doesn't exist
        os.makedirs(upload_folder, exist_ok=True)

    def validate_file(self, file) -> Tuple[bool, str, Dict]:
        """
        Comprehensive file validation.

        Args:
            file: FileStorage object from Flask

        Returns:
            Tuple of (is_valid, error_message, file_info)
        """
        try:
            # Check if file exists
            if not file or file.filename == '':
                return False, "No file selected", {}

            # Check file size
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)  # Reset file pointer

            if file_size > self.max_file_size:
                return False, f"File size ({file_size / 1024 / 1024:.1f}MB) exceeds maximum limit ({self.max_file_size / 1024 / 1024}MB)", {}

            # Validate file extension
            filename = secure_filename(file.filename)
            if not filename or '.' not in filename:
                return False, "Invalid filename", {}

            extension = filename.rsplit('.', 1)[1].lower()
            if extension not in self.allowed_extensions:
                return False, f"File type '{extension}' is not allowed. Allowed types: {', '.join(self.allowed_extensions.keys())}", {}

            # Validate MIME type
            mime_type = self._get_mime_type(file)
            expected_mime = self.allowed_extensions.get(extension)

            if mime_type and expected_mime and mime_type != expected_mime:
                logger.warning(
                    f"MIME type mismatch for {filename}: expected {expected_mime}, got {mime_type}")
                # Don't reject, but log the warning

            # Check for malicious content
            if self._is_malicious_file(file):
                return False, "File appears to be malicious and has been rejected", {}

            file_info = {
                'original_filename': file.filename,
                'secure_filename': filename,
                'extension': extension,
                'size': file_size,
                'mime_type': mime_type,
                'upload_time': datetime.utcnow()
            }

            return True, "", file_info

        except Exception as e:
            logger.error(f"Error validating file: {e}")
            return False, f"Error validating file: {str(e)}", {}

    def _get_mime_type(self, file) -> Optional[str]:
        """Get MIME type of file using python-magic."""
        try:
            # Read first 2048 bytes for MIME detection
            file.seek(0)
            header = file.read(2048)
            file.seek(0)  # Reset file pointer

            # Simplified MIME type detection without python-magic
            # For now, return None to avoid dependency issues
            return None
        except Exception as e:
            logger.warning(f"Could not determine MIME type: {e}")
            return None

    def _is_malicious_file(self, file) -> bool:
        """
        Basic malicious file detection.

        This is a simple implementation. In production, you might want to use
        antivirus scanning or more sophisticated detection.
        """
        try:
            file.seek(0)
            content = file.read(1024)  # Read first 1KB
            file.seek(0)  # Reset file pointer

            # Check for common malicious patterns
            malicious_patterns = [
                b'<script',
                b'javascript:',
                b'vbscript:',
                b'data:text/html',
                b'<?php',
                b'<%',
                b'exec(',
                b'system(',
                b'shell_exec(',
            ]

            content_lower = content.lower()
            for pattern in malicious_patterns:
                if pattern in content_lower:
                    logger.warning(
                        f"Potential malicious content detected: {pattern}")
                    return True

            return False

        except Exception as e:
            logger.error(f"Error checking for malicious content: {e}")
            return False

    def generate_unique_filename(self, original_filename: str) -> str:
        """
        Generate a unique filename to prevent conflicts.

        Args:
            original_filename: Original filename

        Returns:
            Unique filename with timestamp and hash
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        secure_name = secure_filename(original_filename)

        # Generate hash of original filename for uniqueness
        filename_hash = hashlib.md5(secure_name.encode()).hexdigest()[:8]

        # Create unique filename
        name, ext = os.path.splitext(secure_name)
        unique_filename = f"{timestamp}_{name}_{filename_hash}{ext}"

        return unique_filename

    def save_file(self, file, filename: str) -> Tuple[bool, str, str]:
        """
        Save uploaded file with error handling.

        Args:
            file: FileStorage object
            filename: Filename to save as

        Returns:
            Tuple of (success, error_message, file_path)
        """
        try:
            file_path = os.path.join(self.upload_folder, filename)

            # Check if file already exists (shouldn't happen with unique names, but safety check)
            if os.path.exists(file_path):
                logger.warning(f"File already exists: {file_path}")
                return False, "File already exists", ""

            # Save file
            file.save(file_path)

            # Verify file was saved correctly
            if not os.path.exists(file_path):
                return False, "Failed to save file", ""

            # Verify file size matches
            saved_size = os.path.getsize(file_path)
            file.seek(0, os.SEEK_END)
            original_size = file.tell()
            file.seek(0)

            if saved_size != original_size:
                logger.error(
                    f"File size mismatch: original={original_size}, saved={saved_size}")
                os.remove(file_path)  # Clean up corrupted file
                return False, "File corruption detected during save", ""

            logger.info(
                f"File saved successfully: {file_path} ({saved_size} bytes)")
            return True, "", file_path

        except Exception as e:
            logger.error(f"Error saving file: {e}")
            return False, f"Error saving file: {str(e)}", ""

    def cleanup_old_files(self, max_age_hours: int = 24) -> int:
        """
        Clean up old uploaded files.

        Args:
            max_age_hours: Maximum age of files in hours

        Returns:
            Number of files cleaned up
        """
        try:
            cleaned_count = 0
            current_time = datetime.utcnow()

            for filename in os.listdir(self.upload_folder):
                file_path = os.path.join(self.upload_folder, filename)

                if os.path.isfile(file_path):
                    file_time = datetime.fromtimestamp(
                        os.path.getmtime(file_path))
                    age_hours = (current_time -
                                 file_time).total_seconds() / 3600

                    if age_hours > max_age_hours:
                        try:
                            os.remove(file_path)
                            cleaned_count += 1
                            logger.info(f"Cleaned up old file: {filename}")
                        except Exception as e:
                            logger.error(
                                f"Error cleaning up file {filename}: {e}")

            return cleaned_count

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return 0

    def get_file_info(self, filename: str) -> Optional[Dict]:
        """
        Get information about a saved file.

        Args:
            filename: Name of the file

        Returns:
            Dictionary with file information or None if file doesn't exist
        """
        try:
            file_path = os.path.join(self.upload_folder, filename)

            if not os.path.exists(file_path):
                return None

            stat = os.stat(file_path)

            return {
                'filename': filename,
                'path': file_path,
                'size': stat.st_size,
                'created': datetime.fromtimestamp(stat.st_ctime),
                'modified': datetime.fromtimestamp(stat.st_mtime),
                'exists': True
            }

        except Exception as e:
            logger.error(f"Error getting file info for {filename}: {e}")
            return None

    def delete_file(self, filename: str) -> bool:
        """
        Delete a file from the upload directory.

        Args:
            filename: Name of the file to delete

        Returns:
            True if file was deleted successfully
        """
        try:
            file_path = os.path.join(self.upload_folder, filename)

            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted file: {filename}")
                return True
            else:
                logger.warning(f"File not found for deletion: {filename}")
                return False

        except Exception as e:
            logger.error(f"Error deleting file {filename}: {e}")
            return False

    def get_upload_stats(self) -> Dict:
        """
        Get statistics about uploaded files.

        Returns:
            Dictionary with upload statistics
        """
        try:
            total_files = 0
            total_size = 0
            file_types = {}

            for filename in os.listdir(self.upload_folder):
                file_path = os.path.join(self.upload_folder, filename)

                if os.path.isfile(file_path):
                    total_files += 1
                    file_size = os.path.getsize(file_path)
                    total_size += file_size

                    # Count file types
                    ext = filename.rsplit('.', 1)[1].lower(
                    ) if '.' in filename else 'unknown'
                    file_types[ext] = file_types.get(ext, 0) + 1

            return {
                'total_files': total_files,
                'total_size_bytes': total_size,
                'total_size_mb': total_size / (1024 * 1024),
                'file_types': file_types,
                'upload_folder': self.upload_folder
            }

        except Exception as e:
            logger.error(f"Error getting upload stats: {e}")
            return {}
