import os
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, date

logger = logging.getLogger(__name__)


class EquipmentManager:
    """Class for managing equipment database operations."""

    def __init__(self, equipment_data_file: str):
        """Initialize the equipment manager.

        Args:
            equipment_data_file: Path to the equipment data JSON file
        """
        self.equipment_data_file = equipment_data_file

    def load_equipment_from_json(self) -> List[Dict[str, Any]]:
        """Load equipment data from JSON file.

        Returns:
            List of equipment dictionaries
        """
        if not os.path.exists(self.equipment_data_file):
            logger.warning(
                f"Equipment data file not found: {self.equipment_data_file}")
            return []

        try:
            with open(self.equipment_data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('equipment', [])
        except Exception as e:
            logger.error(f"Error loading equipment data: {e}")
            return []

    def save_equipment_to_json(self, equipment_list: List[Dict[str, Any]]) -> bool:
        """Save equipment data to JSON file.

        Args:
            equipment_list: List of equipment dictionaries

        Returns:
            True if successful, False otherwise
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(
                self.equipment_data_file), exist_ok=True)

            data = {
                'equipment': equipment_list,
                'last_updated': datetime.now().isoformat()
            }

            with open(self.equipment_data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)

            logger.info(f"Equipment data saved to {self.equipment_data_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving equipment data: {e}")
            return False

    def find_equipment_by_requirements(self, equipment_list: List[str]) -> List[Dict[str, Any]]:
        """Find equipment based on requirements list.

        Args:
            equipment_list: List of equipment names or IDs to find

        Returns:
            List of equipment dictionaries that match the requirements
        """
        try:
            # Load equipment from JSON file
            all_equipment = self.load_equipment_from_json()

            # Filter equipment based on requirements
            matching_equipment = []
            for equipment in all_equipment:
                if (equipment.get('name') in equipment_list or
                    equipment.get('id') in equipment_list or
                        equipment.get('model') in equipment_list):
                    matching_equipment.append(equipment)

            return matching_equipment
        except Exception as e:
            logger.error(f"Error finding equipment by requirements: {e}")
            return []

    def initialize_equipment_database(self) -> bool:
        """Initialize the equipment database with sample data.

        Returns:
            True if successful, False otherwise
        """
        try:
            # This method is now deprecated since we use MySQL directly
            # Equipment is managed through the Flask app routes
            logger.info(
                "Equipment database initialization skipped - using MySQL directly")
            return True
        except Exception as e:
            logger.error(f"Error initializing equipment database: {e}")
            return False

    def get_sample_equipment_data(self) -> List[Dict[str, Any]]:
        """Get sample equipment data for testing.

        Returns:
            List of sample equipment dictionaries
        """
        return [
            {
                'name': 'Digital Multimeter',
                'model': 'Fluke 87V',
                'serial_number': 'DM001',
                'manufacturer': 'Fluke',
                'specifications': 'DC Voltage: 0.1mV to 1000V, AC Voltage: 0.1mV to 1000V, Resistance: 0.1Ω to 50MΩ',
                'calibration_date': '2024-01-15',
                'next_calibration': '2025-01-15',
                'status': 'available',
                'location': 'Lab A',
                'notes': 'High precision multimeter for electrical measurements'
            },
            {
                'name': 'Oscilloscope',
                'model': 'Tektronix TBS1102B',
                'serial_number': 'OSC001',
                'manufacturer': 'Tektronix',
                'specifications': '100 MHz bandwidth, 2 channels, 1 GS/s sample rate',
                'calibration_date': '2024-02-01',
                'next_calibration': '2025-02-01',
                'status': 'available',
                'location': 'Lab A',
                'notes': 'Digital storage oscilloscope for signal analysis'
            }
        ]
