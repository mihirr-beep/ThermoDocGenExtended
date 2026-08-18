# -*- coding: utf-8 -*-
"""Create the datasheet projection tables (the queryable copy of form_json).

WHY A SEPARATE MODULE
---------------------
Same pattern as schema.py / records.py / fixed_store.py: this project has no
migration framework, so each feature owns idempotent DDL that runs at boot and
only ever ADDS things. Nothing here alters or drops an existing table -
``datasheet_records`` is untouched, and dropping every table below restores the
previous state exactly.

WHAT THIS IS FOR
----------------
``datasheet_records.form_json`` stays the source of truth and stays what the
datasheet form reads (one row, one round trip - see docs/Datasheet_Plan_FINAL).
These tables are a DERIVED projection of it, existing purely so the data can be
queried: which tests failed, what ambient temperature CE ran at, which equipment
is in use, who rejected what and why.

The DDL below is generated from the 11 live datasheet schemas - every column
name is a real form key - and was verified by executing it against a throwaway
database (19 tables, 278 columns, 18 foreign keys).
"""
from sqlalchemy import inspect, text

# Order matters: `datasheet` first, everything else references it.
TABLES = (
    "datasheet",
    "datasheet_ce",
    "datasheet_voltagedips",
    "datasheet_voltageflicker",
    "datasheet_harmonic",
    "datasheet_eft",
    "datasheet_esd",
    "datasheet_surge",
    "datasheet_re",
    "datasheet_rs_ri",
    "datasheet_crf",
    "datasheet_pfmf",
    "datasheet_equipment",
    "datasheet_software",
    "datasheet_modification",
    "datasheet_observation",
    "datasheet_measurement",
    "datasheet_observation_legend",
    "datasheet_revision",
    "datasheet_draft_history",
    "datasheet_status_history",
)


_DDL = (
    """CREATE TABLE IF NOT EXISTS `datasheet` (
  `id`               INT AUTO_INCREMENT PRIMARY KEY,
  `planner_entry_id` INT NOT NULL,
  `test_request_id`  INT NULL,
  `test_code`        VARCHAR(20) NOT NULL,
  -- denormalised identity (so most questions need no join)
  `tco_id`                           VARCHAR(50) NULL,
  `job_number`                       VARCHAR(100) NULL,
  `product_name`                     VARCHAR(255) NULL,
  `eut_class`                        VARCHAR(30) NULL,
  `engineer_name`                    VARCHAR(200) NULL,
  `peer_reviewer_name`               VARCHAR(200) NULL,
  -- the 23 fields shared across datasheets
  `ambient_temperature`              VARCHAR(120) NULL,
  `basic_standard`                   TEXT NULL,
  `signoff_date`                     DATE NULL,
  `deviation`                        TEXT NULL,
  `eut_configuration`                VARCHAR(120) NULL,
  `eut_input_voltage_frequency`      VARCHAR(120) NULL,
  `eut_model_sku_number`             VARCHAR(120) NULL,
  `eut_modification_state`           VARCHAR(120) NULL,
  `eut_name`                         VARCHAR(120) NULL,
  `eut_serial_number`                VARCHAR(120) NULL,
  `immunity_test_requirement`        VARCHAR(120) NULL,
  `met_performance_criteria`         VARCHAR(120) NULL,
  `monitoring_parameters`            TEXT NULL,
  `signoff_name`                     VARCHAR(120) NULL,
  `product_standard`                 TEXT NULL,
  `relative_humidity`                VARCHAR(120) NULL,
  `required_performance_criteria`    VARCHAR(120) NULL,
  `sop_reference`                    VARCHAR(120) NULL,
  `test_date`                        DATE NULL,
  `test_mode`                        TEXT NULL,
  `test_port`                        VARCHAR(120) NULL,
  `test_procedure`                   TEXT NULL,
  `tested_by`                        VARCHAR(120) NULL,
  `result`                           VARCHAR(30) NULL,
  -- Every image of this datasheet, keyed by slot: the stored path, the
  -- caption the engineer typed (they can edit it), and the size they set
  -- in the image editor. ONE column, because images are render-only -
  -- never filtered, never aggregated - the slot count varies per test
  -- (2..11), and RE adds further slots dynamically while filling.
  `images_json`                      JSON NULL,
  -- lifecycle
  `status`                           VARCHAR(20) NOT NULL DEFAULT 'Draft',
  `revision_no`                      INT NOT NULL DEFAULT 1,
  `submitted_at`                     DATETIME NULL,
  `decided_at`                       DATETIME NULL,
  `reviewer_user_id`                 INT NULL,
  `created_by_user_id`               INT NULL,
  `created_at`                       DATETIME NOT NULL,
  `updated_at`                       DATETIME NOT NULL,
  UNIQUE KEY `uq_ds_entry` (`planner_entry_id`),
  KEY `idx_ds_code` (`test_code`), KEY `idx_ds_status` (`status`),
  KEY `idx_ds_tco` (`tco_id`), KEY `idx_ds_result` (`result`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_ce` (          -- Conducted Emission
  `datasheet_id` INT PRIMARY KEY,
  -- 24 field(s) unique to this test
  `eut_model`                                          VARCHAR(120) NULL,
  `eut_serial`                                         VARCHAR(120) NULL,
  `measurement_uncertainty`                            VARCHAR(120) NULL,
  `classification_group`                               VARCHAR(120) NULL,
  `classification_class`                               VARCHAR(120) NULL,
  `coupling_method`                                    VARCHAR(120) NULL,
  `frequency_range`                                    VARCHAR(120) NULL,
  `resolution_bandwidth`                               VARCHAR(120) NULL,
  `step_size`                                          VARCHAR(120) NULL,
  `detector`                                           VARCHAR(120) NULL,
  `measurement_time`                                   VARCHAR(120) NULL,
  `eut_voltage_frequency`                              VARCHAR(120) NULL,
  `limit_qp_015_050`                                   VARCHAR(120) NULL,
  `limit_avg_015_050`                                  VARCHAR(120) NULL,
  `limit_qp_050_5`                                     VARCHAR(120) NULL,
  `limit_avg_050_5`                                    VARCHAR(120) NULL,
  `limit_qp_5_30`                                      VARCHAR(120) NULL,
  `limit_avg_5_30`                                     VARCHAR(120) NULL,
  `software_used`                                      VARCHAR(120) NULL,
  `software_version`                                   VARCHAR(120) NULL,
  `result_class`                                       VARCHAR(120) NULL,
  `overall_result`                                     VARCHAR(120) NULL,
  `tested_by_name`                                     VARCHAR(120) NULL,
  `tested_by_date`                                     VARCHAR(120) NULL,
  -- 6 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `line_measurements_json`                             JSON NULL,   -- 8 cols
  `neutral_measurements_json`                          JSON NULL,   -- 8 cols
  CONSTRAINT `fk_datasheet_ce` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_voltagedips` (          -- Voltage Dips & Interruptions
  `datasheet_id` INT PRIMARY KEY,
  -- 3 field(s) unique to this test
  `number_of_dips_interruptions`                       VARCHAR(40) NULL,
  `time_between_dips_interruptions`                    VARCHAR(40) NULL,
  `phase_angle`                                        VARCHAR(44) NULL,
  -- 2 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `test_observation_rows_json`                         JSON NULL,   -- 3 cols
  `obs_dips_json`                                      JSON NULL,
  `obs_interruptions_json`                             JSON NULL,
  CONSTRAINT `fk_datasheet_voltagedip` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_voltageflicker` (          -- Flicker
  `datasheet_id` INT PRIMARY KEY,
  -- 5 field(s) unique to this test
  `name_of_the_test`                                   VARCHAR(46) NULL,
  `voltage_fluctuation_and_flicker_emission`           VARCHAR(40) NULL,
  `short_term_flicker_measurement_time`                VARCHAR(40) NULL,
  `long_term_flicker_measurement_time`                 VARCHAR(40) NULL,
  `overall_result`                                     VARCHAR(40) NULL,
  -- 2 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `flicker_fc_rows_json`                               JSON NULL,   -- 6 cols
  `flicker_limits_rows_json`                           JSON NULL,   -- 2 cols
  `flicker_meas_rows_json`                             JSON NULL,   -- 3 cols
  CONSTRAINT `fk_datasheet_voltagefli` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_harmonic` (          -- Harmonics
  `datasheet_id` INT PRIMARY KEY,
  -- 8 field(s) unique to this test
  `name_of_the_test`                                   VARCHAR(46) NULL,
  `harmonic_current_emission`                          VARCHAR(40) NULL,
  `classification`                                     VARCHAR(40) NULL,
  `frequency_range`                                    VARCHAR(40) NULL,
  `maximum_harmonics`                                  VARCHAR(40) NULL,
  `measurement_time`                                   VARCHAR(40) NULL,
  `overall_result`                                     VARCHAR(40) NULL,
  `result_class`                                       VARCHAR(40) NULL,
  -- 2 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `harmonic_avgmax_rows_json`                          JSON NULL,   -- 10 cols
  `harmonic_rows_json`                                 JSON NULL,   -- 4 cols
  CONSTRAINT `fk_datasheet_harmonic` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_eft` (          -- Electrical Fast Transient
  `datasheet_id` INT PRIMARY KEY,
  -- 8 field(s) unique to this test
  `test_voltage_power_line`                            VARCHAR(40) NULL,
  `test_voltage_signal_line`                           VARCHAR(40) NULL,
  `pulse_rise_time`                                    VARCHAR(40) NULL,
  `pulse_width`                                        VARCHAR(40) NULL,
  `burst_duration`                                     VARCHAR(40) NULL,
  `burst_period`                                       VARCHAR(40) NULL,
  `pulse_repetition_frequency`                         VARCHAR(40) NULL,
  `test_duration`                                      VARCHAR(40) NULL,
  -- 6 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `test_observation_power_rows_json`                   JSON NULL,   -- 9 cols
  `test_observation_signal_rows_json`                  JSON NULL,   -- 9 cols
  `obs_power_json`                                     JSON NULL,
  `obs_signal_json`                                    JSON NULL,
  CONSTRAINT `fk_datasheet_eft` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_esd` (          -- Electrostatic Discharge
  `datasheet_id` INT PRIMARY KEY,
  -- 6 field(s) unique to this test
  `rc_network`                                         VARCHAR(40) NULL,
  `direct_contact_discharge`                           VARCHAR(40) NULL,
  `indirect_hcp`                                       VARCHAR(40) NULL,
  `indirect_vcp`                                       VARCHAR(40) NULL,
  `air_discharge`                                      VARCHAR(40) NULL,
  `atmospheric_air_pressure`                           VARCHAR(120) NULL,
  -- 5 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `obs_indirect_json`                                  JSON NULL,
  `obs_direct_json`                                    JSON NULL,
  `obs_air_json`                                       JSON NULL,
  CONSTRAINT `fk_datasheet_esd` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_surge` (          -- Surge
  `datasheet_id` INT PRIMARY KEY,
  -- 4 field(s) unique to this test
  `test_port_power`                                    VARCHAR(40) NULL,
  `test_port_signal`                                   VARCHAR(40) NULL,
  `coupling_phases`                                    VARCHAR(40) NULL,
  `repetition_rate`                                    VARCHAR(40) NULL,
  -- 6 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `obs_ac_json`                                        JSON NULL,
  `obs_dc_json`                                        JSON NULL,
  `obs_signal_json`                                    JSON NULL,
  CONSTRAINT `fk_datasheet_surge` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_re` (          -- Radiated Emission
  `datasheet_id` INT PRIMARY KEY,
  -- 51 field(s) unique to this test
  `name_of_the_test`                                   VARCHAR(46) NULL,
  `radiated_emission`                                  VARCHAR(40) NULL,
  `classification_col_1`                               VARCHAR(40) NULL,
  `classification_col_2`                               VARCHAR(40) NULL,
  `frequency_range`                                    VARCHAR(40) NULL,
  `frequency_range_col_1`                              VARCHAR(40) NULL,
  `frequency_range_col_2`                              VARCHAR(40) NULL,
  `resolution_bandwidth_col_1`                         VARCHAR(120) NULL,
  `resolution_bandwidth_col_2`                         VARCHAR(120) NULL,
  `video_bandwidth_col_1`                              VARCHAR(120) NULL,
  `video_bandwidth_col_2`                              VARCHAR(120) NULL,
  `step_size_col_1`                                    VARCHAR(120) NULL,
  `step_size_col_2`                                    VARCHAR(120) NULL,
  `turn_table_rotation_step_col_1`                     VARCHAR(120) NULL,
  `turn_table_rotation_step_col_2`                     VARCHAR(120) NULL,
  `antenna_height_variation_step_for_pre_scan_mea_2`   VARCHAR(120) NULL,
  `antenna_height_variation_step_for_pre_scan_mea_3`   VARCHAR(120) NULL,
  `antenna_height_variation_for_final_measurement_2`   VARCHAR(120) NULL,
  `antenna_height_variation_for_final_measurement_3`   VARCHAR(120) NULL,
  `pre_scan_measurement_time_col_1`                    VARCHAR(120) NULL,
  `pre_scan_measurement_time_col_2`                    VARCHAR(120) NULL,
  `final_scan_measurement_time_col_1`                  VARCHAR(120) NULL,
  `final_scan_measurement_time_col_2`                  VARCHAR(120) NULL,
  `attenuation_col_1`                                  VARCHAR(120) NULL,
  `attenuation_col_2`                                  VARCHAR(120) NULL,
  `test_distance`                                      VARCHAR(40) NULL,
  `polarization_col_1`                                 VARCHAR(46) NULL,
  `polarization_col_2`                                 VARCHAR(46) NULL,
  `detector_col_1`                                     VARCHAR(40) NULL,
  `detector_col_2`                                     VARCHAR(40) NULL,
  `ambient_temperature_sections`                       VARCHAR(40) NULL,
  `ambient_temperature_2`                              VARCHAR(120) NULL,
  `ambient_temperature_3`                              VARCHAR(120) NULL,
  `relative_humidity_sections`                         VARCHAR(40) NULL,
  `relative_humidity_2`                                VARCHAR(120) NULL,
  `relative_humidity_3`                                VARCHAR(120) NULL,
  `test_date_sections`                                 VARCHAR(40) NULL,
  `test_date_2`                                        VARCHAR(120) NULL,
  `test_date_3`                                        VARCHAR(120) NULL,
  `tested_by_sections`                                 VARCHAR(40) NULL,
  `tested_by_2`                                        VARCHAR(120) NULL,
  `tested_by_3`                                        VARCHAR(120) NULL,
  `frequency`                                          VARCHAR(50) NULL,
  `f_30_to_230`                                        VARCHAR(120) NULL,
  `f_230_to_1000`                                      VARCHAR(120) NULL,
  `f_30_to_88_fcc`                                     VARCHAR(120) NULL,
  `f_88_to_216_fcc`                                    VARCHAR(120) NULL,
  `f_216_to_960_fcc`                                   VARCHAR(120) NULL,
  `f_960_to_1000_fcc`                                  VARCHAR(120) NULL,
  `result_class`                                       VARCHAR(40) NULL,
  `overall_result`                                     VARCHAR(40) NULL,
  -- 11 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `re_table1_rows_json`                                JSON NULL,   -- 7 cols
  `re_table2_rows_json`                                JSON NULL,   -- 7 cols
  CONSTRAINT `fk_datasheet_re` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_rs_ri` (          -- Radiated Susceptibility
  `datasheet_id` INT PRIMARY KEY,
  -- 17 field(s) unique to this test
  `frequency_range_col_1`                              VARCHAR(40) NULL,
  `frequency_range_col_2`                              VARCHAR(40) NULL,
  `field_strength_col_1`                               VARCHAR(40) NULL,
  `field_strength_col_2`                               VARCHAR(40) NULL,
  `frequency_step_size`                                VARCHAR(40) NULL,
  `dwell_time`                                         VARCHAR(40) NULL,
  `modulation`                                         VARCHAR(40) NULL,
  `modulation_depth`                                   VARCHAR(40) NULL,
  `modulation_depth_and_frequency`                     VARCHAR(40) NULL,
  `antenna_polarization`                               VARCHAR(46) NULL,
  `test_distance`                                      VARCHAR(40) NULL,
  `ambient_temperature_col_1`                          VARCHAR(120) NULL,
  `ambient_temperature_col_2`                          VARCHAR(120) NULL,
  `relative_humidity_col_1`                            VARCHAR(120) NULL,
  `relative_humidity_col_2`                            VARCHAR(120) NULL,
  `test_date_col_1`                                    VARCHAR(120) NULL,
  `test_date_col_2`                                    VARCHAR(120) NULL,
  -- 5 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `obs_rs_json`                                        JSON NULL,
  CONSTRAINT `fk_datasheet_rs_ri` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_crf` (          -- Conducted RF Immunity
  `datasheet_id` INT PRIMARY KEY,
  -- 6 field(s) unique to this test
  `coupling_method`                                    VARCHAR(40) NULL,
  `test_level`                                         VARCHAR(40) NULL,
  `frequency_range`                                    VARCHAR(40) NULL,
  `frequency_step_size`                                VARCHAR(40) NULL,
  `dwell_time`                                         VARCHAR(40) NULL,
  `modulation`                                         VARCHAR(40) NULL,
  -- 3 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `test_observation_rows_json`                         JSON NULL,   -- 5 cols
  CONSTRAINT `fk_datasheet_crf` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_pfmf` (          -- Power-Frequency Magnetic Field
  `datasheet_id` INT PRIMARY KEY,
  -- 4 field(s) unique to this test
  `test_level`                                         VARCHAR(40) NULL,
  `test_method`                                        VARCHAR(40) NULL,
  `test_duration`                                      VARCHAR(40) NULL,
  `test_frequency`                                     VARCHAR(40) NULL,
  -- 5 image slot(s) -> datasheet.images_json, not columns here
  -- grid/measurement tables, JSON {columns:[{key,label}], rows:[[...]]}
  `obs_pfmf_json`                                      JSON NULL,
  CONSTRAINT `fk_datasheet_pfmf` FOREIGN KEY (`datasheet_id`)
     REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_equipment` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `datasheet_id` INT NOT NULL, `row_no` INT NOT NULL,
  `equipment_name` VARCHAR(255) NULL, `make` VARCHAR(150) NULL,
  `model_no` VARCHAR(150) NULL, `serial_no` VARCHAR(150) NULL,
  `calibration_due` VARCHAR(60) NULL,
  KEY `idx_dse` (`datasheet_id`),
  CONSTRAINT `fk_dse` FOREIGN KEY (`datasheet_id`) REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_software` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `datasheet_id` INT NOT NULL, `row_no` INT NOT NULL,
  `software_name` VARCHAR(200) NULL, `software_version` VARCHAR(80) NULL,
  KEY `idx_dss` (`datasheet_id`),
  CONSTRAINT `fk_dss` FOREIGN KEY (`datasheet_id`) REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_modification` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `datasheet_id` INT NOT NULL, `row_no` INT NOT NULL,
  `mod_state` VARCHAR(60) NULL, `description` TEXT NULL,
  `fitted_by` VARCHAR(150) NULL, `fitted_date` VARCHAR(40) NULL,
  KEY `idx_dsm` (`datasheet_id`),
  CONSTRAINT `fk_dsm` FOREIGN KEY (`datasheet_id`) REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_observation` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `datasheet_id` INT NOT NULL,
  `test_code` VARCHAR(20) NOT NULL,
  `grid_key`  VARCHAR(60)  NOT NULL,   -- indirect | direct | air | ac | dc | signal | power | rs | pfmf | dips
  `row_no`    INT NULL,
  `row_label` VARCHAR(200) NULL,       -- 'HCP (0deg)' | 'L1+N' | '80 to 1000'
  `col_key`   VARCHAR(60)  NULL,
  `col_label` VARCHAR(120) NULL,       -- '+4' | 'CM L->PE 0deg'
  `value`     VARCHAR(20)  NULL,       -- A | B2 | C1 | NA ...
  KEY `idx_dso` (`datasheet_id`),
  KEY `idx_dso_val` (`test_code`, `value`),
  CONSTRAINT `fk_dso` FOREIGN KEY (`datasheet_id`) REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    # Every MEASURED number the lab records. Until this table existed, 13 of the
    # 25 grids on the per-test tables were stored as JSON and nowhere else:
    # CE's Line and Neutral quasi-peak/average readings, RE's two tables,
    # harmonic currents, the three flicker grids, and the per-test observation
    # row tables. Observations had `datasheet_observation`; measurements had
    # nothing, so "show me every CE reading within 3 dB of its limit" could not
    # be written in SQL at all - the numbers were in the database and out of
    # reach of it.
    #
    # Same shape as datasheet_observation on purpose: that design already
    # carries ten different grids with different column counts, so it is known
    # to work, and a DBA who can read one can read the other. The JSON columns
    # are kept as well - they hold the grid's own labels and block structure,
    # which is what regenerates the document - so this is a second view of the
    # same data, not a migration away from it.
    """CREATE TABLE IF NOT EXISTS `datasheet_measurement` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `datasheet_id` INT NOT NULL,
  `revision_no` INT NOT NULL DEFAULT 1,   -- which submitted version this reading belongs to
  `test_code` VARCHAR(20) NOT NULL,
  `grid_key`  VARCHAR(60) NOT NULL,    -- line_measurements | re_table1 | harmonic_rows | flicker_meas_rows ...
  `block_label` VARCHAR(200) NULL,     -- CE repeats its whole grid per Test; this names which
  `row_no`    INT NULL,
  `row_label` VARCHAR(200) NULL,
  `col_key`   VARCHAR(60) NULL,        -- qp_freq | qp | qp_limit | qp_margin ...
  `col_label` VARCHAR(120) NULL,       -- 'Frequency (MHz)' | 'Q-peak' | 'Limit'
  `value`     VARCHAR(120) NULL,       -- wider than an observation: these are numbers with units
  `value_num` DECIMAL(18,6) NULL,      -- the same cell parsed, so a DBA can compare and sort
  KEY `idx_dsms` (`datasheet_id`, `revision_no`),
  KEY `idx_dsms_grid` (`test_code`, `grid_key`, `col_key`),
  UNIQUE KEY `uq_dsms` (`datasheet_id`, `revision_no`, `grid_key`, `block_label`, `row_no`, `col_key`),
  CONSTRAINT `fk_dsms` FOREIGN KEY (`datasheet_id`) REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_observation_legend` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `datasheet_id` INT NOT NULL,
  `grid_scope` VARCHAR(60) NULL,       -- which legend block (obs_legend / eft_obs_legend / ...)
  `code` VARCHAR(20) NOT NULL,         -- A | B1 | C2 | D3 | NA
  `description` TEXT NULL,
  `sort_order` INT NOT NULL DEFAULT 0,
  UNIQUE KEY `uq_dsl` (`datasheet_id`, `grid_scope`, `code`),
  CONSTRAINT `fk_dsl` FOREIGN KEY (`datasheet_id`) REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_revision` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `datasheet_id` INT NOT NULL,
  `revision_no`  INT NOT NULL,
  `status`       VARCHAR(20) NOT NULL,
  `form_json`    LONGTEXT NULL,        -- the FULL form as it stood
  `images_json`  TEXT NULL,
  -- the fields most often compared between revisions, as real columns
  `result` VARCHAR(30) NULL, `test_date` DATE NULL,
  `ambient_temperature` VARCHAR(40) NULL, `relative_humidity` VARCHAR(40) NULL,
  `required_performance_criteria` VARCHAR(20) NULL,
  `met_performance_criteria` VARCHAR(20) NULL,
  `tested_by` VARCHAR(200) NULL, `deviation` TEXT NULL,
  `created_by_user_id` INT NULL,
  `submitted_at` DATETIME NULL,        -- engineer sent it for review
  `decided_at`   DATETIME NULL,        -- reviewer approved/rejected it
  `created_at`   DATETIME NOT NULL,    -- when this snapshot was written
  UNIQUE KEY `uq_dsr` (`datasheet_id`, `revision_no`),
  CONSTRAINT `fk_dsr` FOREIGN KEY (`datasheet_id`) REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    # Every save an engineer makes, kept.
    #
    # datasheet_records holds ONE row per assignment and upserts it in place, so
    # an engineer who typed 48.2, saved, noticed it should be 48.7 and saved
    # again left no trace of 48.2 anywhere in the database. datasheet_revision
    # only froze SUBMITTED versions, which is a coarser grain: a datasheet
    # submitted once has exactly one snapshot and no record of the hour of
    # editing that produced it.
    #
    # Append-only. Nothing updates or deletes a row here, which is the whole
    # point - a history you can rewrite is not a history.
    #
    # content_hash exists because the autosave fires 1.5 s after typing stops
    # and does not know whether anything changed. Without it, tabbing through a
    # form would append identical rows until the table was mostly noise.
    #
    # changed_fields is what makes it readable: "which boxes did they touch on
    # this save" answered without diffing two JSON blobs by eye.
    """CREATE TABLE IF NOT EXISTS `datasheet_draft_history` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `planner_entry_id` INT NOT NULL,     -- stable: exists before the projection does
  `datasheet_id` INT NULL,
  `revision_no` INT NOT NULL DEFAULT 1,
  `test_code` VARCHAR(20) NULL,
  `status` VARCHAR(30) NULL,           -- what the save was called: Draft / Submitted
  `form_json` LONGTEXT NULL,           -- the WHOLE form as it stood after this save
  `content_hash` CHAR(40) NULL,        -- sha1 of form_json; skips no-op autosaves
  `changed_fields` TEXT NULL,          -- the keys that differed from the save before
  `changed_count` INT NOT NULL DEFAULT 0,
  `saved_by_user_id` INT NULL,
  `saved_by_name` VARCHAR(200) NULL,
  `saved_at` DATETIME NOT NULL,
  KEY `idx_dsdh_entry` (`planner_entry_id`, `saved_at`),
  KEY `idx_dsdh_sheet` (`datasheet_id`, `revision_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS `datasheet_status_history` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `datasheet_id` INT NOT NULL,
  `revision_no`  INT NOT NULL,
  `from_status`  VARCHAR(20) NULL,
  `to_status`    VARCHAR(20) NOT NULL,
  `actor_user_id` INT NULL, `actor_name` VARCHAR(200) NULL,
  `actor_role`   VARCHAR(30) NULL,
  `comment`      TEXT NULL,            -- the rejection reason, verbatim
  `created_at`   DATETIME NOT NULL,
  KEY `idx_dsh` (`datasheet_id`),
  KEY `idx_dsh_to` (`to_status`, `created_at`),
  CONSTRAINT `fk_dsh` FOREIGN KEY (`datasheet_id`) REFERENCES `datasheet`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def ensure_projection_tables(app):
    """Create any projection table that does not exist yet.

    Idempotent and best-effort: a failure here must never stop the app booting,
    because the datasheet capture path does not depend on these tables.
    Returns the list of tables actually created.
    """
    try:
        from models import db
    except Exception:  # pragma: no cover - models always importable in the app
        return []

    created = []
    with app.app_context():
        try:
            existing = set(inspect(db.engine).get_table_names())
        except Exception as exc:
            app.logger.warning("datasheet projection: table check skipped: %s", exc)
            return []

        for name, ddl in zip(TABLES, _DDL):
            if name in existing:
                continue
            try:
                db.session.execute(text(ddl))
                db.session.commit()
                created.append(name)
            except Exception as exc:
                db.session.rollback()
                msg = str(exc).lower()
                if "exist" in msg or "duplicate" in msg:
                    continue          # created concurrently (reloader race)
                app.logger.error("datasheet projection: could not create %s: %s",
                                 name, exc)

        created += _ensure_revision_mirrors(app, db, existing)

        if created:
            app.logger.info("datasheet projection: created %d table(s): %s",
                            len(created), ", ".join(created))
        _ensure_integrity(app, db)
    return created


# --------------------------------------------------------------------------
# Fixes to tables this module does not own
# --------------------------------------------------------------------------
# These were applied by hand on one database, which is exactly how a dev
# environment ends up quietly different from production. There is no migration
# framework here - the convention is an idempotent ensure_* at boot - so they
# belong in one, guarded and logged, and every environment converges by being
# started.
#
# Two of the three touch tables outside datasheet_gen (`users`,
# `planner_entries`). That is deliberate: putting them somewhere "more correct"
# would mean inventing a new boot hook, and a fix nobody runs is worth nothing.

_INDEXES = (
    # A join column on every request-to-datasheet query, previously unindexed.
    ("datasheet", "test_request_id", "idx_ds_testreq"),
)

# ON DELETE SET NULL, not CASCADE: deleting a user must not destroy the
# datasheet they touched, and the NAME survives anyway in
# datasheet_status_history.actor_name and datasheet.engineer_name. Untouched
# these columns hold ids with nothing enforcing them, so removing a user leaves
# a number that looks valid and points at nothing.
#
# The STRUCTURAL links are deliberately absent - datasheet.planner_entry_id,
# datasheet.test_request_id, planner_entries.test_request_id,
# datasheet_records.planner_entry_id. app.py deletes requests and planner
# entries, so RESTRICT would block working buttons and CASCADE would silently
# destroy filled datasheets. What deleting a job should MEAN is a product
# decision, not a missing constraint.
_FKS = (
    ("datasheet", "reviewer_user_id", "users", "fk_ds_reviewer"),
    ("datasheet", "created_by_user_id", "users", "fk_ds_creator"),
    ("planner_entries", "engineer_user_id", "users", "fk_pe_engineer"),
    ("planner_entries", "peer_reviewer_user_id", "users", "fk_pe_reviewer"),
    # The only history table with no constraint, and it already had a dangling
    # row: draft-history is append-only, so discarding a draft deleted the
    # `datasheet` row and left the history pointing at an id that no longer
    # exists. That row cannot be joined and cannot be told apart from a valid
    # link. ON DELETE SET NULL (the clause below) is the right behaviour here -
    # the edit history outlives the datasheet, it just stops claiming a parent.
    #
    # changed_fields is the most precise signal in the schema for "what did the
    # engineer change after the reviewer sent it back", so this table being
    # unjoinable is not a cosmetic problem.
    ("datasheet_draft_history", "datasheet_id", "datasheet", "fk_dsdh_sheet"),
)

# `users` was the only table in the database on utf8mb4_unicode_ci while all 61
# others were utf8mb4_0900_ai_ci, so ANY join on a person's name threw MySQL
# 1267 - including the obvious one a question about engineers leads to.
_COLLATION = "utf8mb4_0900_ai_ci"


def _table_exists(db, name):
    return bool(db.session.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_name=:t"), {"t": name}).scalar())


def _ensure_integrity(app, db):
    done = []

    for table, column, index in _INDEXES:
        try:
            if not _table_exists(db, table):
                continue
            if db.session.execute(text(
                    "SELECT COUNT(*) FROM information_schema.statistics "
                    "WHERE table_schema=DATABASE() AND table_name=:t AND column_name=:c"),
                    {"t": table, "c": column}).scalar():
                continue
            db.session.execute(text("CREATE INDEX `%s` ON `%s` (`%s`)"
                                    % (index, table, column)))
            db.session.commit()
            done.append("index %s.%s" % (table, column))
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            if "duplicate" not in str(exc).lower() and "exist" not in str(exc).lower():
                app.logger.warning("schema: index on %s.%s skipped: %s", table, column, exc)

    # Collation before the foreign keys: a FK between columns of differing
    # collation is refused, so doing these the other way round fails on a fresh
    # database and leaves it half-fixed.
    try:
        if _table_exists(db, "users"):
            current = db.session.execute(text(
                "SELECT table_collation FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name='users'")).scalar()
            if current and current != _COLLATION:
                db.session.execute(text(
                    "ALTER TABLE `users` CONVERT TO CHARACTER SET utf8mb4 COLLATE %s"
                    % _COLLATION))
                db.session.commit()
                done.append("users collation %s -> %s" % (current, _COLLATION))
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        app.logger.warning("schema: users collation not converted: %s", exc)

    # Repair DANGLING draft-history links before the foreign keys are attempted,
    # or the constraint below refuses to apply (correctly - it will not delete a
    # row to make itself fit). Distinct from the NULL backfill further down: this
    # is a datasheet_id that WAS valid and whose datasheet has since been
    # deleted. Re-point it if that planner entry has a datasheet again, otherwise
    # NULL it, because a NULL is honestly "no parent" while a dangling id looks
    # like a working link and silently vanishes from every join.
    try:
        if _table_exists(db, "datasheet_draft_history") and _table_exists(db, "datasheet"):
            res = db.session.execute(text(
                "UPDATE datasheet_draft_history h "
                "LEFT JOIN `datasheet` dead ON dead.id = h.datasheet_id "
                "LEFT JOIN `datasheet` live ON live.planner_entry_id = h.planner_entry_id "
                "SET h.datasheet_id = live.id "
                "WHERE h.datasheet_id IS NOT NULL AND dead.id IS NULL"))
            if res.rowcount:
                db.session.commit()
                done.append("repaired %d dangling draft-history link(s)" % res.rowcount)
            else:
                db.session.rollback()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        app.logger.warning("schema: draft-history dangling repair skipped: %s", exc)

    # STRANDED MEASUREMENTS. datasheet.revision_no is the next-to-edit pointer,
    # so re-projecting an APPROVED datasheet used to file a duplicate set of
    # readings under a revision that will never be frozen. projection.py no
    # longer does that, but the duplicates it already wrote are indistinguishable
    # from real readings to every query: on this database 258 rows, 17% of
    # datasheet_measurement, and a COUNT over one datasheet came back exactly
    # double with nothing in the result to say so.
    #
    # Safe to delete because the revision has no header in datasheet_revision,
    # which means it was never submitted and nobody can be looking at it, and
    # the sheet is locked so it is not somebody's working draft. A draft's
    # in-progress readings sit at the pointer too and are deliberately spared.
    try:
        if (_table_exists(db, "datasheet_measurement")
                and _table_exists(db, "datasheet_revision")):
            res = db.session.execute(text(
                "DELETE m FROM datasheet_measurement m "
                "JOIN `datasheet` d ON d.id = m.datasheet_id "
                "LEFT JOIN datasheet_revision r "
                "  ON r.datasheet_id = m.datasheet_id "
                " AND r.revision_no = m.revision_no "
                "WHERE r.id IS NULL AND d.status IN ('Approved')"))
            if res.rowcount:
                db.session.commit()
                done.append("removed %d stranded measurement row(s)" % res.rowcount)
            else:
                db.session.rollback()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        app.logger.warning("schema: stranded-measurement sweep skipped: %s", exc)

    for table, column, ref, name in _FKS:
        try:
            if not (_table_exists(db, table) and _table_exists(db, ref)):
                continue
            if db.session.execute(text(
                    "SELECT COUNT(*) FROM information_schema.key_column_usage "
                    "WHERE table_schema=DATABASE() AND table_name=:t AND column_name=:c "
                    "AND referenced_table_name IS NOT NULL"),
                    {"t": table, "c": column}).scalar():
                continue
            # An existing orphan makes the ALTER fail. Report it and move on
            # rather than half-applying - and never delete the row to make the
            # constraint fit.
            orphans = db.session.execute(text(
                "SELECT COUNT(*) FROM `%s` c LEFT JOIN `%s` p ON p.id=c.`%s` "
                "WHERE c.`%s` IS NOT NULL AND p.id IS NULL"
                % (table, ref, column, column))).scalar()
            if orphans:
                app.logger.warning(
                    "schema: %s.%s has %d row(s) pointing at a missing %s - "
                    "foreign key not added", table, column, orphans, ref)
                continue
            db.session.execute(text(
                "ALTER TABLE `%s` ADD CONSTRAINT `%s` FOREIGN KEY (`%s`) "
                "REFERENCES `%s`(`id`) ON DELETE SET NULL"
                % (table, name, column, ref)))
            db.session.commit()
            done.append("fk %s.%s" % (table, column))
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            if "duplicate" not in str(exc).lower() and "exist" not in str(exc).lower():
                app.logger.warning("schema: fk on %s.%s skipped: %s", table, column, exc)

    # Link history rows written before their datasheet row existed. The ordering
    # bug that caused it is fixed in records.upsert_record, but rows already
    # written carry a NULL and would stay invisible to any query that joins
    # through `datasheet`.
    try:
        if _table_exists(db, "datasheet_draft_history"):
            res = db.session.execute(text(
                "UPDATE datasheet_draft_history h "
                "JOIN `datasheet` d ON d.planner_entry_id = h.planner_entry_id "
                "SET h.datasheet_id = d.id WHERE h.datasheet_id IS NULL"))
            if res.rowcount:
                db.session.commit()
                done.append("linked %d orphaned draft-history row(s)" % res.rowcount)
            else:
                db.session.rollback()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        app.logger.warning("schema: draft-history backfill skipped: %s", exc)

    if done:
        app.logger.info("schema: applied %d integrity fix(es): %s",
                        len(done), "; ".join(done))
    return done


# --------------------------------------------------------------------------
# Per-revision detail, in columns
# --------------------------------------------------------------------------
# datasheet_revision froze a header and a form_json blob, and nothing else. So
# "what did the CE datasheet say before the reviewer rejected it" meant opening
# form_json and reading it by eye - which is the complaint that started this:
# the data was in the database and not in the database's terms.
#
# Each mirror is created with CREATE TABLE ... LIKE its live counterpart, so it
# has exactly the same columns - all 27 of datasheet_ce, all 54 of datasheet_re -
# and cannot drift when one of them gains a column. Writing sixteen schemas by
# hand would have guaranteed that drift.
#
# Two adjustments after the copy: a revision_no column, and every UNIQUE index
# that contains datasheet_id is rebuilt to include it. Without the second step
# datasheet_ce's primary key (which IS datasheet_id) would allow one revision
# per datasheet and silently reject the rest.
_MIRRORED = (
    "datasheet_ce", "datasheet_voltagedips", "datasheet_voltageflicker",
    "datasheet_harmonic", "datasheet_eft", "datasheet_esd", "datasheet_surge",
    "datasheet_re", "datasheet_rs_ri", "datasheet_crf", "datasheet_pfmf",
    "datasheet_equipment", "datasheet_software", "datasheet_modification",
    "datasheet_observation", "datasheet_observation_legend",
)

_MIRROR_PREFIX = "datasheet_rev_"


def mirror_name(table):
    """datasheet_ce -> datasheet_rev_ce."""
    return _MIRROR_PREFIX + table[len("datasheet_"):]


def mirrored_tables():
    return tuple((t, mirror_name(t)) for t in _MIRRORED)


def _unique_indexes_with(db, table, column):
    """[(index_name, [columns...])] for UNIQUE indexes containing ``column``."""
    rows = db.session.execute(text(
        "SELECT index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) "
        "FROM information_schema.statistics WHERE table_schema=DATABASE() "
        "AND table_name=:t AND non_unique=0 GROUP BY index_name"),
        {"t": table}).fetchall()
    out = []
    for name, cols in rows:
        parts = [c.strip() for c in (cols or "").split(",") if c.strip()]
        if column in parts:
            out.append((name, parts))
    return out


def _ensure_revision_mirrors(app, db, existing):
    created = []
    for src, dst in mirrored_tables():
        if src not in existing or dst in existing:
            continue
        try:
            db.session.execute(text("CREATE TABLE `%s` LIKE `%s`" % (dst, src)))
            db.session.execute(text(
                "ALTER TABLE `%s` ADD COLUMN `revision_no` INT NOT NULL DEFAULT 1" % dst))
            for name, cols in _unique_indexes_with(db, dst, "datasheet_id"):
                newcols = ", ".join("`%s`" % c for c in cols + ["revision_no"])
                if name == "PRIMARY":
                    db.session.execute(text(
                        "ALTER TABLE `%s` DROP PRIMARY KEY, ADD PRIMARY KEY (%s)"
                        % (dst, newcols)))
                else:
                    db.session.execute(text(
                        "ALTER TABLE `%s` DROP INDEX `%s`, ADD UNIQUE KEY `%s` (%s)"
                        % (dst, name, name[:60], newcols)))
            db.session.execute(text(
                "ALTER TABLE `%s` ADD KEY `idx_%s_rev` (`datasheet_id`, `revision_no`)"
                % (dst, dst[-24:])))
            db.session.commit()
            created.append(dst)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            msg = str(exc).lower()
            if "exist" in msg or "duplicate" in msg:
                continue
            app.logger.error("datasheet projection: could not mirror %s: %s", src, exc)
    return created
