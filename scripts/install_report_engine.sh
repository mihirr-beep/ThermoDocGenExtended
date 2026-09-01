#!/usr/bin/env bash
# Install the layout engine the Test Report needs, on a Debian/Ubuntu server.
#
# WHY THIS IS A SCRIPT AND NOT SOMETHING THE APP DOES
# ---------------------------------------------------
# Computing "this heading is on page 14" means laying the document out, and there is
# no pure-Python engine for it (report_gen/finalise.py explains the measurement). On
# Linux that engine is LibreOffice driven over UNO. Installing it needs root, needs
# the package repos, and pulls ~400 MB - none of which belongs in a request handler:
# the web app would need root, and a lazy install would hang the first report for
# minutes. So it is a one-time provisioning step, run by whoever deploys.
#
# Idempotent: safe to re-run, and it verifies rather than assumes.
#
#   sudo ./scripts/install_report_engine.sh
#
# Without it the report still BUILDS - every test's section is spliced with pure
# Python - but the contents and figure lists carry no page numbers and the reader's
# Word asks to update the fields. See SETUP.md section 11.
set -euo pipefail

PKGS="libreoffice-writer python3-uno"

say()  { printf '  %s\n' "$*"; }
fail() { printf '  ERROR: %s\n' "$*" >&2; exit 1; }

# ---- 1. right platform, right privileges -----------------------------------
command -v apt-get >/dev/null 2>&1 || fail \
  "this script is for Debian/Ubuntu (no apt-get here). On another distro install
  the equivalents of: $PKGS"

if [ "$(id -u)" -ne 0 ]; then
  fail "run as root: sudo $0"
fi

# ---- 2. already there? ------------------------------------------------------
have_soffice() { command -v soffice >/dev/null 2>&1; }
have_uno() {
  local py
  for py in python3 /usr/bin/python3; do
    "$py" -c 'import uno' >/dev/null 2>&1 && return 0
  done
  return 1
}

if have_soffice && have_uno; then
  say "already present: $(soffice --version 2>/dev/null | head -1)"
  say "uno bridge      : ok"
  say "nothing to do."
  exit 0
fi

# ---- 3. install ------------------------------------------------------------
say "installing: $PKGS"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# --no-install-recommends keeps this to the writer and the bridge rather than the
# whole office suite; both packages are still pulled in full.
apt-get install -y --no-install-recommends $PKGS

# ---- 4. verify, do not assume ----------------------------------------------
have_soffice || fail "soffice still not on PATH after installing $PKGS"
if ! have_uno; then
  fail "soffice is installed but 'import uno' fails.
  libreoffice-writer alone can convert files but cannot rebuild an index - that
  needs the UNO bridge (python3-uno). Check that the interpreter running the app
  is the one that can import uno; a venv created with --system-site-packages, or
  PYTHONPATH pointing at /usr/lib/python3/dist-packages, resolves the usual case."
fi

say "soffice   : $(command -v soffice)"
say "version   : $(soffice --version 2>/dev/null | head -1)"
say "uno bridge: ok"
say ""
say "now confirm the app agrees:  python tools_report_engine_check.py"
