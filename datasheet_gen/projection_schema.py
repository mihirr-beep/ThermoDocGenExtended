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
    "datasheet_observation_legend",
    "datasheet_revision",
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

        if created:
            app.logger.info("datasheet projection: created %d table(s): %s",
                            len(created), ", ".join(created))
    return created
