# Datasheet storage redesign — plan for review

> Status: **proposal, awaiting confirmation**. Nothing has been implemented.
> Written against branch `merged-workflow`, measured from the live
> `test_plan_generator` database and the 11 datasheet schemas.
>
> The complete DDL is in **[`datasheet_schema.sql`](datasheet_schema.sql)** —
> generated from the real schemas (every column name is an actual form key) and
> **verified by executing it**: 19 tables, 384 columns, 18 foreign keys, all
> created cleanly against a throwaway database, with a round-trip insert/join
> and cascade-delete test.

## 1. Why

Today every filled datasheet is one row in `datasheet_records`, and the whole
form lives in a single `form_json` LONGTEXT column. That is lossless and simple,
but it means:

* **nothing inside a datasheet is queryable** — the NL search literally cannot
  answer "which tests failed", "what ambient temperature was CE run at", or
  "which equipment is used most", because those values are inside a JSON blob;
* **rejections leave no trace.** A peer-review rejection sets
  `planner_entries.status` back to `in_progress` and appends free text to
  `datasheet_comments`. Once that happens a rejected datasheet is
  **indistinguishable from one that was never submitted**. There is no
  structured record of who rejected what, when, or why;
* **there is no version history.** Re-submitting after a rejection overwrites
  `form_json`. The previous answers are gone.

This plan fixes all three without touching the existing capture path.

## 2. Guiding principle

> **`form_json` stays the source of truth. The new tables are a derived,
> rebuildable projection of it.**

Everything below follows from that. It is what makes the change safe: the new
tables can be dropped and rebuilt at any time, the datasheet UI does not change,
and a bug in the projection can never lose an engineer's work.

## 3. What is actually in a datasheet

Measured from the 11 schemas (not estimated):

| | Count |
|---|---|
| Scalar fields shared by ≥6 of the 11 datasheets | **24** |
| Test-specific scalar fields, all 11 combined | **136** (RE 51, CE 24, RS_RI 17, EFT/HARMONIC 8, ESD/CRF 6, FLICKER 5, SURGE/PFMF 4, VDIPS 3) |
| Repeating tables identical across all 11 | **3** (modification 4-col, equipment 5-col, software 2-col) |
| Test-specific measurement/observation tables | **10** |
| Image slots | **53** |

**Important:** the observation grids (ESD's 84 cells, SURGE's CM/DM matrices,
EFT's power/signal tables) are **not declared in the JSON schemas**. They are
rendered by custom layouts and posted as flat form keys (`ind_r3_c5`,
`surge_obs_ac_2__c7`), then read back by per-code builders in
`datasheet_gen/generic_service.py`. So the column list cannot be derived from
the schemas alone.

Good news: `report_gen/mapping.py::observation_tables()` already extracts every
one of these into labelled rows, and is exercised against all 11 tests. The
projection reuses it rather than re-deriving the logic.

---

## 4. The storage model

### 4.1 `datasheet` — the header (answers Q1: how each value is stored)

One row per planner entry — i.e. per test execution. Carries the 24 common
fields as **real, named columns**, plus identity columns denormalised at
projection time so that most questions need no join at all.

```sql
CREATE TABLE datasheet (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  planner_entry_id   INT NOT NULL,
  test_request_id    INT NULL,
  test_code          VARCHAR(20) NOT NULL,      -- CE, RE, ESD, ...

  -- denormalised identity (avoids a 4-table join on almost every question)
  tco_id             VARCHAR(50)  NULL,
  job_number         VARCHAR(100) NULL,
  product_name       VARCHAR(255) NULL,
  eut_class          VARCHAR(30)  NULL,
  engineer_name      VARCHAR(200) NULL,
  peer_reviewer_name VARCHAR(200) NULL,

  -- the 24 fields common to (nearly) every datasheet
  eut_name                      VARCHAR(255) NULL,
  eut_model_sku_number          VARCHAR(100) NULL,
  eut_serial_number             VARCHAR(100) NULL,
  product_standard              VARCHAR(500) NULL,
  basic_standard                VARCHAR(500) NULL,
  test_port                     VARCHAR(100) NULL,
  test_mode                     TEXT NULL,
  eut_configuration             VARCHAR(60)  NULL,
  eut_modification_state        VARCHAR(60)  NULL,
  eut_input_voltage_frequency   VARCHAR(120) NULL,
  immunity_test_requirement     VARCHAR(60)  NULL,
  ambient_temperature           DECIMAL(6,2) NULL,
  relative_humidity             DECIMAL(6,2) NULL,
  test_date                     DATE NULL,
  tested_by                     VARCHAR(200) NULL,
  sop_reference                 VARCHAR(100) NULL,
  monitoring_parameters         TEXT NULL,
  deviation                     TEXT NULL,
  test_procedure                MEDIUMTEXT NULL,
  required_performance_criteria VARCHAR(20) NULL,
  met_performance_criteria      VARCHAR(20) NULL,
  signoff_name                  VARCHAR(200) NULL,
  signoff_date                  DATE NULL,
  result                        VARCHAR(30) NULL,   -- Pass / Fail / Incomplete

  -- lifecycle
  status             VARCHAR(20) NOT NULL DEFAULT 'Draft',
  revision_no        INT NOT NULL DEFAULT 1,
  submitted_at       DATETIME NULL,
  decided_at         DATETIME NULL,
  reviewer_user_id   INT NULL,

  created_by_user_id INT NULL,
  created_at         DATETIME NOT NULL,
  updated_at         DATETIME NOT NULL,

  UNIQUE KEY uq_ds_entry (planner_entry_id),
  KEY idx_ds_code (test_code), KEY idx_ds_status (status),
  KEY idx_ds_tco (tco_id), KEY idx_ds_result (result)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Typing:** values are stored in their natural type where the form guarantees
one (`test_date` DATE, `ambient_temperature` DECIMAL) and as VARCHAR/TEXT
otherwise. A value that fails to parse is stored as NULL in the typed column —
never dropped, because the raw text always remains in `form_json`.

### 4.2 `datasheet_<code>` — the 11 test-specific tables (answers Q1, Q4)

One narrow table per test, real column names, one row per datasheet.
**Image paths live here**, as you asked.

```sql
CREATE TABLE datasheet_esd (
  datasheet_id  INT PRIMARY KEY,          -- FK -> datasheet.id

  -- the 6 fields unique to ESD
  rc_network                VARCHAR(60) NULL,
  direct_contact_discharge  VARCHAR(60) NULL,
  indirect_hcp              VARCHAR(60) NULL,
  indirect_vcp              VARCHAR(60) NULL,
  air_discharge             VARCHAR(60) NULL,
  atmospheric_air_pressure  VARCHAR(40) NULL,

  -- image slots, fixed by this test's schema
  img_photo_1_path     VARCHAR(500) NULL,
  img_photo_1_caption  VARCHAR(300) NULL,
  img_photo_2_path     VARCHAR(500) NULL,
  img_photo_2_caption  VARCHAR(300) NULL,
  img_photo_3_path     VARCHAR(500) NULL,
  img_photo_3_caption  VARCHAR(300) NULL,
  img_photo_4_path     VARCHAR(500) NULL,
  img_photo_4_caption  VARCHAR(300) NULL,
  signature_path       VARCHAR(500) NULL,

  CONSTRAINT fk_ds_esd FOREIGN KEY (datasheet_id)
     REFERENCES datasheet(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Widths, driven by the measured field counts (total columns as actually created):

| Table | Spec cols | Image slots | Grid JSON | **Total** |
|---|---|---|---|---|
| `datasheet_voltagedips` | 3 | 2 | 3 | 11 |
| `datasheet_voltageflicker` | 5 | 2 | 3 | 13 |
| `datasheet_crf` | 6 | 3 | 1 | 14 |
| `datasheet_harmonic` | 8 | 2 | 2 | 15 |
| `datasheet_pfmf` | 4 | 5 | 1 | 16 |
| `datasheet_esd` | 6 | 5 | 3 | 20 |
| `datasheet_surge` | 4 | 6 | 3 | 20 |
| `datasheet_eft` | 8 | 6 | 4 | 25 |
| `datasheet_rs_ri` | 17 | 5 | 1 | 29 |
| `datasheet_ce` | 24 | 6 | 2 | 39 |
| `datasheet_re` | 51 | 11 | 2 | **77** |

**Exception 1 — RE's dynamic image slots.** RE also has *dynamic* slots: one pair
of plots per measurement group (`meas_img_*`) and engineer-added extras
(`re_extra_photo_*`). Their number is not fixed, so they cannot be columns.
`datasheet_re` gets an `extra_images_json` column for those. RE is the only test
affected.

**Exception 2 — column widths are sized, not uniform.** A blanket
`VARCHAR(255)` does not work: utf8mb4 charges 4 bytes per character, so RE's 51
spec columns alone came to ~52 KB and MySQL **refused to create the table**
(*"Row size too large… maximum is 65535"*). This was caught by executing the
DDL, not by reading it. Each column is therefore sized from what its field can
actually hold — its option list or default value — with a 40-char floor and a
255-char ceiling, and long free text (`test_procedure`, `deviation`,
`monitoring_parameters`) uses `TEXT`, which is stored off-page. `datasheet_re`
now creates cleanly at 77 columns.

### 4.3 Shared child tables — structured rows

These three are byte-for-byte the same shape in all 11 datasheets, and they are
the ones worth aggregating across tests, so they get real rows:

```sql
CREATE TABLE datasheet_equipment (           -- "is out-of-calibration kit in use?"
  id INT AUTO_INCREMENT PRIMARY KEY,
  datasheet_id INT NOT NULL, row_no INT NOT NULL,
  equipment_name VARCHAR(255), make VARCHAR(150), model_no VARCHAR(150),
  serial_no VARCHAR(150), calibration_due VARCHAR(60),
  KEY idx_dse (datasheet_id),
  CONSTRAINT fk_dse FOREIGN KEY (datasheet_id) REFERENCES datasheet(id) ON DELETE CASCADE
);

CREATE TABLE datasheet_software (            -- software name + version
  id INT AUTO_INCREMENT PRIMARY KEY,
  datasheet_id INT NOT NULL, row_no INT NOT NULL,
  software_name VARCHAR(200), software_version VARCHAR(80),
  KEY idx_dss (datasheet_id),
  CONSTRAINT fk_dss FOREIGN KEY (datasheet_id) REFERENCES datasheet(id) ON DELETE CASCADE
);

CREATE TABLE datasheet_modification (        -- EUT modification record
  id INT AUTO_INCREMENT PRIMARY KEY,
  datasheet_id INT NOT NULL, row_no INT NOT NULL,
  mod_state VARCHAR(60), description TEXT,
  fitted_by VARCHAR(150), fitted_date VARCHAR(40),
  KEY idx_dsm (datasheet_id),
  CONSTRAINT fk_dsm FOREIGN KEY (datasheet_id) REFERENCES datasheet(id) ON DELETE CASCADE
);
```

### 4.4 Measurement & observation grids — JSON with labels (answers Q3)

As you suggested, the per-test grids are stored as **self-describing JSON with
labels**, one column per grid on the test's own table. They are 2-D matrices
whose meaning is test-specific, and their row counts are high (HARMONIC alone is
40 rows).

```json
{
  "label": "CE_Line_Quasi-peak & Average_0.15MHz - 30MHz",
  "columns": [
    {"key": "qp_freq", "label": "Frequency (MHz)"},
    {"key": "qp",      "label": "Quasi-peak (dBµV)"},
    {"key": "qp_limit","label": "Limit (dBµV)"}
  ],
  "rows": [
    ["0.212", "48.2", "63.5"],
    ["0.485", "44.1", "56.0"]
  ]
}
```

So `datasheet_ce` gets `line_measurements_json` and `neutral_measurements_json`,
`datasheet_harmonic` gets `harmonic_rows_json`, and so on. Labels travel with
the data, so nothing has to be looked up elsewhere to render or explain it.

**One recommended addition** (your call — see §9): a thin
`datasheet_observation` table holding only the **verdict cells** of the immunity
grids:

```sql
CREATE TABLE datasheet_observation (
  id INT AUTO_INCREMENT PRIMARY KEY,
  datasheet_id INT NOT NULL, test_code VARCHAR(20) NOT NULL,
  grid_key   VARCHAR(60),      -- 'indirect' | 'direct' | 'air' | 'ac' | 'signal' ...
  row_label  VARCHAR(150),     -- 'HCP (0°)' | 'L1+N' | '80 to 1000'
  col_label  VARCHAR(60),      -- '+4' | 'CM L→PE 0°'
  value      VARCHAR(20),      -- A / B / C / D / NA
  KEY idx_dso (datasheet_id), KEY idx_dso_val (test_code, value)
);
```

It exists purely so that **"show every observation that was not criterion A"**
is one query across all 11 tests, rather than parsing 11 different JSON shapes.
It is derived from the same JSON, so it costs nothing to rebuild and nothing is
duplicated conceptually.

### 4.5 Observation legend — the A2 / B3 / C1 codes

The observation dropdowns do **not** only offer A/B/C/D. The ESD, RS, PFMF and
EFT/SURGE grids offer the extended set:

```
A   B   C   D   B1  B2  B3   C1  C2  C3   D1  D2  D3
```

and for **every code the engineer actually uses**, the form builds a legend box
asking them to describe it — *"B2: display flickered, recovered without
intervention"*. Those descriptions are posted as `<prefix>_code[]` /
`<prefix>_desc[]` (`obs_legend_*`, `eft_obs_legend_*`, `surge_obs_legend_*`,
`pfmf_obs_legend_*`) and today exist **only inside `form_json`** — they are not
queryable, and the report generator re-derives them on every build.

They get their own table so a code and its meaning stay together:

```sql
CREATE TABLE datasheet_observation_legend (
  id INT AUTO_INCREMENT PRIMARY KEY,
  datasheet_id INT NOT NULL,
  grid_scope  VARCHAR(60) NULL,   -- which legend block the code belongs to
  code        VARCHAR(20) NOT NULL,   -- A | B1 | C2 | D3 | NA
  description TEXT NULL,              -- what the engineer typed
  sort_order  INT NOT NULL DEFAULT 0,
  UNIQUE KEY uq_dsl (datasheet_id, grid_scope, code)
);
```

Verified working — this join returns the cell verdict together with its meaning:

```sql
SELECT d.tco_id, o.grid_key, o.row_label, o.col_label, o.value, l.description
FROM datasheet d
JOIN datasheet_observation o        ON o.datasheet_id = d.id
JOIN datasheet_observation_legend l ON l.datasheet_id = d.id AND l.code = o.value
WHERE o.value <> 'A';
-- ('IEC-EMC-999','indirect','HCP (0deg)','+4','B2','Display flickered, self-recovered')
```

---

## 5. Versioning and audit (answers Q2)

Two tables. `form_json` snapshots give lossless history; the status log gives
queryable history.

```sql
CREATE TABLE datasheet_revision (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  datasheet_id  INT NOT NULL,
  revision_no   INT NOT NULL,
  status        VARCHAR(20) NOT NULL,     -- status this revision reached
  form_json     LONGTEXT NULL,            -- the FULL form as submitted
  images_json   TEXT NULL,
  -- the 8 fields most often compared between revisions, as real columns
  result VARCHAR(30) NULL, test_date DATE NULL,
  ambient_temperature DECIMAL(6,2) NULL, relative_humidity DECIMAL(6,2) NULL,
  required_performance_criteria VARCHAR(20) NULL,
  met_performance_criteria VARCHAR(20) NULL,
  tested_by VARCHAR(200) NULL, deviation TEXT NULL,

  created_by_user_id INT NULL,
  submitted_at  DATETIME NULL,            -- when the engineer sent it
  decided_at    DATETIME NULL,            -- when the reviewer approved/rejected
  created_at    DATETIME NOT NULL,        -- when this snapshot was written
  UNIQUE KEY uq_dsr (datasheet_id, revision_no)
);

CREATE TABLE datasheet_status_history (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  datasheet_id  INT NOT NULL,
  revision_no   INT NOT NULL,
  from_status   VARCHAR(20) NULL,
  to_status     VARCHAR(20) NOT NULL,
  actor_user_id INT NULL,
  actor_name    VARCHAR(200) NULL,
  actor_role    VARCHAR(30) NULL,         -- lab_engineer / admin
  comment       TEXT NULL,                -- the rejection reason, verbatim
  created_at    DATETIME NOT NULL,
  KEY idx_dsh (datasheet_id), KEY idx_dsh_to (to_status, created_at)
);
```

**A revision snapshot is written only at lifecycle events** — submit, approve,
reject — never on an autosave. Otherwise you would accumulate thousands of
snapshots per datasheet.

Worked example — CE rejected once, then approved:

| # | Event | `datasheet.status` | `revision_no` | Rows written |
|---|---|---|---|---|
| 1 | engineer types, autosaves | `Draft` | 1 | header + child rows updated in place |
| 2 | sends to peer review | `Peer Review` | 1 | revision 1 snapshot (`submitted_at`), history `Draft → Peer Review` |
| 3 | reviewer rejects, "ambient temp wrong" | `Rejected` | 1 | revision 1 `decided_at` set; history `Peer Review → Rejected` **with the comment** |
| 4 | engineer edits again | `Draft` | **2** | history `Rejected → Draft`; revision 1 stays frozen |
| 5 | resubmits | `Peer Review` | 2 | revision 2 snapshot |
| 6 | approved | `Approved` | 2 | revision 2 `decided_at`; history `Peer Review → Approved` |

Revision 1 remains readable forever, with its own values and timestamps. That
makes all of these answerable:

```sql
-- which datasheets were rejected, by whom, and why
SELECT d.tco_id, d.test_code, h.actor_name, h.comment, h.created_at
FROM datasheet_status_history h JOIN datasheet d ON d.id = h.datasheet_id
WHERE h.to_status = 'Rejected' ORDER BY h.created_at DESC;

-- how long does peer review take, per reviewer
SELECT actor_name, AVG(TIMESTAMPDIFF(HOUR, submitted_at, decided_at)) hrs
FROM datasheet_revision r JOIN datasheet_status_history h
  ON h.datasheet_id = r.datasheet_id AND h.revision_no = r.revision_no
WHERE h.to_status IN ('Approved','Rejected') GROUP BY actor_name;

-- what changed between revision 1 and 2 of this datasheet
SELECT revision_no, result, ambient_temperature, tested_by, created_at
FROM datasheet_revision WHERE datasheet_id = ? ORDER BY revision_no;
```

### Status values

```
Draft ──submit──► Peer Review ──approve──► Approved
                       │
                       └──reject──► Rejected ──edit──► Draft (revision_no + 1)
```

`Draft · Peer Review · Approved · Rejected`. If admin sign-off should also land
on the datasheet (rather than only on the report), add `Accepted` as a second
decision with its own actor and timestamp — see §9.

---

## 6. Draft autosave (answers Q5 and Q6)

**How it behaves today:** the form debounces **1500 ms** after a `change`/blur
event (not per keystroke) and POSTs the whole form to `/save-draft`. A payload
is **43–131 fields, 3–6.5 KB**. Server-side that is one
`INSERT … ON DUPLICATE KEY UPDATE` of `form_json` — one row, one column.

**What changes:** exactly what you proposed. The autosave still writes
`form_json` first — that remains the durable record — and then projects into the
new tables in the *same transaction*:

```
POST /save-draft
  ├─ 1. UPSERT datasheet_records.form_json          (unchanged, still the truth)
  └─ 2. project(datasheet_id):                       (new, same transaction)
       ├─ UPSERT datasheet             1 row
       ├─ UPSERT datasheet_<code>      1 row
       └─ DELETE + INSERT child rows   WHERE datasheet_id = ?   (~5–90 rows)
```

Why this is safe at this shape:

* the work is **bounded by one datasheet** — at worst ~90 child rows, all
  keyed by `datasheet_id`, so the delete is an index range hit;
* it happens **at most once per 1.5 s per engineer**, and only after they leave
  a field — not per character;
* **delete-and-reinsert children** rather than diffing. Diffing 90 rows costs
  more than replacing them, and replacement is idempotent, which is what makes
  the backfill re-runnable.

Two guarantees:

1. **Projection failure never blocks a save.** If step 2 raises, step 1 is
   already committed and the error is logged. The engineer's work is safe; the
   projection self-heals on the next save or on a backfill run.
2. **No revision snapshot on autosave.** Drafts update current state in place.
   Snapshots happen only at submit/approve/reject.

If projection latency ever does become visible (many concurrent engineers), the
escape hatch is a `needs_projection` flag on `datasheet_records` plus a small
worker — but at 11 datasheets and a handful of engineers that would be
premature.

---

## 7. Migration — no data loss, reversible

| Phase | What happens | Risk |
|---|---|---|
| 1 | Create the new tables via an idempotent `ensure_projection_tables()` at startup — the pattern already used by `schema.py`, `records.py`, `fixed_store.py`. **No ALTER on any existing table.** | none |
| 2 | Add `datasheet_gen/projection.py` — pure function, `form_json → rows`. Reuses `report_gen`'s grid extractors. | none |
| 3 | Backfill: `python -m datasheet_gen.projection --all`. Idempotent, re-runnable, reports per-record success. | none |
| 4 | Call `project()` from `records.upsert_record()`, wrapped best-effort. | low |
| 5 | Record status transitions in `app.py::_apply_peer_review_action` and at submit. | low |
| 6 | Point the NL search at the new tables. | none |

**Rollback:** `DROP TABLE` the new tables. `datasheet_records` is never
modified, so there is nothing to restore.

**Schema drift:** the datasheet schemas are generated artifacts (`spec_build.py`
regenerates schema + Word template together). When a field is renamed, you
change the projection mapping and re-run the backfill — you do not migrate data,
because the truth never moved.

## 8. Code change surface

| File | Change |
|---|---|
| `datasheet_gen/projection.py` | **new** — projection + backfill CLI |
| `datasheet_gen/schema.py` | `+ ensure_projection_tables()` |
| `datasheet_gen/records.py` | +2 lines: call `project()` after upsert |
| `app.py` `_apply_peer_review_action` | ~6 lines: write status history + revision on approve/reject |
| **datasheet form UI / templates** | **none** |

The UI reads through exactly two functions — `records.draft_form()` and
`records.draft_images()`, called from six places — and both keep reading
`form_json`. So the forms, the autosave, the image editor and the report
generator all keep working untouched.

## 9. Decisions I need from you

1. **`Accepted` vs `Approved`** — is admin sign-off a separate datasheet state,
   or is peer-review approval the only decision? (Today admin sign-off applies
   to the *report*, covering all of a request's datasheets.)
2. **`datasheet_observation`** (§4.4) — worth building the thin verdict table,
   or keep observations JSON-only? It is the difference between *"show every
   non-A observation across all tests"* being one query or eleven.
3. **Typed vs text columns** — store `ambient_temperature` as `DECIMAL(6,2)`
   (clean aggregation, NULL when the engineer typed something unparseable) or as
   `VARCHAR` (never loses the literal)? I recommend typed, since `form_json`
   already keeps the literal.
