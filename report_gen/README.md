# report_gen — the consolidated IEC-FRM-516 EMI EMC Test Report

One report per **request**. (`datasheet_gen` produces one datasheet per *test*;
this package combines a request and all of its approved datasheets into the
official customer-facing report.)

```
Requester submits request ──► tests planned ──► engineer fills a datasheet per test
        └──────────────► peer review approves each datasheet
                                 └──► "Generate Test Report" ──► report_gen ──► IEC-FRM-516 .docx
```

## Where it is triggered

Nothing new was added to the UI — the existing button is reused:

| | |
|---|---|
| Button | **Generate Test Report** on `/review` ([templates/review.html](../templates/review.html), ~line 804) |
| Endpoint | `POST /api/test-requests/<id>/generate-test-report` ([app.py](../app.py), `generate_test_report`) |
| Entry point | `report_gen.build_request_test_report(request, planner_entries, output_path)` |
| Output | `uploads/reports/<request_id>/<TCO>_Test_Report_<ts>.docx` |
| After generating | every non-cancelled planner entry → `report_uploaded`, request → `Draft Report`, then the normal Proceed → Admin Sign Off → Completed flow |

The button only appears once **every** test selected on the request is finished:
all planner entries `cancelled`/`datasheet_uploaded`, each approved one has a
file, no test still unscheduled, and no report uploaded yet.

## Why template surgery rather than a template engine

The official form (`word_templates/IEC-FRM-516_REPORT.docx`) is a **blank
document**, not a docxtpl template: 74 tables, heavily merged headers, 175
checkbox *content controls*, auto-numbered headings, and live `TOC`/`SEQ`/`PAGE`
fields. Marking that up by hand would be laborious and would not express the
conditional behaviour the report needs (a request contains only *some* tests).

So the generator opens the official document and edits it in place:

1. **Prune** — the Heading-1 section of every test not in the request is deleted
   whole. Because headings are list-numbered (`numId=6`) and captions use `SEQ`
   fields, Word renumbers the remaining sections and figures by itself.
2. **Fill** — values are written into existing cells while preserving the run
   formatting already there, so the document keeps its Arial 11 / bold look.
3. **Tick** — checkboxes are set through their content control's `w14:checked`
   state *and* the cached glyph. Setting only one leaves Word inconsistent.
4. **Finish** — Arial is enforced (except on ballot-box glyph runs, which have no
   Arial equivalent), image borders are added, stale field results are cleared,
   and `w:updateFields` is set.

## Page numbers, table of contents, figure numbers

The TOC, the lists of figures/photos/tables, the Figure/Photo/Table numbers and
"Page X of Y" are all **Word fields**. Python cannot lay out pages, so the
generator clears the template's stale cached values (which referred to the
original 40-page document) and sets `<w:updateFields w:val="true"/>`. Word then
computes the real numbers the first time the file is opened.

**Consequence:** immediately after generation the captions read `Photo :` and the
TOC is blank. That is expected — open the file in Word once and everything
populates. There is no way to bake correct page numbers in without a rendering
engine (see `docx_tools.refresh_fields_on_open`).

## Where the data comes from

| Report part | Source |
|---|---|
| Cover, 1.2, 1.3, **2.x** | `EMCRequest` + its child tables (accessories, cables, standards, supply V/F, functional modes, categories, decision rules) |
| 1.1 Test Method | one row per reported test; spec derived per test, verdict from the datasheet |
| 1.4 Measurement Uncertainty | `datasheet_fixed_values` (admin-editable at `/datasheet/admin/config`) |
| **3** Immunity Criteria & Decision Rule | **static**, except the request's chosen decision rule is ticked |
| **4..14** per-test sections | `datasheet_records.form_json` / `images_json`, reached through the test's planner entry |
| Test equipment / software | the datasheet's rows, falling back to the Equipment Master (same selector the datasheet form uses) |

Anything the database has no source for — Software/Firmware details (2.3), the
monitoring screenshot (2.8), EUT photos 1–2 (2.10), ULR No, signature images,
test location — **keeps its `<angle-bracket>` prompt** so whoever finalises the
report can see what is still pending.

## How the per-test mapping works

The report's per-test sections and the per-test datasheets were generated from
the same IEC-FRM-5xx source forms, so their TEST SPECIFICATION rows line up.
`mapping.resolve_key` matches a report row label against the datasheet schema's
field labels and resolves **187 of 197** rows automatically.

The other ten are rows the report prints as a *matrix* while the datasheet stores
separate scalars; each has an explicit handler in `mapping.SPEC_HANDLERS`:

| Test | Row | Why |
|---|---|---|
| EFT | Number of Test Ports, Test Voltage | two columns (power / signal), cumulative levels |
| SURGE | Test Port, Test Voltage(kV) | CM/DM × power/signal grid |
| ESD | Indirect Contact Discharge HCP/VCP | two option lists in one cell |
| PFMF | Coil Orientation, Test Level | multi-select angles + axes |
| Voltage Dips | Test Level | 3-row merged percentage/duration block |
| all | Classification | Group in one cell, Class in the next |

Observation grids reuse `datasheet_gen.generic_service`'s own builders
(`_eft_obs`, `_surge_obs`, `_vdips_groups`, …) so the report and the datasheet can
never disagree about what a cell means.

Images are matched to captions by **caption text**: once the auto-numbered
`Photo N:` prefix is dropped, the report and the datasheet name the same picture
identically. Slot order is the fallback.

## Growing the document

When a datasheet captured more than the template prints slots for — PFMF takes 3
setup photos against 1 slot, CE/RE add measurement plots — the generator clones
the caption block and inserts a real `SEQ` field, so the extras are numbered in
sequence and appear in the lists of figures/photos.

The CE and RE **measurement grids** are a related case: the template captions
them (`Table 3: CE_Line_…`) but ships without the tables, so they are built from
the datasheet's rows. A caption with no data gets no empty grid.

## Files

| File | Role |
|---|---|
| `builder.py` | orchestrator: prune → fill → finish. `build_report()` |
| `service.py` | data gathering; returns plain dicts, so it is testable without Word |
| `mapping.py` | label→key resolution, the ten explicit row handlers, observation/table extraction |
| `docx_tools.py` | generic Word surgery: block traversal, deletion, cell writing, checkboxes, images, tables, fonts, fields |
| `registry.py` | section ⇄ test-code map, subsection names, 1.1/1.4 row labels |
| `word_templates/IEC-FRM-516_REPORT.docx` | the official blank form, used as the base |

## Adding a test type

1. Add the Heading-1 title ⇄ code pair to `registry.SECTION_TO_CODE` (in
   document order) and its port to `registry.TEST_METHOD_PORT`.
2. Add its row label to `registry.TEST_METHOD_ROWS`.
3. Map the request-side test code in `service.REQUEST_CODE_TO_REPORT`.
4. Run the generator and check `summary["per_test"]`'s `unresolved` count; add a
   handler to `mapping.SPEC_HANDLERS` for any row that did not resolve.
