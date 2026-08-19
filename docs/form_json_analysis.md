# form_json analyses

Every query below was run against `test_plan_generator` and the result it returned is printed with it. Regenerate with `python tools_form_analysis.py`; the results move as the data does, the queries do not.

`form_json` is the datasheet exactly as the engineer submitted it. The projection pulls its substance into columns and does that well - what it cannot carry is the shape of the form. A field that was **asked and left empty** is indistinguishable from a field that does not exist once it is a NULL column, and *how much of this datasheet is filled in* is a question about the form rather than about any row.

That matters here because two of the six peer-review rejections in this database are `INCOMPLETE_OBS` - a reviewer sending work back over a half-filled grid - and no projected column finds the next one before a reviewer does.

## Where it lives

| table | what its form_json is |
|---|---|
| `datasheet_records.form_json` | the CURRENT form, overwritten on every save |
| `datasheet_revision.form_json` | the form FROZEN at each submission - the history |
| `datasheet_draft_history.form_json` | every autosave, including drafts never submitted |

Use `datasheet_records` for "how are things now", `datasheet_revision` for anything comparing attempts. `JSON_VALID(form_json)` first, always: the column is `longtext`, so nothing in the schema guarantees it parses.

## A note on JSON_TABLE

Several queries walk every field without naming any, which needs `JSON_TABLE` plus a dynamic path built with `CONCAT`. **`sql_guard` blocks `JSON_TABLE`**, so the assistant cannot run those itself - they are for a SQL client. The ones using only `JSON_EXTRACT` it can run, and those are marked.

---

## A1. How much of each datasheet is actually filled in

**Answers:** Which datasheets are thin enough that a reviewer will send them back, before a reviewer has to?

**The assistant can run this:** yes - JSON_EXTRACT only

```sql
SELECT r.tco_id, r.test_code, r.status,
       JSON_LENGTH(JSON_KEYS(r.form_json))                          AS keys_total,
       CHAR_LENGTH(r.form_json)                                     AS form_bytes
FROM datasheet_records r
WHERE JSON_VALID(r.form_json)
ORDER BY keys_total
```

Returned 12 row(s):

| tco_id | test_code | status | keys_total | form_bytes |
|---|---|---|---|---|
| DEMO-EMC-301 | RE | Not Submitted | 16 | 499 |
| DEMO-EMC-303 | VOLTAGEFLICKER | Not Submitted | 51 | 2530 |
| DEMO-EMC-302 | CRF | Not Submitted | 53 | 2691 |
| DEMO-EMC-302 | PFMF | Not Submitted | 62 | 2901 |
| IEC-EMC-004 | HARMONIC | Submitted | 65 | 7636 |
| DEMO-EMC-303 | SURGE | Not Submitted | 71 | 3953 |
| DEMO-EMC-302 | RS_RI | Not Submitted | 76 | 3474 |
| DEMO-EMC-303 | EFT | Not Submitted | 84 | 4414 |
| DEMO-EMC-301 | ESD | Not Submitted | 128 | 3242 |
| DEMO-EMC-304 | ESD | Not Submitted | 131 | 3398 |
| IEC-EMC-006 | CE | Not Submitted | 147 | 6086 |
| DEMO-EMC-301 | CE | Not Submitted | 147 | 6156 |

**Why it is worth running:** NO PERCENTAGE, deliberately. The obvious denominator is the field count in datasheet_gen/schemas/<CODE>.json, and it does not work: a schema defines a grid ONCE and the form expands it into one key per cell, so a correctly filled ESD sheet came out at 242% complete, and CE has no schema file at all because its form is hand-built HTML. A ratio nobody can defend is worse than a raw count.

What the raw count is good for: comparing two forms of the SAME test code, where the shape is identical. The two ESD sheets at 128 and 131 keys are comparable and close. DEMO-EMC-301's RE sheet at 16 keys is not comparable to anything here - but see the grid-cell count, which is zero for it and dozens for every other test. A datasheet with no grid cells at all has had nothing measured recorded on it.

---

## A2. Fields that were asked and left empty

**Answers:** Which specific fields do engineers skip?

**The assistant can run this:** no - needs JSON_TABLE, which sql_guard blocks

```sql
SELECT ks.k AS field, COUNT(*) AS forms_leaving_it_blank

    FROM datasheet_records r
    JOIN JSON_TABLE(JSON_KEYS(r.form_json), '$[*]'
                    COLUMNS (k VARCHAR(200) PATH '$')) AS ks
    JOIN LATERAL (SELECT JSON_UNQUOTE(JSON_EXTRACT(
                    r.form_json, CONCAT('$."', ks.k, '"'))) AS v) AS x
    WHERE JSON_VALID(r.form_json)

  AND (x.v IS NULL OR x.v = '' OR x.v = '[]')
GROUP BY ks.k
ORDER BY forms_leaving_it_blank DESC, field
LIMIT 25
```

Returned 7 row(s):

| field | forms_leaving_it_blank |
|---|---|
| test_procedure_manual | 3 |
| peer_reviewer_id | 2 |
| photo_caption | 2 |
| test_date | 2 |
| tested_by_date | 2 |
| date | 1 |
| failure_reason_code | 1 |

**Why it is worth running:** A blank here is a field the form PUT IN FRONT of the engineer and they moved past. That is different information from a NULL column, which cannot tell you whether anyone was ever asked.

---

## A3. Grid fill rate - the INCOMPLETE_OBS signal

**Answers:** Which measurement or observation grids are half empty?

**The assistant can run this:** no - needs JSON_TABLE, which sql_guard blocks

```sql
SELECT r.tco_id, r.test_code,
       REGEXP_REPLACE(ks.k, '_r[0-9]+_c[0-9]+$', '')    AS grid,
       COUNT(*)                                         AS cells,
       SUM(x.v IS NULL OR x.v = '')                     AS empty_cells,
       ROUND(100 * SUM(x.v IS NULL OR x.v = '') / COUNT(*)) AS pct_empty

    FROM datasheet_records r
    JOIN JSON_TABLE(JSON_KEYS(r.form_json), '$[*]'
                    COLUMNS (k VARCHAR(200) PATH '$')) AS ks
    JOIN LATERAL (SELECT JSON_UNQUOTE(JSON_EXTRACT(
                    r.form_json, CONCAT('$."', ks.k, '"'))) AS v) AS x
    WHERE JSON_VALID(r.form_json)

  AND ks.k REGEXP '_r[0-9]+_c[0-9]+$'
GROUP BY r.id, grid
ORDER BY pct_empty DESC, cells DESC
LIMIT 25
```

Returned 6 row(s):

| tco_id | test_code | grid | cells | empty_cells | pct_empty |
|---|---|---|---|---|---|
| DEMO-EMC-301 | ESD | ind | 48 | 0 | 0 |
| DEMO-EMC-304 | ESD | ind | 48 | 0 | 0 |
| DEMO-EMC-301 | ESD | air | 18 | 0 | 0 |
| DEMO-EMC-301 | ESD | dir | 18 | 0 | 0 |
| DEMO-EMC-304 | ESD | air | 18 | 0 | 0 |
| DEMO-EMC-304 | ESD | dir | 18 | 0 | 0 |

**Why it is worth running:** Two of the six rejections in this database are INCOMPLETE_OBS, and the reviewer comment on one reads "indirect discharge grid is only filled for the first four points. HCP 180 and 270 and both VCP rows are empty." That is this query, run by a human eye.

The grid name is recovered by stripping the row/column suffix: an ESD observation grid is stored as one key per cell - `ind_r5_c1`, `air_r2_c3` - not as a list, which is why a LIKE on `__c` finds nothing. `ind`, `air` and `dir` are indirect, air and direct discharge. The other convention, `base__cN[]`, is a list per column and is what A2 sees.

---

## A4. What engineers change after a rejection

**Answers:** Across every rejection, which fields get corrected most often?

**The assistant can run this:** no - needs JSON_TABLE, which sql_guard blocks

```sql
SELECT ks.k AS field, COUNT(DISTINCT v.datasheet_id) AS datasheets_where_it_changed
FROM datasheet_revision v
JOIN datasheet_revision prev
  ON prev.datasheet_id = v.datasheet_id
 AND prev.revision_no = v.revision_no - 1
JOIN JSON_TABLE(JSON_KEYS(v.form_json), '$[*]'
                COLUMNS (k VARCHAR(200) PATH '$')) AS ks
WHERE JSON_VALID(v.form_json) AND JSON_VALID(prev.form_json)
  AND NOT (JSON_EXTRACT(v.form_json,    CONCAT('$."', ks.k, '"'))
       <=> JSON_EXTRACT(prev.form_json, CONCAT('$."', ks.k, '"')))
GROUP BY ks.k
ORDER BY datasheets_where_it_changed DESC, field
LIMIT 25
```

Returned 25 row(s):

| field | datasheets_where_it_changed |
|---|---|
| deviation | 3 |
| ind_r5_c1 | 1 |
| ind_r5_c2 | 1 |
| ind_r5_c3 | 1 |
| ind_r5_c4 | 1 |
| ind_r5_c5 | 1 |
| ind_r5_c6 | 1 |
| ind_r6_c1 | 1 |
| ind_r6_c2 | 1 |
| ind_r6_c3 | 1 |
| ind_r6_c4 | 1 |
| ind_r6_c5 | 1 |
| ind_r6_c6 | 1 |
| ind_r7_c1 | 1 |
| ind_r7_c2 | 1 |
| ind_r7_c3 | 1 |
| ind_r7_c4 | 1 |
| ind_r7_c5 | 1 |
| ind_r7_c6 | 1 |
| ind_r8_c1 | 1 |

*... 5 more rows.*

**Why it is worth running:** review_history answers this for ONE datasheet. Aggregated across all of them it stops being a story about one sheet and becomes the list of what this lab's forms get wrong - which is the list worth fixing in the form itself, not one datasheet at a time. <=> is MySQL's NULL-safe compare: without it a field appearing for the first time reads as unchanged.

---

## A5. Test conditions as they were actually typed

**Answers:** Are the recorded ambient conditions plausible?

**The assistant can run this:** yes - JSON_EXTRACT only

```sql
SELECT r.tco_id, r.test_code,
       JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.ambient_temperature')) AS temp_c,
       JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.relative_humidity'))   AS rh_pct,
       JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.test_date'))           AS test_date
FROM datasheet_records r
WHERE JSON_VALID(r.form_json)
ORDER BY r.test_code
```

Returned 12 row(s):

| tco_id | test_code | temp_c | rh_pct | test_date |
|---|---|---|---|---|
| IEC-EMC-006 | CE | 10 | 10 |  |
| DEMO-EMC-301 | CE | 10 | 10 | 17/08/2026 |
| DEMO-EMC-302 | CRF | 23.2 | 46 | 17/08/2026 |
| DEMO-EMC-303 | EFT | 23.2 | 46 | 17/08/2026 |
| DEMO-EMC-301 | ESD | 23.4 | 48 | 17/08/2026 |
| DEMO-EMC-304 | ESD | 22.8 | 51 | 17/08/2026 |
| IEC-EMC-004 | HARMONIC | 4 | 4 |  |
| DEMO-EMC-302 | PFMF | 23.2 | 46 | 17/08/2026 |
| DEMO-EMC-301 | RE | 23.1 | 47 |  |
| DEMO-EMC-302 | RS_RI |  |  | 17/08/2026 |
| DEMO-EMC-303 | SURGE | 23.2 | 46 | 17/08/2026 |
| DEMO-EMC-303 | VOLTAGEFLICKER | 23.2 | 46 | 17/08/2026 |

**Why it is worth running:** These are strings, not numbers, so nothing has ever validated them. A standard expects roughly 15-35 C and 25-75% RH; anything outside that was either a real excursion worth noting on the report or a keystroke, and the two look identical in a column.

---

## A6. Sign-off completeness

**Answers:** Which submitted datasheets have no name, date or signature on them?

**The assistant can run this:** yes - JSON_EXTRACT only

```sql
SELECT r.tco_id, r.test_code, r.status,
       COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.tested_by_name')), ''),
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.tested_by')),      ''),
                '(nobody)')                                        AS tested_by,
       CASE WHEN JSON_EXTRACT(r.form_json, '$.signature') IS NULL
             OR JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.signature')) = ''
            THEN 'MISSING' ELSE 'present' END                      AS signature,
       COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.test_date')), ''),
                '(none)')                                          AS test_date
FROM datasheet_records r
WHERE JSON_VALID(r.form_json)
ORDER BY signature DESC, r.test_code
```

Returned 12 row(s):

| tco_id | test_code | status | tested_by | signature | test_date |
|---|---|---|---|---|---|
| DEMO-EMC-302 | CRF | Not Submitted | Krishna Muthangi | present | 17/08/2026 |
| DEMO-EMC-303 | EFT | Not Submitted | engineer1 | present | 17/08/2026 |
| DEMO-EMC-302 | PFMF | Not Submitted | Krishna Muthangi | present | 17/08/2026 |
| DEMO-EMC-302 | RS_RI | Not Submitted | Krishna Muthangi | present | 17/08/2026 |
| DEMO-EMC-303 | SURGE | Not Submitted | engineer1 | present | 17/08/2026 |
| DEMO-EMC-303 | VOLTAGEFLICKER | Not Submitted | engineer1 | present | 17/08/2026 |
| IEC-EMC-006 | CE | Not Submitted | Krishna Gonela | MISSING | (none) |
| DEMO-EMC-301 | CE | Not Submitted | Krishna Gonela | MISSING | 17/08/2026 |
| DEMO-EMC-301 | ESD | Not Submitted | DEMO Engineer | MISSING | 17/08/2026 |
| DEMO-EMC-304 | ESD | Not Submitted | DEMO Engineer | MISSING | 17/08/2026 |
| IEC-EMC-004 | HARMONIC | Submitted | Krishna Gonela | MISSING | (none) |
| DEMO-EMC-301 | RE | Not Submitted | DEMO Engineer | MISSING | (none) |

**Why it is worth running:** MISSING_SIGNATURE is one of the sixteen rejection codes, so this is a rejection somebody can avoid rather than receive.

---

## A7. Deviations engineers actually wrote down

**Answers:** Where did the test depart from the standard, in their own words?

**The assistant can run this:** yes - JSON_EXTRACT only

```sql
SELECT r.tco_id, r.test_code,
       JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.deviation')) AS deviation
FROM datasheet_records r
WHERE JSON_VALID(r.form_json)
  AND JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.deviation')) NOT IN ('', 'NA', 'N/A', 'None', '-')
ORDER BY r.test_code
```

Returned 3 row(s):

| tco_id | test_code | deviation |
|---|---|---|
| DEMO-EMC-301 | CE | Calibration date added after review. |
| DEMO-EMC-304 | ESD | Indirect discharge grid completed for all eight points. |
| DEMO-EMC-302 | PFMF | Missing orientations added after review. |

**Why it is worth running:** DEVIATION_UNDOC is another of the sixteen codes. Free text, so no aggregate is honest here - it is a read-through, not a metric.

---

## A8. Fields one form of a test type has and another does not

**Answers:** Are two engineers filling the same form differently?

**The assistant can run this:** no - needs JSON_TABLE, which sql_guard blocks

```sql
SELECT r.test_code, ks.k AS field,
       COUNT(*)                                   AS forms_with_it,
       (SELECT COUNT(*) FROM datasheet_records r2
         WHERE r2.test_code = r.test_code
           AND JSON_VALID(r2.form_json))          AS forms_of_this_test

    FROM datasheet_records r
    JOIN JSON_TABLE(JSON_KEYS(r.form_json), '$[*]'
                    COLUMNS (k VARCHAR(200) PATH '$')) AS ks
    JOIN LATERAL (SELECT JSON_UNQUOTE(JSON_EXTRACT(
                    r.form_json, CONCAT('$."', ks.k, '"'))) AS v) AS x
    WHERE JSON_VALID(r.form_json)

GROUP BY r.test_code, ks.k
HAVING forms_with_it < forms_of_this_test
ORDER BY r.test_code, field
LIMIT 30
```

Returned 5 row(s):

| test_code | field | forms_with_it | forms_of_this_test |
|---|---|---|---|
| CE | assignment_id | 1 | 2 |
| CE | failure_reason_code | 1 | 2 |
| ESD | eut_model_sku_number | 1 | 2 |
| ESD | eut_serial_number | 1 | 2 |
| ESD | tested_by_name | 1 | 2 |

**Why it is worth running:** Only meaningful where a test code has more than one datasheet. On this database that is CE and ESD; everything else has a single form and cannot disagree with itself.

---

## A9. Grid fill rate AT SUBMISSION, per revision

**Answers:** Which grids were half empty at the moment the engineer submitted - the state a reviewer was looking at?

**The assistant can run this:** no - needs JSON_TABLE, which sql_guard blocks

```sql
SELECT d.tco_id, d.test_code, v.revision_no,
       REGEXP_REPLACE(ks.k, '_r[0-9]+_c[0-9]+$', '')    AS grid,
       COUNT(*)                                         AS cells,
       SUM(x.v IS NULL OR x.v = '')                     AS empty_cells,
       ROUND(100 * SUM(x.v IS NULL OR x.v = '') / COUNT(*)) AS pct_empty,
       (SELECT h.reason_code FROM datasheet_status_history h
         WHERE h.datasheet_id = v.datasheet_id
           AND h.revision_no  = v.revision_no
           AND h.to_status    = 'Rejected' LIMIT 1)     AS sent_back_for
FROM datasheet_revision v
JOIN `datasheet` d ON d.id = v.datasheet_id
JOIN JSON_TABLE(JSON_KEYS(v.form_json), '$[*]'
                COLUMNS (k VARCHAR(200) PATH '$')) AS ks
JOIN LATERAL (SELECT JSON_UNQUOTE(JSON_EXTRACT(
                v.form_json, CONCAT('$."', ks.k, '"'))) AS v) AS x
WHERE JSON_VALID(v.form_json)
  AND ks.k REGEXP '_r[0-9]+_c[0-9]+$'
GROUP BY v.id, grid
ORDER BY pct_empty DESC, d.tco_id, v.revision_no
LIMIT 25
```

Returned 12 row(s):

| tco_id | test_code | revision_no | grid | cells | empty_cells | pct_empty | sent_back_for |
|---|---|---|---|---|---|---|---|
| DEMO-EMC-304 | ESD | 1 | ind | 48 | 24 | 50 | CAL_EXPIRED |
| DEMO-EMC-304 | ESD | 2 | ind | 48 | 24 | 50 | INCOMPLETE_OBS |
| DEMO-EMC-301 | ESD | 1 | air | 18 | 0 | 0 |  |
| DEMO-EMC-301 | ESD | 1 | dir | 18 | 0 | 0 |  |
| DEMO-EMC-301 | ESD | 1 | ind | 48 | 0 | 0 |  |
| DEMO-EMC-304 | ESD | 1 | air | 18 | 0 | 0 | CAL_EXPIRED |
| DEMO-EMC-304 | ESD | 1 | dir | 18 | 0 | 0 | CAL_EXPIRED |
| DEMO-EMC-304 | ESD | 2 | air | 18 | 0 | 0 | INCOMPLETE_OBS |
| DEMO-EMC-304 | ESD | 2 | dir | 18 | 0 | 0 | INCOMPLETE_OBS |
| DEMO-EMC-304 | ESD | 3 | air | 18 | 0 | 0 |  |
| DEMO-EMC-304 | ESD | 3 | dir | 18 | 0 | 0 |  |
| DEMO-EMC-304 | ESD | 3 | ind | 48 | 0 | 0 |  |

**Why it is worth running:** A3 runs on datasheet_records, which holds the CURRENT form - so a grid that was half empty when it was rejected reads 0% there, because the engineer has since filled it in. This runs on the frozen revisions instead, which is the state the reviewer actually saw, and puts the rejection code beside it.

This is the one to run before submitting rather than after. A grid at 40% with no reason_code yet is the next INCOMPLETE_OBS.

