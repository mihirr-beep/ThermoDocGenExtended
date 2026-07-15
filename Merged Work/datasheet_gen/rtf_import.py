"""Extract the 'Average and Maximum harmonic current results' table from an
IEC 61000-3-2 harmonics instrument RTF export.

The instrument writes the results as a (nested) RTF table. We locate the table
by its title, then split the following region into rows/cells on the RTF row/cell
delimiters and keep the sequential harmonic-number data rows. Returns a list of
10-column row dicts (c0..c9):

    c0 Hn | c1..c4 Average(Ieff, of Limit %, Limit, Result)
          | c5..c8 Maximum(Ieff, of Limit %, Limit, Result) | c9 Harmonic Result
"""
import re

_TITLE = "average and maximum harmonic current results"
_COLS = 10
_MAX_HARMONIC = 40  # IEC 61000-3-2 goes up to the 40th harmonic


def _cell_text(chunk):
    """Plain text of one RTF cell: drop hex escapes, control words and braces,
    leaving the literal run text (numbers/labels)."""
    s = re.sub(r"\\'[0-9a-fA-F]{2}", "", chunk)   # \'xx hex escapes
    s = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", s)     # \controlword / \cw123
    s = s.replace("{", " ").replace("}", " ")
    return " ".join(s.split()).strip()


def parse_avgmax_table(data):
    """Return the Average/Maximum harmonic table as a list of {c0..c9} dicts.
    Empty list if the titled table isn't found."""
    text = data.decode("latin-1", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
    i = text.lower().find(_TITLE)
    if i < 0:
        return []
    region = text[i:]
    # nested table (\nestrow/\nestcell) or a plain one (\row/\cell)
    if r"\nestrow" in region:
        rowsep, cellsep = r"\nestrow", r"\nestcell"
    else:
        rowsep, cellsep = r"\row", r"\cell"

    rows = []
    last_hn = 0
    for row_chunk in region.split(rowsep):
        cells = [_cell_text(c) for c in row_chunk.split(cellsep)]
        if len(cells) > 1:
            cells = cells[:-1]                    # trailing content after final cell delim
        c0 = cells[0].strip() if cells else ""
        if not c0.isdigit():
            continue                              # header / non-data row
        hn = int(c0)
        if rows and hn <= last_hn:
            break                                 # numbering restarted -> next table began
        if hn > _MAX_HARMONIC:
            break
        cells = (cells + [""] * _COLS)[:_COLS]     # pad/truncate to 10 columns
        rows.append({f"c{j}": cells[j] for j in range(_COLS)})
        last_hn = hn
    return rows


# --------------------------------------------------------------------------
# Flicker (IEC 61000-3-3) Functional Check "Flicker Measurements" table.
# The instrument RTF has three labelled rows (Line 1 / Limits / Results), each
# with five values in the column order: Plt, Max Pst, Max dc, Max dmax, Max Tmax.
# Returned as generic-table rows {c0..c5}: c0 = row label, c1..c5 = the values.
# --------------------------------------------------------------------------
_FC_LABELS = {"line 1": "Line 1:", "limits": "Limits:", "results": "Results:"}
_FC_COLS = 6  # c0 label + 5 parameter values


def parse_flicker_fc(data):
    """Return the Flicker Functional-Check rows [Line 1, Limits, Results] as a
    list of {c0..c5} dicts. Empty list if not found."""
    text = data.decode("latin-1", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
    anchor = text.find("Line 1:")
    if anchor < 0:
        return []
    region = text[anchor - 200: anchor + 5200]
    found = {}
    for row_chunk in region.split(r"\nestrow"):
        cells = [_cell_text(c) for c in row_chunk.split(r"\nestcell")]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue
        label = cells[0].rstrip(":").strip().lower()
        if label in _FC_LABELS and label not in found:
            vals = (cells + [""] * _FC_COLS)[:_FC_COLS]
            vals[0] = _FC_LABELS[label]
            found[label] = {f"c{j}": vals[j] for j in range(_FC_COLS)}
    return [found[k] for k in ("line 1", "limits", "results") if k in found]
