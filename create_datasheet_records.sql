-- SQL script to create the 'datasheet_records' table if it is not present in your database.
-- Run this against your database (e.g. test_plan_generator) after restoring Dump.sql if needed.

CREATE TABLE IF NOT EXISTS `datasheet_records` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `planner_entry_id` INT NULL,
  `test_request_id` INT NULL,
  `test_code` VARCHAR(20) NULL,
  `tco_id` VARCHAR(50) NULL,
  `job_number` VARCHAR(100) NULL,
  `eut_name` VARCHAR(255) NULL,
  `eut_model_sku` VARCHAR(100) NULL,
  `eut_serial_number` VARCHAR(100) NULL,
  `test_date` DATE NULL,
  `result` VARCHAR(30) NULL,
  `tested_by_name` VARCHAR(200) NULL,
  `tested_by_user_id` INT NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'Not Submitted',
  `form_json` LONGTEXT NULL,
  `images_json` TEXT NULL,
  `generated_file_path` VARCHAR(500) NULL,
  `created_by_user_id` INT NULL,
  `created_at` DATETIME NULL,
  `updated_at` DATETIME NULL,
  UNIQUE KEY `uq_ds_planner` (`planner_entry_id`),
  KEY `idx_ds_tco` (`tco_id`),
  KEY `idx_ds_status` (`status`),
  KEY `idx_ds_testcode` (`test_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
