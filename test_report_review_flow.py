import unittest
from pathlib import Path

from app import _format_report_access_feedback_comment, _report_is_approvable_status


class ReportReviewStatusTests(unittest.TestCase):
    def test_draft_report_statuses_are_approvable(self):
        for status in ("Draft Report", "Report Uploaded", "report_uploaded"):
            with self.subTest(status=status):
                self.assertTrue(_report_is_approvable_status(status))

    def test_completed_report_statuses_are_not_approvable(self):
        for status in ("Proceed Report", "Admin Sign Off", "Completed", None):
            with self.subTest(status=status):
                self.assertFalse(_report_is_approvable_status(status))


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
            "payload.require_feedback_before_report_access",
            self.source,
        )

    def test_modal_uses_slider_feedback_inputs(self):
        self.assertIn("reportAccessOverallSatisfaction_${requestId}", self.source)
        self.assertIn("reportAccessQualityOfTesting_${requestId}", self.source)
        self.assertIn("reportAccessCommunication_${requestId}", self.source)
        self.assertIn("reportAccessScheduleAdherence_${requestId}", self.source)
        self.assertIn('type="range"', self.source)
        self.assertIn("updateReportAccessSliderValue(this)", self.source)

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


if __name__ == "__main__":
    unittest.main()
