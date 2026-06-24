# Release Notes

## Version 1.0.0
**Release Date:** March 13, 2026

### Overview
Version 1.0.0 is the first formal production release of the Test Request Generator workflow application.  
This release standardizes the end-to-end request lifecycle from requester submission through lab execution, report review, and admin sign-off.
Use the <strong>Help</strong> button (question-mark icon in the top navigation) to understand the workflow and usage guidelines better.

### Major Highlights

1. Request lifecycle and workflow maturity
- Requesters can create, save, and track requests in their queue.
- Admin assigns TCO and routes requests into lab workflow.
- Lab engineers plan tests under TCO, execute tests, and upload datasheets.
- Reports are uploaded, reviewed, finalized, and downloadable.
- Final admin sign-off completes the workflow.

2. Comments and review experience improvements
- Comments are now accessible from the index page across all statuses.
- Review flow for Need More Information now supports:
  - **Add Comment** (adds comment only, no status update)
  - **Submit Review** (updates status to **Need More Information**)
- Message notification bubbles were added to index/review actions when comment thread activity exists.

3. UI and usability improvements
- Required-field clarity updates (including mandatory markers for key fields).
- New Help Center page with workflow guidance.
- Question-mark Help quick access added in navigation (yellow icon styling).
- Footer branding updated to Thermo Fisher Scientific and application version updated to **Version 1**.

### Admin Updates (Added)
- Admin assigns and governs **TCO** routing for incoming requests.
- Admin can review report progression and perform final **Admin Sign Off**.
- Admin visibility is maintained across planner/review stages for control and traceability.
- Admin retains user and workflow governance responsibilities (review, approvals, and lifecycle closure).

### Functional Areas Included
- Test Request creation and validation
- Request queue and status tracking
- Planner and assignment management
- Review and approval workflows
- Datasheet/report upload flows
- Admin sign-off process
- Equipment management and reminder features (from previous versions)

### Complete Feature Set (Version 1)

1. Requester Features
- Create new EMC test requests with structured form sections.
- Save drafts and continue editing later.
- Track own request queue with status and search/filter support.
- Add/view threaded comments with reviewer/lab engineer.
- Review uploaded reports and participate in report workflow.

2. Review & Collaboration Features
- Need More Information workflow with two actions:
  - Add Comment (no status update)
  - Submit Review (status update)
- Shared comment thread across requester/reviewer context.
- Visual message notification bubbles for requests with comments.
- Role-based review handling for admin and lab engineer users.

3. Planner & Execution Features
- TCO-based assignment workflow.
- Test planning and scheduling for lab engineers.
- Assignment updates/rescheduling support.
- Per-test execution tracking and status progression.

4. Datasheet & Report Lifecycle Features
- Datasheet upload handling for planned/assigned tests.
- Final report upload with mandatory comments.
- Report viewing and download support.
- Admin sign-off stage for final closure and traceability.

5. Admin Features
- TCO assignment governance and routing control.
- Workflow supervision across review/planner/report stages.
- Admin approval and final lifecycle closure.
- User/workflow governance responsibilities.

6. Authentication & Access Control
- Login/registration/password/profile flows.
- Role-based access for requester, lab engineer, and admin actions.
- Route/API protection with authorization checks.

7. Equipment Management Features
- Equipment records with calibration/IC/maintenance tracking.
- EOU/non-EOU support for reminder logic.
- Automated email reminders for due activities.
- Admin/lab engineer notification coverage.

8. Usability & UI Features
- Responsive web UI for desktop and mobile.
- Guided Help page with workflow documentation.
- Top-nav Help quick access (yellow question-mark icon).
- Required-field indicators and improved form clarity.
- Updated branded footer and Version 1 display.

### Behavior Change (Important)
- `more_info` review submissions now respect an explicit mode:
  - `action_mode = comments` -> comment saved, **status unchanged**
  - `action_mode = need_more_info` -> comment saved and status set to **Need More Information**

### Notes
- This release supersedes previous `v0.x` progression and establishes the baseline for future minor/patch releases.

### Known Bugs (Current)
- In some concurrent update scenarios (multiple users editing/reviewing the same request), latest status/comments may require a manual page refresh to appear immediately.
- Large file uploads can take longer on slower networks and may fail depending on deployment timeout settings.
- After deployment updates, browser-cached assets may briefly show older UI behavior until cache refresh (hard reload).

### Upcoming Enhancements
- Real-time in-app notifications for comments, status changes, and review actions.
- Unread comment counters per request to improve queue prioritization.
- Expanded workflow dashboards for requester, lab engineer, and admin roles.
- Improved audit history view with exportable timeline of status/comment/report events.
- Additional report lifecycle automation and approval checkpoints.
