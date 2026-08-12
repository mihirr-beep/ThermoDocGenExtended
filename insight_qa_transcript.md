# Every insight question asked, and what the chatbot replied

17 questions through the real pipeline on gpt-5-nano, against the seeded DEMO
corpus plus the lab's genuine data. Verdicts from tools_insight_coverage.py.
Three FAIL verdicts turned out to be my expectation being wrong rather than
the answer; those are marked MY ERROR.

### 1. supported / kept-failing  --  PASS

**Q:** The DEMO Aurora Centrifuge C5 kept failing. What was actually wrong with it?

**A:** The DEMO Aurora Centrifuge C5 failed due to exceeding the CE limit for electromagnetic interference (EMI). Specifically, it exceeded the class B quasi-peak limit at 0.72 MHz, with failures recorded as follows:
- DEMO-EMC-201 on 2025-12-15: measured 60.8 dB against a limit of 56.0 dB, over by 4.8 dB.
- DEMO-EMC-202 on 2026-01-05: measured 60.5 dB against a limit of 56.0 dB, over by 4.5 dB.
- DEMO-EMC-203 on 2026-01-26: measured 57.9 dB against a limit of 56.0 dB, over by 1.9 dB.
After modifications, including a 4.7 mH common-mode choke and a 2.2 nF Y-capacitor, the centrifuge passed the CE test on 2026-02-16 (DEMO-EMC-204).

---

### 2. supported / how-many-attempts  --  FAIL  (scoring error - see note)

**Q:** How many attempts did the DEMO Aurora Centrifuge C5 need before it passed?

**A:** The DEMO Aurora Centrifuge C5 needed 3 attempts before it passed. The sequence of results is as follows: DEMO-EMC-201 (CE) FAIL; DEMO-EMC-202 (CE) FAIL [CAL_EXPIRED]; DEMO-EMC-203 (CE) FAIL. The data does not provide information about the subsequent test that resulted in a pass.

*scored:* MISSING : 4 / four

*MY ERROR:* 3 attempts before the pass is correct; the case demanded 4

---

### 3. supported / came-down  --  FAIL  (scoring error - see note)

**Q:** Show me how the emissions on the DEMO Aurora Centrifuge C5 came down across its tests.

**A:** Answer: Emissions decreased across tests after targeted EMI modifications. Three CE tests failed at 0.72 MHz with progressively smaller worst-case readings, then a pass after changes.
- Timeline (CE tests for DEMO Aurora Centrifuge C5)
- 2025-12-15 (tco_id DEMO-EMC-201): CE, FAIL; worst at 0.72 MHz = 60.8 (limit 56.0).
- 2026-01-05 (tco_id DEMO-EMC-202): CE, FAIL; worst at 0.72 MHz = 60.5 (limit 56.0).
- 2026-01-26 (tco_id DEMO-EMC-203): CE, FAIL; worst at 0.72 MHz = 57.9 (limit 56.0).
- 2026-02-16 (tco_id DEMO-EMC-204): CE, PASS; met_performance_criteria A.
- Modifications introduced before the passing test
- Introduced between the last two tests (203 -> 204):
- common-mode choke 4.7 mH at the mains inlet
- Y-capacitor 2.2 nF across line-neutral
- Ferrite sleeve on mains inlet cable was already present.
What this shows
- The worst-case CE reading at 0.72 MHz trended downward (60.8 → 60.5 → 57.9) as the modifications were added, and the test sequence ended with a PASS after the modifications.
SQL shapes used
-.timeline: analyse_history(analysis="timeline", product="DEMO Aurora Centrifuge C5")
-.modifications_before_pass: analyse_history(analysis="modifications_before_pass", product="DEMO Aurora Centrifuge C5")
Note: The final pass indic

*scored:* MISSING : 52.6

*MY ERROR:* the 60.8 -> 60.5 -> 57.9 trend IS the answer; the case demanded 52.6

---

### 4. supported / vega-fix  --  FAIL

**Q:** What did the DEMO Vega Incubator V2 have fitted to get it through?

**A:** I could not verify one part of my answer (30, 9), so here is what the database actually returned instead - these rows are exact:
field                     value
------------------------  ----------------------
tco_id                    DEMO-EMC-206
product_name              DEMO Vega Incubator V2
model_number              VEG-V2-110
test_code                 CE
test_date                 2026-04-09
result                    PASS
failure_reason_code
met_performance_criteria  A
review_rounds             1
record_rejected_for
is_synthetic              1
tco_id                    DEMO-EMC-205
... and 17 more row(s)
Ask me about any single line above and I will go deeper.

*scored:* MISSING : a written answer - got a raw evidence dump

---

### 5. supported / same-reason  --  PASS

**Q:** Did the DEMO Orion Analyzer O9 and the DEMO Aurora Centrifuge C5 fail for the same reason?

**A:** Yes. For both DEMO Orion Analyzer O9 and DEMO Aurora Centrifuge C5, the recorded failure_reason_code across all campaigns is CE_LIMIT_EXCEEDED.
- Orion O9: 2 campaigns, both CE_LIMIT_EXCEEDED (with a MISSING_PHOTO issue noted in one peer-review).
- Aurora C5: 3 campaigns, all CE_LIMIT_EXCEEDED (one submission also flagged CAL_EXPIRED and later re-issued; one peer-review note about a missing LISN photo).

---

### 6. real / real-history  --  PASS

**Q:** Give me the testing history of the Full-Scope EMC Sample Unit.

**A:** Timeline history for Full-Scope EMC Sample Unit:
- 34 campaigns in total; 10 failed, 23 passed. This is the timeline data only; it lists what was measured and the per-campaign results, not reasons why.
- IEC-EMC-010, model FSES-2026, test_date 2026-08-10:
- CE: FAIL
- RE: FAIL
- ESD: D
- EFT: D
- SURGE: D
- CRF: D (review_rounds=15)
- RS_RI: D
- HARMONIC: FAIL
- VOLTAGEFLICKER: FAIL
- PFMF: D
- VOLTAGEDIPS: no result listed
- IEC-EMC-011, test_date 2026-08-11:
- ESD: A
- VOLTAGEDIPS: A
- HARMONIC: A
- EFT: A
- RS_RI: A
- (PFMF/CRF not shown in this block)
- IEC-EMC-012, test_date 2026-08-11:
- ESD: A
- SURGE: A
- HARMONIC: A
- RS_RI: A
- VOLTAGEDIPS: A
- IEC-EMC-013, test_date 2026-08-11:
- CE: A
- RE: A
- ESD: A
- EFT: A
- SURGE: A
- CRF: A
- RS_RI: A
- HARMONIC: A
- VOLTAGEFLICKER: A
- PFMF: A
- VOLTAGEDIPS: A
- IEC-EMC-014, test_date 2026-08-11:
- EFT: A
- ESD: A
- RS_RI: A
- SURGE: A
analyse_history(analysis="timeline", product="Full-Scope EMC Sample Unit")

---

### 7. real / real-why-failed  --  FAIL

**Q:** Why did the Full-Scope EMC Sample Unit fail on IEC-EMC-010?

**A:** The Full-Scope EMC Sample Unit failed on IEC-EMC-010 due to repeated reviewer notes indicating the need to "please correct the limits."
The test conducted on 2026-08-10 resulted in a FAIL, and the unit was rejected multiple times for not meeting the limits. The evidence shows that there were multiple review rounds, but it does not provide a single root cause or a complete history of modifications made between attempts.

*scored:* MISSING : not recorded / no reason / no classified / not captured / no failure reason / does not record / no measurement

---

### 8. aggregate / most-common-mode  --  FAIL

**Q:** What is the most common reason products fail in this lab?

**A:** The analysis for failure_detail returned no rows, so I cannot identify a most common failure reason from history.
- Analyzed with: analyse_history(analysis='failure_detail', product='', tco='', tco_before='', tco_after='', reason_code='')
- Result: empty

*scored:* MISSING : CE_LIMIT_EXCEEDED / conducted emission; 6 / 3

---

### 9. aggregate / never-passed  --  PASS

**Q:** Are there any products that have failed and never passed?

**A:** analyse_history.timeline returned empty (no rows). No evidence in this dataset of any product that failed and never passed.

---

### 10. aggregate / how-many-products-ce  --  FAIL

**Q:** How many different products have failed conducted emission?

**A:** 4
Reason: Distinct products with a CE datasheet where result is not 'Pass' (i.e., considered failed).

*scored:* MISSING : 3 / three; Aurora / Orion / Vega

---

### 11. aggregate / mode-most-products  --  PASS

**Q:** Which failure mode affects the largest number of different products?

**A:** CE_LIMIT_EXCEEDED is the failure mode affecting the largest number of different products, seen across 3 products: DEMO Aurora Centrifuge C5 (AUR-C5-230), DEMO Orion Analyzer O9 (ORI-O9-400), and DEMO Vega Incubator V2 (VEG-V2-110). EFT_RESET affects 2 products: DEMO Lyra Pump L3 (LYR-L3-015) and DEMO Nova Sampler N1 (NOV-N1-220). No other failure modes are shown affecting more than one product.

---

### 12. review / why-sent-back  --  PASS

**Q:** Why do datasheets get sent back in peer review in this lab?

**A:** Most datasheets are returned for peer review due to test-configuration/limits issues. Specifically, "lifecycle test: please correct the limits" was the reason in 7 of 9 rejected peer-review events (CRF on job TFS-EMC-2026-010, Full-Scope EMC Sample Unit). Other reasons (each 1 occurrence) include expired LISN calibration certificate and missing test setup photographs.

---

### 13. review / how-many-rejected  --  FAIL  (scoring error - see note)

**Q:** How many datasheets have been rejected in peer review?

**A:** 3
This number is based on the count of datasheets that transitioned from 'Peer Review' to 'Rejected'.

*scored:* MISSING : 9 / nine / 7 / seven

*MY ERROR:* 3 distinct datasheets is correct; the case demanded 9 rejection events

---

### 14. honest / which-component  --  FAIL

**Q:** Which internal component caused the DEMO Aurora Centrifuge C5's emission failure?

**A:** There are four DEMO Aurora Centrifuge C5 EMC jobs in the system. Please specify which one you mean:
- DEMO-EMC-201
- DEMO-EMC-202
- DEMO-EMC-203
- DEMO-EMC-204
If you’d like, I can fetch the datasheets for all four and identify any stated internal component associated with an emission failure.

*scored:* MISSING : an explicit statement that the data cannot answer this

---

### 15. honest / will-it-pass  --  FAIL

**Q:** Will the DEMO Nova Sampler N1 pass its next test?

**A:** Not guaranteed to pass the next test.
"IN WORDS (quote this, do not re-count): 2 campaign(s) in total: 1 failed, 1 passed. the unit failed for: EFT_RESET." Timeline derived from analyse_history(analysis='timeline', product='DEMO Nova Sampler N1').

*scored:* MISSING : an explicit statement that the data cannot answer this

---

### 16. honest / rework-hours  --  PASS

**Q:** How many rework hours did the DEMO Aurora Centrifuge C5 failures cost us?

**A:** Short answer: the rework hours cannot be determined from this data.
What I found:
- There are four DEMO EMC jobs on the DEMO Aurora Centrifuge C5 (tco_ids: DEMO-EMC-201, DEMO-EMC-202, DEMO-EMC-203, DEMO-EMC-204).
- Results per datasheet:
- DEMO-EMC-201: FAIL
- DEMO-EMC-202: FAIL
- DEMO-EMC-203: FAIL
- DEMO-EMC-204: PASS
- The planner_entries data that could track time spent on rework (total_hours) is NULL for these tco_ids, so no rework-hour figure can be derived from the current records.
Bottom line:
- Failures occurred on 3 of 4 tests for this product, but the dataset does not store rework hours, so I cannot quantify the rework hours cost from these records alone.

---

### 17. honest / root-cause  --  FAIL

**Q:** What was the confirmed root cause of the DEMO Aurora Centrifuge C5's conducted emission failure?

**A:** I could not verify one part of my answer (DEMO-EMC-203, 60.5, 4.5, 1.9), so here is what the database actually returned instead - these rows are exact:
field                     value
------------------------  -------------------------
tco_id                    DEMO-EMC-201
product_name              DEMO Aurora Centrifuge C5
model_number              AUR-C5-230
test_code                 CE
test_date                 2025-12-15
result                    FAIL
failure_reason_code       CE_LIMIT_EXCEEDED
met_performance_criteria  D
review_rounds             1
record_rejected_for
is_synthetic              1
worst_frequency_mhz       0.720
... and 53 more row(s)
Ask me about any single line above and I will go deeper.

*scored:* MISSING : a written answer - got a raw evidence dump

---

