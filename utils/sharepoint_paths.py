# -*- coding: utf-8 -*-
"""Where a request's datasheets live in SharePoint, and what the folders are called.

Kept apart from the Graph client because it is all edge cases: SharePoint rejects a set of
characters outright, silently trims others, and the lab's own folder names have to be
matched exactly or the same job ends up split across two folders.

THE LAB'S CONVENTION, read off the site rather than invented here:

    Product Testing Lab/
      01-Product Testing/Projects/<year>/
        <Job Number>_<Product Name>_<Service Type>/     e.g.
          01_TRF & Test Plan/                           TFS-EMC-2026-001_ICS 6000 EG_DA
          02_Test Datasheet/
            Draft/                                      <TCO>_<CODE>_Draft.docx
            Approved/                                   <TCO>_<CODE>.docx
          03_Test Pictures/
          04_Test Report/

The job number comes FIRST - the opposite of what the integration note assumed - and the
service type is a suffix, absent on requests that have none (some QA fixtures).

The "01-Product Testing/Projects" root is a default, not a hardcode: set
SHAREPOINT_PROJECTS_ROOT to rehearse against a sandbox (the lab keeps one at
"01-Product Testing/Archive/Test") with the year/job/subfolder shape unchanged, so what is
verified there is what production will do. Note that Archive is a SIBLING of Projects -
"Projects/Archive" does not exist.
"""
import os
import re
from datetime import datetime

#: The library-relative parent every job folder sits under, by default; the year and then
#: the job folder are appended. SHAREPOINT_PROJECTS_ROOT overrides it, so the whole tree can
#: be pointed at a sandbox for a rehearsal without touching code or the folder shape.
DEFAULT_PROJECTS_ROOT = "01-Product Testing/Projects"


def projects_root():
    """Where the <year>/<job folder> tree is rooted.

    Read per call rather than at import, so .env decides at runtime and a test can point it
    somewhere harmless. Trailing and leading slashes are stripped because every caller
    joins with "/".
    """
    return (os.environ.get("SHAREPOINT_PROJECTS_ROOT")
            or DEFAULT_PROJECTS_ROOT).strip().strip("/")

#: The subfolder skeleton a job folder carries, copied from the site's own
#: "[Job Number_Product Name_Compliance]" template folders. Created with the job folder so
#: an app-made folder is indistinguishable from a lab-made one.
JOB_SKELETON = (
    "01_TRF & Test Plan",
    "01_TRF & Test Plan/Reference Data",
    "02_Test Datasheet",
    "03_Test Pictures",
    "04_Test Report",
)

#: Where datasheets go inside a job folder, and the two states we mirror.
DATASHEET_FOLDER = "02_Test Datasheet"
DRAFT_FOLDER = "Draft"
APPROVED_FOLDER = "Approved"

#: SharePoint / OneDrive reject these in an item name outright.
_ILLEGAL = r'/\\:*?"<>|'
_ILLEGAL_RE = re.compile("[%s]" % re.escape(_ILLEGAL))

#: A product name column is 200 chars; a folder that long is unusable in Explorer and
#: pushes the whole path towards SharePoint's limit, so it is trimmed.
PRODUCT_MAX = 60


def sanitize(name, max_len=None):
    """One path segment, safe for SharePoint.

    Drops the illegal characters, collapses runs of whitespace, and trims leading and
    trailing spaces and periods - SharePoint strips those itself, so leaving them in means
    the name we think we created is not the name that exists.
    """
    text = _ILLEGAL_RE.sub(" ", str(name or ""))
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(". ")
    if max_len and len(text) > max_len:
        text = text[:max_len].strip(". ")
    return text


def job_label(request_obj):
    """The job number as the folder name uses it.

    job_number is free text and nullable, job_id is the generated TFS-EMC-YYYY-NNN, and
    tco_id is the last resort - the same fallback order app.py:164 already uses.
    """
    for attr in ("job_number", "job_id", "tco_id"):
        value = sanitize(getattr(request_obj, attr, "") or "", 100)
        if value:
            return value
    return "NO-JOB"


def service_label(request_obj):
    """The service type suffix ('DA', 'Compliance', 'Pre-Compliance'), or ''.

    Multi-valued on the request; the first is used, which is what the existing folders
    show. A request with none simply has no suffix.
    """
    rows = getattr(request_obj, "service_types", None) or []
    for row in rows:
        value = sanitize(getattr(row, "service_type", row) or "", 40)
        if value:
            return value
    return ""


def job_folder_name(request_obj):
    """'<Job>_<Product>_<Service>' - the lab's own convention.

    Resolved ONCE per request and then stored, because product_name and job_number stay
    editable after datasheets are uploaded: deriving this fresh each time would split one
    request's datasheets across two folders the first time someone fixes a typo.
    """
    parts = [job_label(request_obj), sanitize(getattr(request_obj, "product_name", ""), PRODUCT_MAX)]
    service = service_label(request_obj)
    if service:
        parts.append(service)
    return "_".join(p for p in parts if p)


def job_year(request_obj):
    """The year folder a job belongs in.

    Taken from the job number when it carries one (TFS-EMC-2026-001), because that is what
    the lab filed it under, and only then from created_at. Falls back to this year.
    """
    for attr in ("job_number", "job_id", "tco_id"):
        m = re.search(r"(20\d{2})", str(getattr(request_obj, attr, "") or ""))
        if m:
            return m.group(1)
    created = getattr(request_obj, "created_at", None)
    if isinstance(created, datetime):
        return str(created.year)
    return str(datetime.now().year)


def job_folder_path(request_obj, stored_name=None):
    """The library-relative path of this request's job folder.

    `stored_name` is the name already recorded on the request; when present it wins, so a
    later edit to the product name cannot move the folder.
    """
    name = sanitize(stored_name, None) or job_folder_name(request_obj)
    return "%s/%s/%s" % (projects_root(), job_year(request_obj), name)


def datasheet_folder_path(request_obj, approved, stored_name=None):
    """Where a datasheet of this state belongs."""
    leaf = APPROVED_FOLDER if approved else DRAFT_FOLDER
    return "%s/%s/%s" % (job_folder_path(request_obj, stored_name), DATASHEET_FOLDER, leaf)


def datasheet_filename(tco_id, code, approved, local_path=None):
    """'<TCO>_<CODE>.docx' approved, '<TCO>_<CODE>_Draft.docx' in draft.

    Stable on purpose: re-saving replaces the file rather than accumulating timestamped
    copies, and SharePoint's own version history carries the per-save audit trail.
    """
    ext = os.path.splitext(local_path or "")[1] or ".docx"
    stem = "_".join(p for p in (sanitize(tco_id, 60), sanitize(code, 30)) if p) or "datasheet"
    if not approved:
        stem += "_Draft"
    return stem + ext


def destination(request_obj, tco_id, code, approved, stored_name=None, local_path=None):
    """The full library-relative destination path for one datasheet."""
    return "%s/%s" % (datasheet_folder_path(request_obj, approved, stored_name),
                      datasheet_filename(tco_id, code, approved, local_path))
