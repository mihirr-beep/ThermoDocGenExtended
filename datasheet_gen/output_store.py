"""Mirror generated datasheets into the lab's job-folder tree.

When a datasheet is generated we copy the outputs into a structured tree that
mirrors the reference at `Folder/new_ouput_files`:

    <JOB>_<Product>_Compliance/
      01_TRF & Test Plan/ Reference Data/
      02_Test Datasheet/
        <TEST>/                 <- the generated .docx
        <TEST>/Test data/       <- csv / txt raw data (Harmonics & Flicker: Raw Data)
      03_Test Pictures/<TEST>/  <- uploaded images (setup photos, functional-check, plots)
      04_Test Report/ Draft Report/ Final Report/ Review/

The root is created LOCALLY inside the codebase (``<codebase>/new_ouput_files``)
unless ``DATASHEET_OUTPUT_ROOT`` overrides it (e.g. to a shared drive). Every call
is best-effort and never raises into the generation flow.
"""
import os
import re
import shutil

# codebase root = parent of the datasheet_gen package
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_ROOT = os.environ.get("DATASHEET_OUTPUT_ROOT") or os.path.join(_HERE, "new_ouput_files")

# app test code -> reference folder name (used under 02_Test Datasheet / 03_Test Pictures).
# Harmonic + Voltage-Flicker share one "Harmonics & Flicker" folder in the reference.
TEST_FOLDER = {
    "CE": "CE", "RE": "RE", "EFT": "EFT", "ESD": "ESD", "SURGE": "Surge",
    "VOLTAGEDIPS": "VDIPS", "CRF": "CRF", "PFMF": "PFMF", "RS_RI": "RS",
    "HARMONIC": "Harmonics & Flicker", "VOLTAGEFLICKER": "Harmonics & Flicker",
}
_ALL_TEST_FOLDERS = ["CE", "RE", "EFT", "ESD", "Surge", "VDIPS", "CRF", "PFMF",
                     "RS", "Harmonics & Flicker"]
_PICTURE_EXTRA = ["Measured EUT Current & Monitoring Parameters"]

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe(name, default="Unknown"):
    """Filesystem-safe folder segment (keeps spaces; strips illegal chars)."""
    s = _ILLEGAL.sub("", str(name or "")).strip().strip(".")
    s = re.sub(r"\s+", " ", s).strip()
    return s or default


def ensure_output_root():
    """Create the output root on startup (no-op if it already exists)."""
    try:
        _mkdirs(OUTPUT_ROOT)
    except OSError:
        pass
    return OUTPUT_ROOT


def _attr(request_obj, *names):
    for n in names:
        v = getattr(request_obj, n, None)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def job_folder_name(request_obj):
    """"<job_number|tco_id>_<product name>_Compliance" (mirrors the reference).
    The product name is capped so deep subpaths stay within the Windows MAX_PATH
    (260-char) limit."""
    prefix = _safe(_attr(request_obj, "job_number", "tco_id") or "UNKNOWN")
    product = _safe(_attr(request_obj, "product_name", "eut_name") or "EUT")[:60].strip()
    return _safe("%s_%s_Compliance" % (prefix, product))


def job_dir(request_obj):
    return os.path.join(OUTPUT_ROOT, job_folder_name(request_obj))


def ensure_job_structure(jdir):
    """Create the full (empty) skeleton for a job if any part is missing."""
    subdirs = [
        os.path.join("01_TRF & Test Plan", "Reference Data"),
        os.path.join("04_Test Report", "Draft Report"),
        os.path.join("04_Test Report", "Final Report"),
        os.path.join("04_Test Report", "Review"),
    ]
    for t in _ALL_TEST_FOLDERS:
        subdirs.append(os.path.join("02_Test Datasheet", t))
        subdirs.append(os.path.join("03_Test Pictures", t))
    subdirs.append(os.path.join("02_Test Datasheet", "CE", "Test data"))
    subdirs.append(os.path.join("02_Test Datasheet", "Harmonics & Flicker", "Raw Data"))
    subdirs.append(os.path.join("02_Test Datasheet", "Harmonics & Flicker", "Functional Check Data"))
    for extra in _PICTURE_EXTRA:
        subdirs.append(os.path.join("03_Test Pictures", extra))
    for d in subdirs:
        try:
            _mkdirs(os.path.join(jdir, d))
        except OSError:
            pass


def _long(path):
    """Windows extended-length path (``\\\\?\\``) so deep trees beat the 260-char
    MAX_PATH limit; unchanged on other platforms."""
    if os.name == "nt":
        ap = os.path.abspath(path)
        if not ap.startswith("\\\\?\\"):
            return "\\\\?\\" + ap
    return path


def _mkdirs(path):
    os.makedirs(_long(path), exist_ok=True)


def _copy(src, dst_dir):
    if not src or not os.path.exists(src):
        return None
    try:
        _mkdirs(dst_dir)
        dst = os.path.join(dst_dir, os.path.basename(src))
        shutil.copy2(src, _long(dst))
        return dst
    except OSError:
        return None


def store_datasheet(request_obj, code, docx_path=None, images=None, data_files=None, logger=None):
    """Copy a generated datasheet + its images/data into the job-folder tree.

    docx  -> 02_Test Datasheet/<TEST>/
    images-> 03_Test Pictures/<TEST>/          (dict {key: path}; 'signature' skipped)
    data  -> 02_Test Datasheet/<TEST>/Test data/  (Harmonics & Flicker -> Raw Data/)

    Best-effort: returns the destination folder or None; never raises.
    """
    try:
        code = (code or "").upper()
        folder = TEST_FOLDER.get(code)
        if not folder or request_obj is None:
            return None
        ensure_output_root()
        jdir = job_dir(request_obj)
        ensure_job_structure(jdir)

        ds_dir = os.path.join(jdir, "02_Test Datasheet", folder)
        pic_dir = os.path.join(jdir, "03_Test Pictures", folder)
        data_sub = "Raw Data" if folder == "Harmonics & Flicker" else "Test data"
        data_dir = os.path.join(ds_dir, data_sub)

        if docx_path:
            _copy(docx_path, ds_dir)

        if isinstance(images, dict):
            img_items = images.items()
        else:
            img_items = [(str(i), p) for i, p in enumerate(images or [])]
        for key, path in img_items:
            if path and "sign" not in str(key).lower():   # skip the signature image
                _copy(path, pic_dir)

        for path in (data_files or []):
            _copy(path, data_dir)

        return jdir
    except Exception as exc:  # never break datasheet generation
        if logger:
            logger.error("output_store.store_datasheet failed: %s", exc)
        return None
