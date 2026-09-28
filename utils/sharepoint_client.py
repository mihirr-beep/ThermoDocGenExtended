# -*- coding: utf-8 -*-
"""Microsoft Graph client for mirroring datasheets to SharePoint.

A port of the working Streamlit prototype (sharepoint_logic/main.py, from line 239): MSAL
client-credentials -> site id -> library drive id -> PUT .../content. App-only, no user
sign-in.

TWO THINGS THE PROTOTYPE DID THAT ARE DELIBERATELY NOT REPRODUCED

  * it cached site id and drive id with @st.cache_data keyed on the ACCESS TOKEN, which put
    a rotating bearer token in the cache key - so the cache missed on every token refresh
    and re-resolved the site each hour. They are cached here in a module dict, keyed on
    nothing, because for a given deployment they never change.
  * it could only VERIFY a folder. Mirroring needs to create the job folder and its
    subfolders, so ensure_folder() creates each missing segment and treats 409 as success.

The client secret is read from the environment and never logged: the failure paths print
Graph's own error description, which does not contain it.

Everything here is a no-op when SHAREPOINT_ENABLED is not true, so a machine with no
network access - or a deployment that has not been given credentials - runs unchanged.
"""
import logging
import os
import threading
from urllib.parse import quote

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"

#: Graph's simple upload is capped (4 MB at the time of writing). Datasheets are ~60-900 KB,
#: so a file over this is a surprise worth a clear error rather than a silent failure.
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024

_LOCK = threading.Lock()
_STATE = {"app": None, "site_id": None, "drive_id": None, "truststore": False}


class SharePointError(RuntimeError):
    """A Graph call failed. The message carries Graph's own description, never the secret."""


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def enabled():
    """True when the mirror is switched on AND has something to authenticate with."""
    if _env("SHAREPOINT_ENABLED").lower() not in ("1", "true", "yes", "on"):
        return False
    return all(_env(k) for k in ("SHAREPOINT_TENANT_ID", "SHAREPOINT_CLIENT_ID",
                                 "SHAREPOINT_CLIENT_SECRET"))


def _projects_root():
    """Imported lazily: paths is the higher-level module and must not be a load-time dep."""
    from utils import sharepoint_paths
    return sharepoint_paths.projects_root()


def config_summary():
    """What is configured, for a log line or a health check. No secret in it."""
    return {
        "enabled": enabled(),
        "hostname": _env("SHAREPOINT_HOSTNAME", "thermofisher.sharepoint.com"),
        "site_path": _env("SHAREPOINT_SITE_PATH", "/sites/IECRDSiteRepository"),
        "library": _env("SHAREPOINT_LIBRARY", "Product Testing Lab"),
        "projects_root": _projects_root(),
        "tenant": _env("SHAREPOINT_TENANT_ID")[:8] + "..." if _env("SHAREPOINT_TENANT_ID") else "",
        "client_secret_set": bool(_env("SHAREPOINT_CLIENT_SECRET")),
    }


def _use_system_trust_store():
    """Corporate TLS inspection presents an internal root CA that certifi does not carry.

    The prototype called this unconditionally at import; here it runs once, lazily, and a
    missing truststore is a warning rather than an import error - the probe on this host
    succeeded without it, but a host behind the inspecting proxy will not.
    """
    if _STATE["truststore"]:
        return
    try:
        import truststore
        truststore.inject_into_ssl()
        log.debug("SharePoint: using the system trust store")
    except Exception as exc:  # noqa: BLE001 - never block the upload on this
        log.warning("SharePoint: truststore unavailable (%s); TLS inspection may fail", exc)
    _STATE["truststore"] = True


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
def get_token():
    """An app-only Graph token. MSAL holds its own cache, so this is cheap to call."""
    import msal
    _use_system_trust_store()
    with _LOCK:
        if _STATE["app"] is None:
            _STATE["app"] = msal.ConfidentialClientApplication(
                client_id=_env("SHAREPOINT_CLIENT_ID"),
                authority="%s/%s" % (LOGIN_BASE, _env("SHAREPOINT_TENANT_ID")),
                client_credential=_env("SHAREPOINT_CLIENT_SECRET"),
            )
        app = _STATE["app"]
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    token = result.get("access_token")
    if not token:
        raise SharePointError("could not get a Graph token: %s"
                             % (result.get("error_description") or result.get("error") or "unknown"))
    return token


def _headers(token, **extra):
    head = {"Authorization": "Bearer %s" % token, "Accept": "application/json"}
    head.update(extra)
    return head


def _get(url, token):
    import requests
    resp = requests.get(url, headers=_headers(token), timeout=60)
    if not resp.ok:
        raise SharePointError("Graph GET %s -> %s: %s" % (url, resp.status_code, resp.text[:300]))
    return resp.json()


# ---------------------------------------------------------------------------
# site and library, resolved once
# ---------------------------------------------------------------------------
def site_id(token=None):
    if _STATE["site_id"]:
        return _STATE["site_id"]
    token = token or get_token()
    host = _env("SHAREPOINT_HOSTNAME", "thermofisher.sharepoint.com")
    path = _env("SHAREPOINT_SITE_PATH", "/sites/IECRDSiteRepository").rstrip("/")
    site = _get("%s/sites/%s:%s" % (GRAPH_BASE, host, path), token)
    _STATE["site_id"] = site["id"]
    log.info("SharePoint: site %r resolved", site.get("displayName"))
    return _STATE["site_id"]


def drive_id(token=None):
    """The document library's drive id, matched on the library's display name."""
    if _STATE["drive_id"]:
        return _STATE["drive_id"]
    token = token or get_token()
    wanted = _env("SHAREPOINT_LIBRARY", "Product Testing Lab")
    drives = _get("%s/sites/%s/drives" % (GRAPH_BASE, site_id(token)), token).get("value", [])
    for drive in drives:
        if (drive.get("name") or "").strip().lower() == wanted.lower():
            _STATE["drive_id"] = drive["id"]
            return _STATE["drive_id"]
    available = ", ".join(sorted(d.get("name", "") for d in drives)) or "none"
    raise SharePointError("document library %r not found; the site has: %s" % (wanted, available))


def reset_cache():
    """Forget the resolved site/drive - for tests and for a config change at runtime."""
    with _LOCK:
        _STATE.update({"app": None, "site_id": None, "drive_id": None})


# ---------------------------------------------------------------------------
# folders and upload
# ---------------------------------------------------------------------------
def _encode(path):
    return quote(str(path).strip("/"), safe="/")


def folder_exists(path, token=None):
    import requests
    token = token or get_token()
    url = "%s/drives/%s/root:/%s" % (GRAPH_BASE, drive_id(token), _encode(path))
    resp = requests.get(url, headers=_headers(token), timeout=60)
    return resp.ok and "folder" in (resp.json() or {})


def ensure_folder(path, token=None):
    """Create `path` and any missing parent, and return it.

    Segment by segment, because Graph creates one child at a time. A 409 means someone
    else got there first, which is success. Nothing is created for a segment that already
    exists, so this is safe to call on every upload.
    """
    import requests
    token = token or get_token()
    drive = drive_id(token)
    segments = [s for s in str(path).strip("/").split("/") if s]
    built = ""
    for segment in segments:
        parent, built = built, ("%s/%s" % (built, segment) if built else segment)
        if folder_exists(built, token):
            continue
        url = ("%s/drives/%s/root:/%s:/children" % (GRAPH_BASE, drive, _encode(parent))
               if parent else "%s/drives/%s/root/children" % (GRAPH_BASE, drive))
        resp = requests.post(
            url, headers=_headers(token, **{"Content-Type": "application/json"}),
            json={"name": segment, "folder": {},
                  "@microsoft.graph.conflictBehavior": "fail"}, timeout=60)
        if resp.status_code in (200, 201):
            log.info("SharePoint: created folder %s", built)
        elif resp.status_code == 409:
            log.debug("SharePoint: folder %s already existed", built)
        else:
            raise SharePointError("could not create folder %r -> %s: %s"
                                  % (built, resp.status_code, resp.text[:300]))
    return path


def upload(local_path, dest_path, token=None):
    """Upload one file to `dest_path` (library-relative), replacing what is there.

    Stable names plus conflictBehavior=replace: re-saving a draft overwrites its file
    instead of leaving a trail of near-identical ones, and SharePoint's version history
    keeps the per-save record.

    Returns the item's webUrl.
    """
    import requests
    if not os.path.exists(local_path):
        raise SharePointError("nothing to upload: %s does not exist" % local_path)
    size = os.path.getsize(local_path)
    if size > SIMPLE_UPLOAD_LIMIT:
        raise SharePointError(
            "%s is %.1f MB, over Graph's %.0f MB simple-upload limit - this needs an upload "
            "session, which is not implemented because datasheets are under a megabyte"
            % (os.path.basename(local_path), size / 1048576.0, SIMPLE_UPLOAD_LIMIT / 1048576.0))
    token = token or get_token()
    parent = "/".join(str(dest_path).strip("/").split("/")[:-1])
    if parent:
        ensure_folder(parent, token)
    url = ("%s/drives/%s/root:/%s:/content?@microsoft.graph.conflictBehavior=replace"
           % (GRAPH_BASE, drive_id(token), _encode(dest_path)))
    with open(local_path, "rb") as fh:
        resp = requests.put(
            url,
            headers=_headers(token, **{
                "Content-Type": "application/vnd.openxmlformats-officedocument"
                                ".wordprocessingml.document"}),
            data=fh.read(), timeout=300)
    if resp.status_code not in (200, 201):
        raise SharePointError("upload of %s failed -> %s: %s"
                              % (os.path.basename(local_path), resp.status_code, resp.text[:300]))
    item = resp.json()
    log.info("SharePoint: uploaded %s (%d bytes)", dest_path, size)
    return item.get("webUrl") or ""
