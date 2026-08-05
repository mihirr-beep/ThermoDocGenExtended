# -*- coding: utf-8 -*-
"""report_gen - the consolidated IEC-FRM-516 EMI EMC Test Report.

One report per **request** (as opposed to ``datasheet_gen``, which produces one
datasheet per test). It reuses the datasheet schemas and context builders, so
the report and the datasheets always agree on what a field means.

The app already has the trigger for this: the "Generate Test Report" button on
the review page posts to ``/api/test-requests/<id>/generate-test-report``, which
calls ``build_request_test_report`` below.
"""
from .builder import build_report


def build_request_test_report(request_obj, planner_entries, output_path, now=None):
    """Build the IEC-FRM-516 report for one request.

    request_obj     : EMCRequest
    planner_entries : that request's PlannerEntry rows (cancelled ones are
                      ignored; the datasheet of each remaining test is read from
                      datasheet_records)
    Returns (output_path, summary dict).
    """
    return build_report(request_obj, planner_entries, output_path, now=now)


__all__ = ["build_report", "build_request_test_report"]
