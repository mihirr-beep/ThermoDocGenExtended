# -*- coding: utf-8 -*-
"""Keep nlp_search/schema_catalog.py honest, without anyone having to remember.

The catalog is the only thing the NL->SQL model ever sees of the database. It is
generated, committed, and about 150 of its facts are measured from live rows -
row counts, the value list of 74 columns, the JSON keys of 16 text columns. When
it goes stale the model is not confused, it is confidently wrong: it once read
`datasheet (24 rows)` from a catalog built against a different database and
answered questions about 24 datasheets that did not exist.

Two modes, wired up in .claude/settings.json:

  --on-edit   PostToolUse. A file that invalidates the catalog was just edited,
              so regenerate it now and tell the agent what changed.
  --check     SessionStart. Compare the committed catalog against the live
              database, read-only, and announce any drift up front.

Stdlib only: it must run before anyone has thought about which interpreter this
is. The regeneration itself is a subprocess under the project's venv, which is
where pymysql lives.
"""
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CATALOG = os.path.join(ROOT, "nlp_search", "schema_catalog.py")

# Editing any of these changes what the catalog SHOULD say, so the committed
# copy is stale the moment the edit lands. Matched against the repo-relative
# path with forward slashes.
WATCHED = (
    "nlp_search/build_catalog.py",        # the generator itself
    "datasheet_gen/projection_schema.py",  # creates and alters the analytical tables
    "datasheet_gen/projection.py",         # decides what lands in them
    "models.py",                           # the core request/planner schema
)

# Drift below this fraction is just the app being used - people file requests and
# fill datasheets all day. Report a table only when its count moved by more than
# this, or crossed zero in either direction, which is the failure that matters:
# a table the catalog calls EMPTY that now has rows is invisible to the model,
# and one it calls populated that is now empty invites invented answers.
DRIFT_FRACTION = 0.5


def _venv_python():
    """The interpreter that has pymysql, wherever this checkout put it."""
    for rel in ("venv/Scripts/python.exe", "venv/bin/python",
                ".venv/Scripts/python.exe", ".venv/bin/python"):
        cand = os.path.join(ROOT, *rel.split("/"))
        if os.path.isfile(cand):
            return cand
    return sys.executable


def _emit(system_message=None, context=None):
    """Hook protocol: systemMessage reaches the user, additionalContext the model."""
    out = {"suppressOutput": True}
    if system_message:
        out["systemMessage"] = system_message
    if context:
        out["hookSpecificOutput"] = {"hookEventName": os.environ.get(
            "CLAUDE_HOOK_EVENT", "PostToolUse"), "additionalContext": context}
    sys.stdout.write(json.dumps(out))
    return 0


def _stdin_json():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}


def _edited_path(payload):
    inp = payload.get("tool_input") or {}
    resp = payload.get("tool_response") or {}
    path = (resp.get("filePath") if isinstance(resp, dict) else None) \
        or inp.get("file_path") or inp.get("notebook_path") or ""
    if not path:
        return ""
    try:
        rel = os.path.relpath(os.path.abspath(path), ROOT)
    except ValueError:          # different drive on Windows
        return ""
    return rel.replace("\\", "/")


def _catalog_counts():
    """{table: rows} as the committed catalog states them, or None if unreadable."""
    if not os.path.isfile(CATALOG):
        return None
    try:
        with io.open(CATALOG, encoding="utf-8") as fh:
            text = fh.read()
    except (IOError, OSError):
        return None
    m = re.search(r"^ROW_COUNTS = (\{.*?\})$", text, re.M | re.S)
    if not m:
        return None                     # catalog predates ROW_COUNTS
    try:
        import ast
        return ast.literal_eval(m.group(1))
    except (ValueError, SyntaxError):
        return None


def regenerate():
    """Run the generator under the venv. Returns (ok, one-line summary)."""
    proc = subprocess.Popen(
        [_venv_python(), "-m", "nlp_search.build_catalog"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.communicate()[0].decode("utf-8", "replace")
    if proc.returncode != 0:
        tail = [ln for ln in out.strip().split("\n") if ln.strip()][-1:] or [""]
        return False, tail[0][:200]
    for line in out.split("\n"):
        if line.strip().startswith("database:"):
            return True, line.strip()
    return True, "catalog rewritten"


def on_edit():
    rel = _edited_path(_stdin_json())
    if not rel or rel not in WATCHED:
        return 0                        # silent: the common case by far
    before = _catalog_counts()
    ok, summary = regenerate()
    if not ok:
        return _emit(
            system_message="Catalog NOT regenerated after editing %s" % rel,
            context="Editing %s invalidated nlp_search/schema_catalog.py, but "
                    "regenerating it failed: %s\nThe committed catalog is now "
                    "stale - every prompt is quoting figures that no longer "
                    "describe the database. Fix this before trusting an answer, "
                    "then run: python -m nlp_search.build_catalog" % (rel, summary))
    after = _catalog_counts()
    note = ""
    if before is not None and after is not None:
        added = sorted(set(after) - set(before))
        dropped = sorted(set(before) - set(after))
        if added:
            note += "\nTables now in the catalog that were not before: %s" % ", ".join(added)
        if dropped:
            note += "\nTables gone from the catalog: %s" % ", ".join(dropped)
    return _emit(
        system_message="Regenerated schema_catalog.py (%s changed it)" % rel,
        context="You edited %s, so nlp_search/schema_catalog.py was regenerated "
                "automatically - it is generated, not hand-written. %s%s\n"
                "It is a tracked file, so it will appear in git status; commit it "
                "with your change. If tables were added, check they are in a "
                "DOMAINS slice in build_catalog.py, or no worker can see them."
                % (rel, summary, note))


def check():
    """Read-only: does the committed catalog still describe this database?"""
    stated = _catalog_counts()
    if stated is None:
        return _emit(context=(
            "nlp_search/schema_catalog.py has no ROW_COUNTS block, so it predates "
            "the staleness check and its age is unknown. Regenerate before "
            "trusting any NL search answer: python -m nlp_search.build_catalog"))

    # Count only the tables the generator would itself include. EXCLUDE and
    # EXCLUDE_PREFIXES are deliberate - the 16 datasheet_rev_* mirrors and the
    # legacy orphans are kept out on purpose - so reading them from the
    # generator is the difference between reporting drift and reporting its
    # design decisions back at it.
    code = (
        "import sys, os; sys.path.insert(0, %r)\n"
        "import pymysql, mysql_config, json\n"
        "from nlp_search.build_catalog import EXCLUDE, EXCLUDE_PREFIXES\n"
        "c = mysql_config.config['default']\n"
        "cn = pymysql.connect(host=c.MYSQL_HOST, port=int(c.MYSQL_PORT), "
        "user=c.MYSQL_USER, password=c.MYSQL_PASSWORD, database=c.MYSQL_DATABASE, "
        "charset='utf8mb4')\n"
        "cur = cn.cursor()\n"
        "cur.execute(\"SELECT table_name FROM information_schema.tables \"\n"
        "            \"WHERE table_schema=%%s AND table_type='BASE TABLE'\", "
        "(c.MYSQL_DATABASE,))\n"
        "names = [r[0] for r in cur.fetchall()\n"
        "         if r[0] not in EXCLUDE and not r[0].startswith(EXCLUDE_PREFIXES)]\n"
        "out = {}\n"
        "for n in names:\n"
        "    cur.execute('SELECT COUNT(*) FROM `' + n + '`')\n"
        "    out[n] = cur.fetchone()[0]\n"
        "print(json.dumps({'db': c.MYSQL_DATABASE, 'counts': out}))\n" % ROOT)
    proc = subprocess.Popen([_venv_python(), "-c", code], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw, err = proc.communicate()
    if proc.returncode != 0:
        # No database reachable is normal - offline, wrong host, not set up yet.
        # Say so once rather than pretending the catalog was verified.
        detail = err.decode("utf-8", "replace").strip().split("\n")[-1][:160]
        return _emit(context=(
            "Could not verify nlp_search/schema_catalog.py against the database "
            "(%s). The catalog is generated and committed, so it describes "
            "whichever database last built it - treat its row counts, enum values "
            "and JSON keys as unverified until "
            "`python -m nlp_search.build_catalog` runs here." % detail))

    try:
        live = json.loads(raw.decode("utf-8", "replace").strip().split("\n")[-1])
    except (ValueError, IndexError):
        return 0
    counts, dbname = live["counts"], live["db"]

    missing = sorted(set(counts) - set(stated))     # in the DB, not in the catalog
    extra = sorted(set(stated) - set(counts))       # in the catalog, not in the DB
    moved = []
    for name, was in sorted(stated.items()):
        if name not in counts:
            continue
        now = counts[name]
        if was == now:
            continue
        crossed_zero = (was == 0) != (now == 0)
        big = abs(now - was) > max(1.0, was * DRIFT_FRACTION)
        if crossed_zero or big:
            moved.append("%s %d->%d" % (name, was, now))

    if not (missing or extra or moved):
        return 0                        # the catalog is current; say nothing

    lines = ["nlp_search/schema_catalog.py no longer matches database `%s`."
             % dbname]
    if missing:
        lines.append("INVISIBLE TO THE MODEL - tables exist but are absent from "
                     "the catalog, so sql_guard refuses them and no worker can "
                     "query them: %s" % ", ".join(missing))
    if extra:
        lines.append("DESCRIBED BUT GONE - the catalog documents tables this "
                     "database does not have: %s" % ", ".join(extra))
    if moved:
        lines.append("ROW COUNTS the prompt states, vs actual: %s"
                     % "; ".join(moved))
    lines.append("Every table heading in the prompt quotes the stale figure, and "
                 "ENUM_VALUES / JSON_KEYS were sampled from those same old rows, "
                 "so status vocabularies may be wrong too. Regenerate before "
                 "trusting an answer: python -m nlp_search.build_catalog")
    return _emit(
        system_message="Schema catalog is stale for `%s` - see context" % dbname,
        context="\n".join(lines))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    try:
        sys.exit(check() if mode == "--check" else on_edit())
    except Exception as exc:            # a hook must never break the session
        sys.stderr.write("catalog_guard: %s\n" % exc)
        sys.exit(0)
