import unittest
from pathlib import Path

from app import (
    ADMIN_APPROVAL_STATUS_VALUES,
    DATASHEET_DA_SKIPPED_STATUS,
    REQUEST_SEARCH_FIELD_ATTRS,
    TEST_PLAN_APPROVAL_DISPLAY_LABEL,
    TEST_PLAN_APPROVAL_STATUS,
    TEST_PLAN_APPROVAL_STATUS_KEY,
    _build_admin_approval_status_filter_options,
    _get_admin_approval_status_key,
    _format_report_access_feedback_comment,
    _format_admin_approval_status_label,
    _format_request_status_label,
    _get_stat_card_logic_definitions,
    _is_terminal_request_status,
    _partition_request_status_queue,
    _request_record_matches_search,
    _report_is_approvable_status,
    _build_status_filter_options,
)


class ReportReviewStatusTests(unittest.TestCase):
    def test_draft_report_statuses_are_approvable(self):
        for status in ("Draft Report", "Report Uploaded", "report_uploaded"):
            with self.subTest(status=status):
                self.assertTrue(_report_is_approvable_status(status))

    def test_completed_report_statuses_are_not_approvable(self):
        for status in ("Proceed Report", "Admin Sign Off", "Completed", None):
            with self.subTest(status=status):
                self.assertFalse(_report_is_approvable_status(status))

    def test_report_workflow_requires_admin_signoff_before_final_download(self):
        source = Path("app.py").read_text(encoding="utf-8")
        admin_approval_source = Path("templates/admin_approval.html").read_text(
            encoding="utf-8"
        )
        review_source = Path("templates/review.html").read_text(encoding="utf-8")
        proceed_route = source[
            source.index("def proceed_report(request_id):") :
            source.index("# 4. View report PDF in browser.")
        ]
        signoff_route = source[
            source.index("def admin_sign_off(request_id):") :
            source.index("@flask_app.route('/api/test-requests/<int:request_id>/resync-status'")
        ]
        download_route = source[
            source.index("def download_report(request_id):") :
            source.index("# 1. Submit a new review comment")
        ]

        self.assertIn("test_request.status = 'Proceed Report'", proceed_route)
        self.assertIn("current_user.role != 'admin'", signoff_route)
        self.assertIn("test_request.status = 'Completed'", signoff_route)
        self.assertIn("entry.status = 'completed'", signoff_route)
        self.assertIn("send_completion_notification", signoff_route)
        self.assertIn(
            "Final report download is available only after Admin Sign Off is completed.",
            download_route,
        )
        self.assertIn("Admin Sign Off & Complete", admin_approval_source)
        self.assertIn("/admin-completed", admin_approval_source)
        self.assertIn("['Proceed Report', 'Admin Sign Off'].includes(statusLabel)", admin_approval_source)
        self.assertNotIn(
            'This will mark the status as "Completed" and make the final report available for download.',
            review_source,
        )

    def test_terminal_requests_are_partitioned_after_active_requests(self):
        requests = [
            type("Request", (), {"status": "Completed", "id": 1})(),
            type("Request", (), {"status": "In Progress", "id": 2})(),
            type("Request", (), {"status": "Cancelled", "id": 3})(),
            {"status_key": "in_progress", "status_display": "Rejected", "id": 4},
        ]

        ordered = _partition_request_status_queue(requests)

        self.assertEqual(
            [request.get("id") if isinstance(request, dict) else request.id for request in ordered],
            [2, 1, 3, 4],
        )

    def test_terminal_status_helper_covers_closed_queue_states(self):
        for status in ("Completed", "completed", "Cancelled", "Rejected", "rejected"):
            with self.subTest(status=status):
                self.assertTrue(_is_terminal_request_status(status))
        for status in ("Proceed Report", "Admin Sign Off", "Test Plan Approved", "In Progress"):
            with self.subTest(status=status):
                self.assertFalse(_is_terminal_request_status(status))


class CompletedTcoFeedbackWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = Path("app.py").read_text(encoding="utf-8")
        cls.index_source = Path("templates/index.html").read_text(encoding="utf-8")

    def test_report_actions_do_not_require_feedback_grant(self):
        for start, end in (
            ("def download_report(request_id):", "# 1. Submit a new review comment"),
            ("def proceed_report(request_id):", "# 4. View report PDF in browser."),
            ("def view_report(request_id):", "@flask_app.route('/api/test-requests/<int:request_id>/view-report-data'"),
        ):
            with self.subTest(start=start):
                route_source = cls_source_between(self.app_source, start, end)
                self.assertNotIn("_has_report_access_grant", route_source)
                self.assertNotIn("Submit feedback and acknowledge", route_source)

    def test_completed_feedback_state_is_returned_separately_from_report_access(self):
        route_source = cls_source_between(
            self.app_source,
            "def view_report_data(request_id):",
            "@flask_app.route('/assigned-tests')",
        )

        self.assertIn("'require_feedback_before_report_access': False", route_source)
        self.assertIn("'report_access_granted': True", route_source)
        self.assertIn("'completed_feedback_required': pending_completed_feedback", route_source)
        self.assertIn("'completed_feedback_submitted': completed_feedback_submitted", route_source)
        self.assertIn("_has_completed_tco_feedback(test_request)", route_source)

    def test_new_tco_creation_is_blocked_by_pending_completed_feedback(self):
        create_route = cls_source_between(
            self.app_source,
            "def create_test_plan():",
            "@flask_app.route('/generate', methods=['POST'])",
        )

        self.assertIn("_new_tco_feedback_block_response()", create_route)
        self.assertIn("completed_tco_feedback_required", self.app_source)
        self.assertIn("_completed_tcos_missing_required_feedback", self.app_source)
        self.assertIn("_has_completed_tco_feedback(test_request)", self.app_source)
        self.assertIn("'pending_feedback_tcos': pending_tcos", self.app_source)

    def test_completed_feedback_helper_accepts_grant_or_saved_feedback_comment(self):
        helper_source = cls_source_between(
            self.app_source,
            "def _has_completed_tco_feedback(test_request):",
            "def _grant_report_access(request_id, planner_entry):",
        )
        grant_source = cls_source_between(
            self.app_source,
            "def _grant_report_access(request_id, planner_entry):",
            "def _completed_tcos_missing_required_feedback(user_id):",
        )

        self.assertIn("_get_report_planner_entries(test_request)", helper_source)
        self.assertIn("'[Report Access Feedback]'", helper_source)
        self.assertIn("'[Completed TCO Feedback]'", helper_source)
        self.assertIn("for entry in report_entries:", grant_source)
        self.assertIn("entry.report_access_granted = True", grant_source)

    def test_index_modal_keeps_report_actions_enabled_and_prompts_for_new_tco_feedback(self):
        self.assertIn("const reportAccessGranted = true", self.index_source)
        self.assertIn("downloadFeedbackModal", self.index_source)
        self.assertIn("Feedback - ${escapeHtml(tcoLabel)}", self.index_source)
        self.assertIn("Please submit feedback for completed ${tcoLabel}", self.index_source)
        self.assertIn("Skip for Now", self.index_source)
        self.assertNotIn("Feedback Required Before Report Access", self.index_source)

    def test_index_api_returns_tco_specific_completed_feedback_state(self):
        index_api = cls_source_between(
            self.app_source,
            "def get_test_requests():",
            "def _append_datasheet_peer_review_comment(",
        )

        self.assertIn("'completed_feedback_required': completed_feedback_required", index_api)
        self.assertIn("'completed_feedback_submitted':", index_api)
        self.assertIn("'completed_feedback': {", index_api)
        self.assertIn("'pending_tcos': pending_feedback_tcos", index_api)
        self.assertIn("'pending_requests': pending_feedback_request_data", index_api)

    def test_index_create_button_uses_pending_tco_feedback_state(self):
        self.assertIn("let pendingCompletedFeedbackRequests = []", self.index_source)
        self.assertIn("getPendingCompletedFeedbackMessage", self.index_source)
        self.assertIn("updateCompletedFeedbackBlocker(data.completed_feedback || {})", self.index_source)
        self.assertIn("showCompletedFeedbackBlocker()", self.index_source)
        self.assertIn("openFirstPendingCompletedFeedback", self.index_source)
        self.assertIn("openReportFeedbackModal(String(pendingRequest.id)", self.index_source)
        self.assertNotIn("createBtn.disabled = Boolean(message)", self.index_source)
        self.assertIn("loadTestRequests();", self.index_source)

    def test_download_report_prompts_feedback_without_changing_view_report(self):
        self.assertIn("function viewReportPdf(requestId)", self.index_source)
        self.assertNotIn("function viewReportPdf(requestId) {\n        openReportFeedbackModal", self.index_source)
        self.assertIn("async function downloadReportPdf(requestId)", self.index_source)
        self.assertIn("payload.completed_feedback_required === true", self.index_source)
        self.assertIn("openReportFeedbackModal(requestId", self.index_source)
        self.assertIn("skipDownloadFeedback(requestId)", self.index_source)
        self.assertIn("triggerReportDownload(requestId)", self.index_source)


class ReportAccessFeedbackFormattingTests(unittest.TestCase):
    def test_structured_feedback_comment_is_human_readable(self):
        comment = _format_report_access_feedback_comment(
            {
                "overall_satisfaction": 9,
                "quality_of_testing": 8,
                "communication": 10,
                "schedule_adherence": 7,
            },
            "Fast turnaround and clear notes.",
        )

        self.assertIn("[Report Access Feedback]", comment)
        self.assertIn("Overall Satisfaction: 9/10", comment)
        self.assertIn("Quality Of Testing: 8/10", comment)
        self.assertIn("Communication: 10/10", comment)
        self.assertIn("Schedule Adherence: 7/10", comment)
        self.assertIn("Comments: Fast turnaround and clear notes.", comment)
        self.assertIn(
            "[Report Access Feedback]\n"
            "Overall Satisfaction: 9/10\n"
            "Quality Of Testing: 8/10\n"
            "Communication: 10/10\n"
            "Schedule Adherence: 7/10\n"
            "Comments: Fast turnaround and clear notes.",
            comment,
        )
        self.assertNotIn(" | ", comment)


class StatusFilterWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = Path("app.py").read_text(encoding="utf-8")

    def test_admin_status_options_use_admin_workflow_scope(self):
        admin_route = cls_source_between(
            self.app_source,
            "def admin_approval():",
            "@flask_app.route('/api/test-requests/<int:request_id>/admin-completed'",
        )

        self.assertIn("ADMIN_APPROVAL_STATUS_VALUES", admin_route)
        self.assertIn("status_options=_build_admin_approval_status_filter_options", admin_route)
        self.assertNotIn("approval_statuses = [", admin_route)

    def test_admin_api_uses_same_workflow_scope_and_normalized_status_filter(self):
        admin_api = cls_source_between(
            self.app_source,
            "def get_admin_test_requests():",
            "@flask_app.errorhandler(413)",
        )

        self.assertIn("ADMIN_APPROVAL_STATUS_VALUES", admin_api)
        self.assertIn("TEST_PLAN_APPROVAL_STATUS_KEY: [TEST_PLAN_APPROVAL_STATUS]", admin_api)
        self.assertIn("db.func.replace(db.func.trim(EMCRequest.status), '_', ' ')", admin_api)
        self.assertIn("normalized_status_column == normalized_filter", admin_api)
        self.assertNotIn("approval_statuses = [", admin_api)

    def test_status_filter_builder_deduplicates_underscore_and_label_spellings(self):
        options = _build_status_filter_options(
            ["Report Uploaded", "report_uploaded", "At Review"]
        )

        self.assertEqual(
            options,
            [
                {"value": "At Review", "label": "At Review"},
                {"value": "Report Uploaded", "label": "Report Uploaded"},
            ],
        )

    def test_admin_workflow_constant_contains_only_admin_page_statuses(self):
        self.assertIn("At Review", ADMIN_APPROVAL_STATUS_VALUES)
        self.assertIn("Test Plan To Approve", ADMIN_APPROVAL_STATUS_VALUES)
        self.assertIn("Completed", ADMIN_APPROVAL_STATUS_VALUES)
        self.assertNotIn("Draft", ADMIN_APPROVAL_STATUS_VALUES)
        self.assertNotIn("Cancelled", ADMIN_APPROVAL_STATUS_VALUES)

    def test_admin_approval_filter_markup_contains_show_all_toggle_and_table(self):
        source = Path("templates/admin_approval.html").read_text(encoding="utf-8")
        table_section = source[
            source.index("<!-- Show All Toggle Section -->") :
            source.index("<!-- Pagination Footer")
        ]

        self.assertIn('id="showAllToggle"', table_section)
        self.assertIn('id="testPlansTableBody"', table_section)
        self.assertIn('id="loadingRow"', table_section)
        self.assertIn("<thead", table_section)
        self.assertNotIn("AppPagination.renderPagination", table_section)
        self.assertEqual(_format_request_status_label("report_uploaded"), "Report Uploaded")

    def test_admin_test_plan_approval_state_has_specific_label_key_and_filter(self):
        self.assertEqual(
            _format_admin_approval_status_label(TEST_PLAN_APPROVAL_STATUS),
            TEST_PLAN_APPROVAL_DISPLAY_LABEL,
        )
        self.assertEqual(
            _get_admin_approval_status_key(TEST_PLAN_APPROVAL_STATUS),
            TEST_PLAN_APPROVAL_STATUS_KEY,
        )
        self.assertEqual(
            _build_admin_approval_status_filter_options([TEST_PLAN_APPROVAL_STATUS]),
            [
                {
                    "value": TEST_PLAN_APPROVAL_STATUS_KEY,
                    "label": TEST_PLAN_APPROVAL_DISPLAY_LABEL,
                }
            ],
        )

    def test_admin_template_uses_specific_approve_test_plan_action(self):
        source = Path("templates/admin_approval.html").read_text(encoding="utf-8")

        self.assertIn("Approve Test Plan", source)
        self.assertIn("getAdminStatusKey(status) === 'test_plan_needs_approval'", source)
        self.assertIn("Test Plan Needs Approval", source)

    def test_queue_ordering_is_applied_before_pagination_on_target_pages(self):
        index_api = cls_source_between(
            self.app_source,
            "def get_test_requests():",
            "def _append_datasheet_peer_review_comment(",
        )
        review_route = cls_source_between(
            self.app_source,
            "def review():",
            "@flask_app.route('/api/test-requests/<int:request_id>/upload-report'",
        )
        assigned_context = cls_source_between(
            self.app_source,
            "def _get_assigned_tests_context():",
            "@flask_app.route('/api/planner'",
        )
        admin_route = cls_source_between(
            self.app_source,
            "def admin_approval():",
            "@flask_app.route('/api/test-requests/<int:request_id>/admin-completed'",
        )
        admin_api = cls_source_between(
            self.app_source,
            "def get_admin_test_requests():",
            "@flask_app.errorhandler(413)",
        )

        self.assertLess(
            index_api.index("_partition_request_status_queue(sorted_requests)"),
            index_api.index("test_requests = sorted_requests[start_index:end_index]"),
        )
        self.assertLess(
            review_route.index("_partition_request_status_queue(test_plans)"),
            review_route.index("start_index = (page - 1) * per_page"),
        )
        self.assertIn("_is_terminal_request_status(plan.get('status'))", assigned_context)
        self.assertIn("at_review_requests = _partition_request_status_queue", admin_route)
        self.assertLess(
            admin_api.index("_partition_request_status_queue(test_plans)"),
            admin_api.index("'success': True"),
        )


def cls_source_between(source, start, end):
    return source[source.index(start):source.index(end)]


class ReportReviewModalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path("templates/index.html").read_text(encoding="utf-8")

    def test_modal_exposes_submit_and_approve_actions(self):
        self.assertIn("submitReportReviewComment(requestId)", self.source)
        self.assertIn("Approve Report", self.source)

    def test_modal_uses_backend_approval_and_access_flags(self):
        self.assertIn("payload.can_approve_report", self.source)
        self.assertIn(
            "payload.completed_feedback_required",
            self.source,
        )
        self.assertIn("const reportAccessGranted = true", self.source)

    def test_modal_uses_slider_feedback_inputs(self):
        self.assertIn("reportAccessOverallSatisfaction_${requestId}", self.source)
        self.assertIn("reportAccessQualityOfTesting_${requestId}", self.source)
        self.assertIn("reportAccessCommunication_${requestId}", self.source)
        self.assertIn("reportAccessScheduleAdherence_${requestId}", self.source)
        self.assertIn('type="range"', self.source)
        self.assertIn("updateReportAccessSliderValue(this)", self.source)
        self.assertIn("downloadFeedbackModal", self.source)
        self.assertIn("Required before creating a new request", self.source)
        self.assertIn("Skip for Now", self.source)
        self.assertNotIn("Feedback Required Before Report Access", self.source)
        self.assertNotIn("unlock View and Download", self.source)

    def test_standalone_comments_modal_has_visible_submit_form(self):
        self.assertIn('id="commentsForm_${requestId}"', self.source)
        self.assertIn('type="submit" id="commentsSubmitBtn_${requestId}"', self.source)
        self.assertIn("<span>Submit</span>", self.source)
        self.assertIn("submitThreadComment(${requestId})", self.source)


class PeerReviewPageOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review_source = Path("templates/review.html").read_text(
            encoding="utf-8"
        )
        cls.assigned_source = Path("templates/assigned_test.html").read_text(
            encoding="utf-8"
        )

    def test_review_page_has_no_peer_review_interface(self):
        self.assertNotIn('id="peerReviewToggle"', self.review_source)
        self.assertNotIn('id="peerReviewSection"', self.review_source)
        self.assertNotIn('id="peerReviewTable"', self.review_source)

    def test_assigned_tests_remains_peer_review_owner(self):
        self.assertIn('id="peerReviewToggle"', self.assigned_source)
        self.assertIn('id="peerReviewSection"', self.assigned_source)
        self.assertIn("/api/planner/peer-review", self.assigned_source)

    def test_assigned_peer_review_actions_refresh_in_place(self):
        action_block = self.assigned_source[
            self.assigned_source.index("window.submitPeerReviewAction"):
        ]
        action_block = action_block[:action_block.index("function setPeerReviewMode")]

        self.assertIn("window.loadPeerReviewData();", action_block)
        self.assertNotIn("window.location.reload()", action_block)

    def test_all_request_pages_default_to_tco_sorting(self):
        index_source = Path("templates/index.html").read_text(encoding="utf-8")
        admin_source = Path("templates/admin_approval.html").read_text(
            encoding="utf-8"
        )
        review_source = Path("templates/review.html").read_text(encoding="utf-8")
        pagination_source = Path("static/js/pagination.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("sortBy: 'tco_id'", index_source)
        self.assertIn("let ADMIN_SORT_BY = 'tco_id';", admin_source)
        self.assertIn("sortBy: 'tco_id'", self.assigned_source)
        self.assertIn("reviewPaginationControls", review_source)
        self.assertIn("AppPagination.renderPagination", index_source)
        self.assertIn("AppPagination.renderPagination", admin_source)
        self.assertIn("AppPagination.renderPagination", self.assigned_source)
        self.assertIn("buildPageItems", pagination_source)
        self.assertIn("First page", pagination_source)
        self.assertIn("Last page", pagination_source)
        self.assertIn(
            "const peerReviewSortState = { sortBy: 'tco_id', sortDir: 'asc' };",
            self.assigned_source,
        )

    def test_review_exposes_eligible_test_plan_download(self):
        self.assertIn("can_generate_test_plan", self.review_source)
        self.assertIn(
            "/api/test-requests/${encodeURIComponent(requestId)}/download-test-plan-docx",
            self.review_source,
        )
        self.assertIn("downloadTestPlan('{{ test_plan.id }}', this)", self.review_source)
        self.assertIn("response.blob()", self.review_source)
        self.assertIn("showToast('Test Plan downloaded successfully.'", self.review_source)

    def test_test_plan_gate_uses_assignment_and_admin_approval(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        gate = app_source[app_source.index("def _test_plan_generation_eligible"):]
        gate = gate[:gate.index("def _validate_planner_time_window")]

        self.assertIn("test plan approved", gate)
        self.assertIn("selected_keys - assigned_keys", gate)
        self.assertNotIn("datasheet_file_path", gate)

    def test_reschedule_preserves_existing_engineer_selection(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        assign_route = cls_source_between(
            app_source,
            "def assign_tests_to_engineers(request_id):",
            "def submit_review(request_id):",
        )
        planner_update_route = cls_source_between(
            app_source,
            "def update_planner_entry(planner_id):",
            "def delete_planner_entry(planner_id):",
        )
        planner_source = Path("templates/planner.html").read_text(encoding="utf-8")

        self.assertIn("currentAssignRescheduleMode ? []", self.review_source)
        self.assertIn("} else if (!savedAssignment) {", self.review_source)
        self.assertNotIn("} else {\n                const engineerSelect = testCard.querySelector('select[name^=\"test_engineer\"]');\n                if (engineerSelect) {\n                    engineerSelect.value = '';\n                }\n            }", self.review_source)
        self.assertIn("existing_assignment_by_key = {}", assign_route)
        self.assertIn("existing_assignment_by_key[assignment_key] = normalized_payload", assign_route)
        self.assertIn("if reschedule_existing_assignments and assignment_key:", assign_route)
        self.assertIn("**existing_assignment", assign_route)
        self.assertIn("const desiredEngineerId = String(event.engineerId || '')", planner_source)
        self.assertNotIn("const desiredEngineerId = String(event.engineerId || this.defaultUserId || '')", planner_source)
        self.assertIn("if 'test_person_name' in data:", planner_update_route)
        self.assertIn("if 'start_time' in data else target_entry.start_time", planner_update_route)
        self.assertIn("if 'total_hours' in data:", planner_update_route)

    def test_index_search_requires_explicit_submission(self):
        index_source = Path("templates/index.html").read_text(encoding="utf-8")

        self.assertIn('id="indexSearchButton"', index_source)
        self.assertIn("searchButton?.addEventListener('click', executeSearch)", index_source)
        self.assertIn("if (event.key === 'Enter')", index_source)
        self.assertNotIn("setTimeout(() => {\n                    testRequestsState.page = 1;\n                    loadTestRequests();\n                }, 500);", index_source)


class IndexRequestVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = Path("app.py").read_text(encoding="utf-8")

    def test_index_visibility_helper_scopes_roles(self):
        helper = self.app_source[
            self.app_source.index("def _index_visible_requests_query") :
            self.app_source.index("def _can_access_review_thread")
        ]

        self.assertIn("current_user.role == 'admin'", helper)
        self.assertIn("current_user.role == 'lab_engineer'", helper)
        self.assertIn("_non_draft_request_filter()", helper)
        self.assertIn("EMCRequest.user_id == current_user.id", helper)
        lab_engineer_branch = helper[
            helper.index("if current_user.role == 'lab_engineer':") :
            helper.index("return EMCRequest.query.filter(EMCRequest.user_id == current_user.id)")
        ]
        self.assertIn("return EMCRequest.query", lab_engineer_branch)
        self.assertNotIn("EMCRequest.user_id == current_user.id", lab_engineer_branch)
        self.assertNotIn("_non_draft_request_filter()", lab_engineer_branch)
        self.assertIn("EMCRequest.query.filter(EMCRequest.user_id == current_user.id)", helper)

    def test_index_page_filter_options_use_authorized_dataset(self):
        index_route = self.app_source[
            self.app_source.index("def index():") :
            self.app_source.index("@flask_app.route('/help')")
        ]

        self.assertIn("status_scope_query = _index_visible_requests_query()", index_route)
        self.assertIn("all_requests = _index_visible_requests_query()", index_route)
        self.assertNotIn("all_requests = EMCRequest.query.all()", index_route)

    def test_test_requests_api_authorizes_before_filter_sort_paginate(self):
        api_route = self.app_source[
            self.app_source.index("def get_test_requests():") :
            self.app_source.index("def _append_datasheet_peer_review_comment")
        ]

        visibility_index = api_route.index("base_query = _index_visible_requests_query()")
        filter_index = api_route.index("#  Apply filters")
        ordering_index = api_route.index("#  Ordering & pagination")

        self.assertLess(visibility_index, filter_index)
        self.assertLess(filter_index, ordering_index)
        self.assertIn("elif current_user.role == 'lab_engineer':", api_route)
        self.assertIn("query = list_query", api_route)


class TestEngineerAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = Path("app.py").read_text(encoding="utf-8")

    def test_request_detail_access_uses_planner_assignment_for_engineers(self):
        helper = cls_source_between(
            self.app_source,
            "def _request_has_engineer_assignment",
            "def _can_access_iec_request",
        )
        access_helper = cls_source_between(
            self.app_source,
            "def _can_access_iec_request",
            "def _non_draft_request_filter",
        )

        self.assertIn("PlannerEntry.engineer_user_id == user_id", helper)
        self.assertIn("PlannerEntry.status", helper)
        self.assertIn("current_user.role in ENGINEER_ROLES", access_helper)
        self.assertIn("_request_has_engineer_assignment(test_request, current_user.id)", access_helper)

    def test_review_and_assigned_pages_allow_test_engineer_role(self):
        assigned_context = cls_source_between(
            self.app_source,
            "def _get_assigned_tests_context():",
            "@flask_app.route('/api/planner'",
        )
        review_route = cls_source_between(
            self.app_source,
            "def review():",
            "@flask_app.route('/api/test-requests/<int:request_id>/upload-report'",
        )
        assigned_route = cls_source_between(
            self.app_source,
            "def assigned_test():",
            "# Configuration",
        )

        self.assertIn("ENGINEER_ROLES = ('lab_engineer', 'test_engineer')", self.app_source)
        self.assertIn("current_user.role not in ('admin', *ENGINEER_ROLES)", assigned_context)
        self.assertIn("current_user.role in ENGINEER_ROLES", assigned_context)
        self.assertIn("current_user.role not in ('admin', *ENGINEER_ROLES)", review_route)
        self.assertIn("current_user.role not in ('admin', *ENGINEER_ROLES)", assigned_route)

    def test_test_engineer_planner_visibility_is_scoped_to_own_assignments(self):
        planner_route = cls_source_between(
            self.app_source,
            "def planner_entries_api():",
            "@flask_app.route('/api/planner/tco-ids'",
        )
        tco_id_route = cls_source_between(
            self.app_source,
            "def get_planner_tco_ids():",
            "def send_cancellation_email",
        )

        self.assertIn("elif current_user.role in ENGINEER_ROLES", planner_route)
        self.assertIn("query = query.filter_by(engineer_user_id=current_user.id)", planner_route)
        self.assertIn("elif current_user.role in ENGINEER_ROLES", tco_id_route)
        self.assertIn("query = query.filter_by(engineer_user_id=current_user.id)", tco_id_route)


class SharedRequestSearchTests(unittest.TestCase):
    def test_shared_request_search_fields_cover_key_identifiers(self):
        for field_name in (
            "product_name",
            "tco_id",
            "job_number",
            "manufacturer",
            "model_number",
            "serial_number",
            "requester_name",
            "requester_email",
            "assigned_engineer_name",
            "status",
        ):
            with self.subTest(field_name=field_name):
                self.assertIn(field_name, REQUEST_SEARCH_FIELD_ATTRS)

    def test_request_record_search_is_case_insensitive_partial_match(self):
        record = {
            "product_name": "Falcon Controller",
            "tco_id": "TCO-2026-042",
            "requester_email": "owner@example.com",
            "status_display": "Datasheet Uploaded",
        }

        self.assertTrue(_request_record_matches_search(record, "falcon"))
        self.assertTrue(_request_record_matches_search(record, "2026-04"))
        self.assertTrue(_request_record_matches_search(record, "UPLOADED"))
        self.assertFalse(_request_record_matches_search(record, "missing"))

    def test_request_pages_use_explicit_search_triggers(self):
        for template_name, button_id in (
            ("templates/index.html", "indexSearchButton"),
            ("templates/admin_approval.html", "adminSearchButton"),
            ("templates/assigned_test.html", "assignedSearchButton"),
            ("templates/equipment.html", "equipmentSearchButton"),
            ("templates/review.html", "reviewSearchButton"),
        ):
            with self.subTest(template_name=template_name):
                source = Path(template_name).read_text(encoding="utf-8")
                self.assertIn(f'id="{button_id}"', source)
                self.assertIn("if (event.key === 'Enter')", source)

    def test_equipment_page_exposes_read_only_view_action(self):
        source = Path("templates/equipment.html").read_text(encoding="utf-8")

        self.assertIn("view-equipment-btn", source)
        self.assertIn("bg-blue-700 hover:bg-blue-800", source)
        self.assertIn("function viewEquipment(id)", source)
        self.assertIn("editEquipment(id, true)", source)
        self.assertIn("function editEquipment(id, readOnly = false)", source)
        self.assertIn("setEquipmentModalReadOnly(readOnly)", source)
        self.assertIn("openModal(readOnly ? 'View Equipment' : 'Edit Equipment')", source)
        self.assertIn("submitBtn.classList.toggle('hidden', Boolean(isReadOnly))", source)
        self.assertIn('id="equipmentModalBody"', source)
        self.assertIn("flex-1 space-y-6 overflow-y-auto", source)

    def test_equipment_page_shows_and_sorts_by_sl_no(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        equipment_route = cls_source_between(
            app_source,
            "def equipment_list():",
            "@flask_app.route('/api/equipment')",
        )
        source = Path("templates/equipment.html").read_text(encoding="utf-8")
        table_source = source[
            source.index('<table class="equipment-table') :
            source.index("<!-- Enhanced Pagination -->")
        ]

        self.assertIn("Equipment.sl_no.is_(None)", equipment_route)
        self.assertIn("Equipment.sl_no.asc()", equipment_route)
        self.assertIn("Equipment.name.asc()", equipment_route)
        self.assertIn("SL No</th>", table_source)
        self.assertIn("{{ equipment.sl_no or 'N/A' }}", table_source)
        self.assertNotIn("hidden px-4 py-3 text-left text-xs font-bold text-gray-700 uppercase tracking-wider whitespace-nowrap\">\n                                Asset ID", table_source)
        self.assertIn("equipmentData.sl_no || 'N/A'", source)
        self.assertIn("min-w-[1700px]", table_source)
        self.assertIn(".equipment-table-wrap", source)
        self.assertIn("overflow-x: auto;", source)

    def test_backend_request_routes_use_shared_search_helper(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        index_api = app_source[
            app_source.index("def get_test_requests():") :
            app_source.index("def _append_datasheet_peer_review_comment")
        ]
        review_route = app_source[
            app_source.index("def review():") :
            app_source.index("@flask_app.route('/api/test-requests/<int:request_id>/upload-report'")
        ]
        admin_api = app_source[
            app_source.index("def get_admin_test_requests():") :
            app_source.index("@flask_app.route('/.well-known/appspecific/com.chrome.devtools.json')")
        ]

        self.assertIn("_apply_request_search_filter(query, search_query)", index_api)
        self.assertIn("_request_record_matches_search(plan, search_query)", review_route)
        self.assertIn("_apply_request_search_filter(query, search_query)", admin_api)


class StatCardLogicTransparencyTests(unittest.TestCase):
    def test_logic_registry_covers_dashboard_and_equipment_cards(self):
        definitions = _get_stat_card_logic_definitions()
        for key in (
            "dashboard.total_requests",
            "dashboard.active_tests",
            "dashboard.completed_requests",
            "dashboard.testing_hours_ytd",
            "dashboard.cost_avoidance",
            "dashboard.compliance_projects_completed",
            "dashboard.active_lab_engineers",
            "equipment.total",
            "equipment.total_instruments",
            "equipment.total_equipment",
            "equipment.in_calibration",
            "equipment.out_of_calibration",
            "equipment.maintenance",
            "request_summary.total",
            "request_summary.completed",
            "request_summary.testing_in_progress",
            "request_summary.draft_report",
            "request_summary.datasheet_uploaded",
            "request_summary.at_review",
            "request_summary.partially_scheduled",
            "request_summary.rejected_cancelled",
            "assigned.under_review",
            "assigned.approved",
            "assigned.rejected",
            "assigned.assigned",
            "assigned.total_tests",
            "assigned.peer_review",
            "assigned.datasheet_uploaded",
        ):
            with self.subTest(key=key):
                logic = definitions[key]
                self.assertIn("model_sources", logic)
                self.assertTrue(logic["logic"])
                self.assertNotIn("password", str(logic).lower())
                self.assertNotIn("token", str(logic).lower())

    def test_stat_logic_endpoint_is_admin_only(self):
        source = Path("app.py").read_text(encoding="utf-8")
        route = source[
            source.index("def get_admin_stat_card_logic") :
            source.index("# Initialize extensions")
        ]

        self.assertIn("current_user.role != 'admin'", route)
        self.assertIn("}), 403", route)
        self.assertIn("_get_stat_card_logic_definitions().get(logic_key)", route)

    def test_shared_template_hides_controls_for_non_admins(self):
        source = Path("templates/_stat_logic_controls.html").read_text(
            encoding="utf-8"
        )
        base_source = Path("templates/base.html").read_text(encoding="utf-8")

        self.assertIn("current_user.role == 'admin'", source)
        self.assertIn('class="stat-logic-toggle', source)
        self.assertIn("absolute bottom-3 right-3", source)
        self.assertIn("text-black", source)
        self.assertIn('class="h-3.5 w-3.5 pointer-events-none"', source)
        self.assertIn("data-stat-logic-key", source)
        self.assertIn(".stat-logic-toggle", base_source)
        self.assertIn("right: 0.75rem;", base_source)
        self.assertIn("bottom: 0.75rem;", base_source)
        self.assertIn("top: auto;", base_source)
        self.assertIn("left: auto;", base_source)
        self.assertNotIn("Current Value Source", base_source)
        self.assertNotIn("Models / Tables", base_source)
        self.assertNotIn("Role Dependency", base_source)

    def test_dashboard_and_equipment_use_shared_logic_controls(self):
        dashboard_source = Path("templates/dashboard.html").read_text(
            encoding="utf-8"
        )
        equipment_source = Path("templates/_equipment_statistics_cards.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("stat_logic.stat_logic_button('dashboard.total_requests'", dashboard_source)
        self.assertIn("stat_logic.stat_logic_button('dashboard.cost_avoidance'", dashboard_source)
        self.assertIn("stat_logic.stat_logic_button('equipment.total'", equipment_source)
        self.assertIn("stat_logic.stat_logic_panel('equipment.maintenance'", equipment_source)
        self.assertNotIn("absolute right-3 top-3", dashboard_source)
        self.assertNotIn("absolute right-3 top-3", equipment_source)

    def test_request_workflow_pages_use_shared_logic_controls(self):
        index_source = Path("templates/index.html").read_text(encoding="utf-8")
        admin_source = Path("templates/admin_approval.html").read_text(
            encoding="utf-8"
        )
        review_source = Path("templates/review.html").read_text(encoding="utf-8")
        assigned_source = Path("templates/assigned_test.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("stat_logic.stat_logic_button('request_summary.total'", index_source)
        self.assertIn("stat_logic.stat_logic_panel('request_summary.completed'", index_source)
        self.assertIn("stat_logic.stat_logic_button('request_summary.total'", admin_source)
        self.assertIn(
            "stat_logic.stat_logic_panel('request_summary.partially_scheduled'",
            admin_source,
        )
        self.assertIn("stat_logic.stat_logic_button('assigned.under_review'", review_source)
        self.assertIn("stat_logic.stat_logic_panel('assigned.datasheet_uploaded'", review_source)
        self.assertIn("stat_logic.stat_logic_button('assigned.peer_review'", assigned_source)
        self.assertIn("stat_logic.stat_logic_panel('assigned.total_tests'", assigned_source)
        for source in (index_source, admin_source, review_source, assigned_source):
            self.assertNotIn("absolute right-3 top-3", source)


class DevelopmentalAssistanceDatasheetSkipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = Path("app.py").read_text(encoding="utf-8")
        cls.assigned_source = Path("templates/assigned_test.html").read_text(
            encoding="utf-8"
        )

    def test_backend_skip_endpoint_validates_da_service_type(self):
        route_source = self.app_source[
            self.app_source.index("def skip_developmental_assistance_datasheet") :
            self.app_source.index("def _format_checkbox_option_line")
        ]

        self.assertIn("_request_skips_report_review(parent_request)", route_source)
        self.assertIn("Datasheet generation can only be skipped", route_source)
        self.assertIn("assignment.status = DATASHEET_DA_SKIPPED_STATUS", route_source)
        self.assertIn("assignment.datasheet_file_path = None", route_source)

    def test_da_skip_counts_as_datasheet_requirement_satisfied_only_for_da(self):
        helper_source = self.app_source[
            self.app_source.index("def _is_datasheet_requirement_satisfied") :
            self.app_source.index("def _calculate_calibration_status")
        ]
        self.assertIn("normalized_status == 'datasheet uploaded'", helper_source)
        self.assertIn("_is_datasheet_da_skipped_status", helper_source)
        self.assertIn("_request_skips_report_review(parent_request)", helper_source)

    def test_assigned_tests_confirm_before_skipping_da_datasheet(self):
        self.assertIn("Developmental Assistance", self.assigned_source)
        self.assertIn(
            "This request has a Developmental Assistance (DA) service type. Do you want to skip datasheet generation?",
            self.assigned_source,
        )
        self.assertIn("Yes, Skip Datasheet", self.assigned_source)
        self.assertIn("handleGenerateDatasheetClick", self.assigned_source)
        self.assertIn("skip-da-datasheet", self.assigned_source)
        self.assertIn("SKIPPED - DEVELOPMENTAL ASSISTANCE", self.assigned_source)

    def test_da_skip_status_is_filterable_and_labeled(self):
        self.assertEqual(DATASHEET_DA_SKIPPED_STATUS, "da_skipped")
        options = _build_status_filter_options(
            ["datasheet_uploaded", DATASHEET_DA_SKIPPED_STATUS],
            value_style="underscore",
        )
        self.assertIn(
            {"value": "da_skipped", "label": "Skipped - Developmental Assistance"},
            options,
        )


if __name__ == "__main__":
    unittest.main()
