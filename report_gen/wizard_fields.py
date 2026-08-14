# -*- coding: utf-8 -*-
"""One definition of every field the admin fills, read by the form AND the builder.

WHY A SPEC AND NOT TWO LISTS
----------------------------
The lesson from the per-test sections of this same report: the template and the
filler each kept their own idea of what a field was, and 49 of 92 subsections had
quietly drifted apart by the time anyone measured. A form built from one list and
a document filled from another would do it again within a month.

So the form is generated from FIELDS, the document is written from FIELDS, and a
field that is not here appears in neither.

WHERE EACH FIELD LIVES, AND WHY IT MATTERS
------------------------------------------
    store="request"  the column already exists on iec_emc_requests and is simply
                     never filled - measured: length, width, height,
                     dimension_unit, weight and operating_frequency are empty on
                     EVERY request in the database, including the real ones. The
                     wizard writes these BACK to the request rather than keeping
                     its own copy, so "weight of the EUT" has one home and the
                     next report for the same product inherits it.

    store="draft"    there is no column anywhere in the eighty tables. Either
                     report-specific (issue date, ULR, condition on receipt) or a
                     test-time observation (measured current) that the request
                     could not know at the time it was raised.

Nothing here duplicates a value the builder can already derive. 1.1 Results, 1.2
Applicable Standards, 1.4 Measurement Uncertainty and everything from section 4
onward are computed from the datasheets and the request, and are shown in the
wizard read-only: an editable copy could make the report disagree with what peer
review signed off.
"""

# ULR NO is a constant for now, by decision - there is no column for it anywhere
# and no rule for deriving one. When the lab has a numbering scheme this becomes
# a field with store="draft".
ULR_NO = "TC14704YY0XXXXXXXXF"

# (key, label, kind, store, report_location, help_text)
#   kind: text | date | textarea | image | choice
FIELDS = [
    # ---------------------------------------------------------- cover page
    ("condition_on_receipt", "Condition of EUT on receipt", "text", "draft",
     "cover: CONDITION OF EUT ON RECEIPT",
     "e.g. Received in good condition, no visible damage"),
    ("date_of_receipt", "Date of receipt of EUT", "date", "draft",
     "cover: DATE OF RECEIPT OF EUT",
     "Today this is written as NA, which reads as though the lab decided it was "
     "not applicable. It is simply never asked."),
    ("test_location", "Location of performance of test", "choice", "draft",
     "cover: LOCATION OF PERFORMANCE OF TEST",
     "Permanent or Onsite"),
    ("report_issue_date", "Test report issue date", "date", "draft",
     "cover: TEST REPORT ISSUE DATE", ""),
    ("issued_to", "Issued to - name and contact information", "textarea", "draft",
     "cover: ISSUED TO",
     "Filled here by hand. The request's requester is not always who the report "
     "is issued to."),

    # ------------------------------------------- cover signature block
    # Name: is filled from the database - who submitted the datasheets, who peer
    # reviewed them, who manages the laboratory. Signature and Date are not
    # derivable from anything: they record an act that happens when a person
    # signs, which is why they are asked for here. Until this existed the report
    # printed the ISSUE date in all three Date cells, which dated a signature
    # that had not been given.
    ("sign_date_prepared", "Date - Prepared By", "date", "draft",
     "cover signature block: Date / Prepared By", ""),
    ("sign_date_reviewed", "Date - Reviewed By", "date", "draft",
     "cover signature block: Date / Reviewed By", ""),
    ("sign_date_authorized", "Date - Authorized Signatory", "date", "draft",
     "cover signature block: Date / Authorized Signatory", ""),
    ("img_sign_prepared", "Signature - Prepared By", "image", "draft",
     "cover signature block: Signature / Prepared By", ""),
    ("img_sign_reviewed", "Signature - Reviewed By", "image", "draft",
     "cover signature block: Signature / Reviewed By", ""),
    ("img_sign_authorized", "Signature - Authorized Signatory", "image", "draft",
     "cover signature block: Signature / Authorized Signatory", ""),

    # ------------------------------------------------- 2.1 EUT DETAILS
    # These six have a column on iec_emc_requests already and are empty on every
    # request, so the wizard fills the request rather than shadowing it.
    # number, not text: these four are FLOAT columns on iec_emc_requests, and the
    # first test of the save path posted "12.4 kg" into weight and got MySQL
    # error 1265, "Data truncated". The unit belongs in dimension_unit (and kg
    # for weight), not inside the value.
    ("length", "EUT length", "number", "request", "2.1 Size of the EUT (L x W x H)",
     "Number only - the unit is set below"),
    ("width", "EUT width", "number", "request", "2.1 Size of the EUT (L x W x H)",
     "Number only"),
    ("height", "EUT height", "number", "request", "2.1 Size of the EUT (L x W x H)",
     "Number only"),
    ("dimension_unit", "Dimension unit", "choice", "request",
     "2.1 Size of the EUT (L x W x H)", "mm, cm or m"),
    ("weight", "Weight of the EUT (kg)", "number", "request", "2.1 Weight of the EUT",
     "Number only, in kilograms - the column is numeric"),
    ("operating_frequency", "EUT operating frequency", "text", "request",
     "2.1 EUT Operating Frequency", "e.g. 50 Hz"),
    # No column exists for these two. Power rating is a product property and
    # arguably belongs on the request; measured current is something the lab
    # measures during the test and the request could not have known.
    ("power_rating", "EUT power rating", "text", "draft", "2.1 EUT Power Rating",
     "e.g. 650 W"),
    ("measured_current", "Measured EUT current", "text", "draft",
     "2.1 Measured EUT Current", "As measured during the test, e.g. 2.8 A"),

    # ----------------------------------------------- 2.5 and 2.8 free text
    #
    # 2.3 and 2.7 USED TO BE HERE and are gone on purpose. Neither is typed any
    # more:
    #
    #   2.3 SOFTWARE AND FIRMWARE is a table built from datasheet_software -
    #       every test's software and version, one row each. It was listed here
    #       as "printed as NA because nothing supplies it", which was true of the
    #       REQUEST and never true of the datasheets.
    #
    #   2.7 EUT MODES OF OPERATION is the request's functional modes, labelled
    #       "Mode A:", "Mode B:" by position. A textarea beside it invited an
    #       admin to retype the modes and disagree with the request.
    #
    # Both are shown read-only on the wizard page with their real values, the
    # same as the cover rows the request supplies.
    #
    # 2.5 and 2.8 are not derived - but they are not homeless either. Both have a
    # column on iec_emc_requests that ten of the thirty requests already fill, so
    # they are written BACK to the request like the size and weight fields rather
    # than kept as a private second copy that the next report would not inherit.
    # The key IS the column name, which is why 2.5 is `test_configuration` here
    # and not the `eut_configuration` it was called while it lived in the draft.
    ("test_configuration", "EUT configuration during test", "textarea", "request",
     "2.5 EUT CONFIGURATION DURING TEST",
     "Prefilled from the test request. Editing it here updates the request."),
    ("monitoring_parameters", "EUT monitoring parameters", "textarea", "request",
     "2.8 EUT MONITORING PARAMETERS",
     "Prefilled from the test request. How the EUT was monitored, and with what."),

    # ------------------------------------------------------------- images
    ("img_block_diagram", "Block diagram of the EUT setup", "image", "draft",
     "2.6 EUT SETUP DETAILS - Figure 1", ""),
    ("img_eut_photo", "Photo of the EUT", "image", "draft",
     "2.9 EUT AND ACCESSORIES PICTURES - Photo 1", ""),
    ("img_eut_label", "Photo of the model / serial label", "image", "draft",
     "2.9 EUT AND ACCESSORIES PICTURES - Photo 2", ""),
    ("img_monitoring", "Screenshot of the monitoring software", "image", "draft",
     "2.8 EUT MONITORING PARAMETERS", ""),
]

CHOICES = {
    "test_location": ["Permanent", "Onsite"],
    "dimension_unit": ["mm", "cm", "m"],
}

# The box each image is fitted into IN THE DOCUMENT, in millimetres, taken from
# builder.py's DIAGRAM_BOX / PHOTO_BOX rather than restated - if those move, this
# moves with them.
#
# It matters that these are the REPORT's boxes and not the datasheet's. The
# datasheet form fits every test-setup photo into 159.2 x 95 mm (_box() in
# generic_generator); the report uses 150 x 95 for the block diagram and
# 140 x 90 for the EUT pictures. Cropping in the wizard against the datasheet's
# ratio would frame the picture to a shape the report never uses, and the
# difference shows up as a letterboxed or clipped image on the client's copy.
def image_box_mm(key):
    """(width_mm, height_mm) the document will fit this image into."""
    from .builder import DIAGRAM_BOX, PHOTO_BOX, SIGNATURE_BOX
    if key == "img_block_diagram":
        return DIAGRAM_BOX
    # A signature goes into a table cell, not onto the page. The Signature row
    # of the cover block is 20 mm tall; anything framed to the 140 x 90 photo
    # box would be scaled down to a smear or would stretch the row.
    if key.startswith("img_sign_"):
        return SIGNATURE_BOX
    return PHOTO_BOX


def image_boxes():
    """{key: [w_mm, h_mm]} for every image field, for the form's JS."""
    return {k: list(image_box_mm(k)) for k in image_keys()}

# Fields whose absence should stop nobody. Everything else is reported as
# outstanding in the completeness check - not silently turned into NA, which is
# the behaviour this whole phase exists to remove.
#
# The three signature images are optional because a laboratory that signs on
# paper is doing nothing wrong; the three signature DATES are not, because an
# unsigned date column is what this replaced.
OPTIONAL = {"img_monitoring", "measured_current",
            "img_sign_prepared", "img_sign_reviewed", "img_sign_authorized"}

# FLOAT columns. A value that will not parse is rejected with a message rather
# than sent to MySQL, which truncates and raises 1265 for the whole statement.
NUMERIC = {f[0] for f in FIELDS if f[2] == "number"}


def coerce(key, raw):
    """(value, error). Numeric fields become floats; everything else is text.

    Tolerant of the unit somebody types anyway - "12.4 kg" yields 12.4 - because
    the label asks for a number and a form that rejects the obvious input teaches
    people to distrust it. Only genuinely unparseable text is an error.
    """
    s = "" if raw is None else str(raw).strip()
    if key not in NUMERIC:
        return s, None
    if s == "":
        return None, None
    import re as _re
    m = _re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    if not m:
        return None, "%s must be a number" % key
    return float(m.group(0)), None


def _value_of(key, kind, store, draft_form, request_row, images):
    """Where this field's value actually lives.

    THE IMAGE CASE IS THE WHOLE REASON THIS EXISTS. An uploaded picture is stored
    in report_draft's ``images`` map, never in ``form`` - so reading the form for
    an image key returns nothing however many files were uploaded, and every
    required picture was reported outstanding forever. Measured: uploading the
    block diagram returned images_saved=1 and left img_block_diagram in the
    outstanding list on the same response.
    """
    if kind == "image":
        return (images or {}).get(key)
    if store == "request":
        return (request_row or {}).get(key)
    return (draft_form or {}).get(key)


def filled_count(draft_form, request_row, images=None):
    """How many of FIELDS actually have a value.

    Not "total minus outstanding": outstanding skips the optional ones, so that
    subtraction counted an empty optional field as filled and reported 3/21 for a
    request where nothing at all had been entered.
    """
    n = 0
    for key, _l, kind, store, _loc, _h in FIELDS:
        v = _value_of(key, kind, store, draft_form, request_row, images)
        if v is not None and str(v).strip() != "":
            n += 1
    return n


def by_store(store):
    return [f for f in FIELDS if f[3] == store]


def keys():
    return [f[0] for f in FIELDS]


def image_keys():
    return [f[0] for f in FIELDS if f[2] == "image"]


def spec(key):
    for f in FIELDS:
        if f[0] == key:
            return {"key": f[0], "label": f[1], "kind": f[2], "store": f[3],
                    "location": f[4], "help": f[5]}
    return None


def outstanding(draft_form, request_row, images=None):
    """Which required fields are still unsupplied. The completeness check.

    Reads the request for store="request" fields, the images map for pictures and
    the draft for the rest, so a value the admin has already supplied is not asked
    for twice - and an uploaded picture actually counts as supplied.
    """
    missing = []
    for key, label, kind, store, location, _help in FIELDS:
        if key in OPTIONAL:
            continue
        v = _value_of(key, kind, store, draft_form, request_row, images)
        if v is None or str(v).strip() == "":
            missing.append({"key": key, "label": label, "location": location,
                            "kind": kind})
    return missing
