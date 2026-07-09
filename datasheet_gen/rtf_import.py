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
