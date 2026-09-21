# -*- coding: utf-8 -*-
# AISAKA/CAELUS Server v2 - reconstrução fiel do servidor original
# Mantém: health, settings, device/initialize, account-info, batch thumbnails,
#          universal-app-config proxy, crash upload, download APK, _logs, signalr
# Corrige: catálogo (catalog.roblox.com em vez de apis.roblox.com)
#          e imagens do CDN rbxcdn (proxy de PNG/JPG/WebP)
import os
import re
import json
import time
import uuid
import glob
import threading
from datetime import datetime, timezone

from flask import Flask, request, jsonify, Response, send_file
import requests as rq

app = Flask(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data" if os.path.isdir("/data") else ".")
LOG_DIR = os.path.join(DATA_DIR, "logs")
CRASH_DIR = os.path.join(DATA_DIR, "crashes")
DL_DIR = os.environ.get("DL_DIR", DATA_DIR)
FF_FILE = os.environ.get("FF_FILE", "Brayan.apk")
UPLOAD_KEY = os.environ.get("UPLOAD_KEY", "rzim-up-2026")
CRASH_SEQ = [0]
LOG_LOCK = threading.Lock()

for _d in (LOG_DIR, CRASH_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass

LOG_FILES = {
    "requests.log": os.path.join(LOG_DIR, "requests.log"),
    "unknown_endpoints.log": os.path.join(LOG_DIR, "unknown_endpoints.log"),
    "response_samples.log": os.path.join(LOG_DIR, "response_samples.log"),
}

KNOWN_PREFIXES = (
    "/v1/settings", "/device/initialize", "/users/account-info", "/v1/batch",
    "/universal-app-configuration", "/catalog/", "/mobileapi/", "/notifications/",
    "/crash/upload", "/download/", "/_logs", "/health", "/avatar/v1/",
)

CDN_PATTERNS = [
    re.compile(r"^/[A-Za-z0-9_\-]+-(Png|Jpg|Webp|WebP)/\d+x\d+/"),
    re.compile(r"^/[A-Za-z0-9_\-]{16,}/\d+x\d+/"),
]
UPSTREAMS = {
    "catalog": "https://catalog.roblox.com",
    "thumbnails": "https://thumbnails.roblox.com",
    "apis": "https://apis.roblox.com",
    "cdn": "https://tr.rbxcdn.com",
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def log_line(fname, text):
    path = LOG_FILES.get(fname)
    if not path:
        return
    try:
        with LOG_LOCK:
            with open(path, "a", encoding="utf-8", errors="replace") as f:
                f.write("[%s] %s\n" % (now_iso(), text))
    except Exception:
        pass


def log_request(known=True):
    body = ""
    try:
        raw = request.get_data()[:400]
        if raw:
            body = raw.decode("utf-8", errors="replace")
    except Exception:
        body = ""
    line = "%s %s UA=%s BODY=%s" % (
        request.method, request.full_path, request.headers.get("User-Agent", "")[:120], body
    )
    log_line("requests.log", line)
    if not known:
        log_line("unknown_endpoints.log", line)


def sample(tag, obj):
    try:
        log_line("response_samples.log", "%s: %s" % (tag, json.dumps(obj)[:1500]))
    except Exception:
        pass


def proxy_json(upstream_base, path_override=None, tag="PROXY"):
    url = upstream_base + (path_override if path_override is not None else request.full_path)
    if url.endswith("?"):
        url = url[:-1]
    hdrs = {}
    for k in ("Content-Type", "Accept", "User-Agent", "Accept-Language"):
        v = request.headers.get(k)
        if v:
            hdrs[k] = v
    body = request.get_data() or b""
    if request.headers.get("Content-Encoding", "").lower() == "gzip" and body:
        import gzip as _gz
        try:
            body = _gz.decompress(body)
        except Exception:
            pass
    try:
        resp = rq.request(request.method, url, headers=hdrs,
                          data=body, timeout=25)
        size = len(resp.content or b"")
        log_line("requests.log", "%s %s -> %s (%db)" % (
            tag, url, resp.status_code, size))
        return Response(resp.content, status=resp.status_code,
                        content_type=resp.headers.get("Content-Type", "application/json"))
    except Exception as e:
        log_line("requests.log", "%s FAIL %s (%s)" % (tag, url, e))
        return jsonify({"data": [], "errors": None})


def server_base():
    return "https://" + (request.host or "web-production-115c6.up.railway.app")


# ---------------- rotas principais ----------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "online", "server": "AISAKA/CAELUS", "version": "0.1.0"})


@app.route("/v1/settings/application", methods=["GET"])
def settings_application():
    log_request()
    base = server_base()
    resp = {
        "applicationSettings": {
            "JavaCrashUploadToBacktracePercentage": "100",
            "JavaCrashUploadToBacktraceUrl": base + "/crash/upload?src=java",
            "JavaCrashUploadToBacktraceToken": "rzim",
            "CrashUploadToBacktracePercentage": "100",
            "CrashUploadToBacktraceUrl": base + "/crash/upload?src=native",
            "EnableCrashpadOnAndroidPercentage": "0",
            "FFlagDebugDisableTelemetryEphemeralCounter": "True",
            "FFlagDebugDisableTelemetryEphemeralStat": "True",
            "FFlagDebugDisableTelemetryEventIngest": "True",
            "FFlagDebugDisableTelemetryPoint": "True",
            "FFlagDebugDisableTelemetryV2Counter": "True",
            "FFlagDebugDisableTelemetryV2Event": "True",
            "FFlagDebugDisableTelemetryV2Stat": "True",
            "DFIntDefaultFramebufferQuality": "5",
        }
    }
    sample("cs_v1", resp)
    return jsonify(resp)


@app.route("/device/initialize", methods=["POST"])
def device_initialize():
    log_request()
    try:
        dev = (request.get_json(silent=True) or {}).get("mobileDeviceId", "")
    except Exception:
        dev = ""
    resp = {"browserTrackerId": 4393622086}
    sample("device_initialize", {"dev": dev, "resp": resp})
    return jsonify(resp)


@app.route("/users/account-info", methods=["GET"])
def account_info():
    log_request()
    resp = {
        "UserId": 1,
        "AgeBracket": 1,
        "Username": "Player1",
        "DisplayName": "Player1",
        "CountryCode": "BR",
        "Email": {"Value": ""},
        "MembershipType": 0,
        "RobuxBalance": 0,
    }
    sample("account_info", {"resp": resp})
    return jsonify(resp)


@app.route("/v1/batch", methods=["POST", "GET"])
def batch():
    log_request()
    return proxy_json(UPSTREAMS["thumbnails"] + "/v1/batch", path_override="")


@app.route("/universal-app-configuration/<path:sub>", methods=["GET", "POST"])
def universal_app_config(sub):
    log_request()
    return proxy_json(UPSTREAMS["apis"] + "/universal-app-configuration/" + sub, path_override="", tag="PROXY")


@app.route("/catalog/<path:sub>", methods=["GET", "POST"])
def catalog(sub):
    # CORRECAO: catalog.roblox.com responde anonimo; apis.roblox.com dava erro vazio
    log_request()
    q = ("?" + request.query_string.decode()) if request.query_string else ""
    return proxy_json(UPSTREAMS["catalog"] + "/" + sub + q, path_override="", tag="PROXY")


import re as _re


def _validate_username(u, ctx="Signup"):
    if not u:
        return False, 1, "A valid username is required."
    if len(u) < 3 or len(u) > 20:
        return False, 4, "That username is too short or too long."
    if u.startswith("_") or u.endswith("_"):
        return False, 5, "Usernames cannot start or end with an underscore."
    if "__" in u:
        return False, 6, "Usernames cannot have more than one underscore in a row."
    if " " in u:
        return False, 7, "Usernames cannot contain spaces."
    if not _re.match(r"^[A-Za-z0-9_]+$", u):
        return False, 8, "Usernames can only contain letters, numbers and underscores."
    return True, 1, "Username is valid."


def _validate_password(p, u=""):
    if not p:
        return False, 3, "Password is required."
    if len(p) < 8:
        return False, 3, "Your password must be at least 8 characters."
    if u and p.lower() == u.lower():
        return False, 4, "Your password cannot be the same as your username."
    return True, 1, "Password is valid."


@app.route("/signup/is-username-valid", methods=["GET"])
def signup_is_username_valid():
    log_request()
    username = request.args.get("username", "")
    ok, code, msg = _validate_username(username)
    resp = {"IsValid": ok, "Errors": [] if ok else [{"Code": code, "Message": msg}]}
    sample("is_username_valid", {"username": username, "resp": resp})
    return jsonify(resp)


@app.route("/signup/is-password-valid", methods=["GET"])
def signup_is_password_valid():
    log_request()
    username = request.args.get("username", "")
    password = request.args.get("password", "")
    ok, code, msg = _validate_password(password, username)
    resp = {"IsValid": ok, "Errors": [] if ok else [{"Code": code, "Message": msg}]}
    sample("is_password_valid", {"username": username, "resp": resp})
    return jsonify(resp)


@app.route("/v2/signup", methods=["POST"])
def v2_signup():
    log_request()
    body = request.get_json(silent=True) or {}
    username = body.get("username", "Player1")
    password = body.get("password", "")
    ok_u, _, msg_u = _validate_username(username)
    ok_p, _, msg_p = _validate_password(password, username)
    if not ok_u:
        return jsonify({"errors": [{"code": 5, "message": msg_u}]}), 403
    if not ok_p:
        return jsonify({"errors": [{"code": 7, "message": msg_p}]}), 403
    resp = {"userId": 1, "starterPlaceId": 1818}
    sample("v2_signup", {"username": username, "resp": resp})
    r = jsonify(resp)
    r.headers["Set-Cookie"] = ".ROBLOSECURITY=EMULATED_SESSION_TOKEN_PLAYER1; Path=/; HttpOnly"
    return r


@app.route("/v2/login", methods=["POST"])
def v2_login():
    log_request()
    resp = {"user": {"id": 1, "name": "Player1", "displayName": "Player1"}}
    sample("v2_login", resp)
    r = jsonify(resp)
    r.headers["Set-Cookie"] = ".ROBLOSECURITY=EMULATED_SESSION_TOKEN_PLAYER1; Path=/; HttpOnly"
    return r


@app.route("/mobileapi/check-app-version", methods=["GET"])
def check_app_version():
    log_request()
    resp = {
        "success": True,
        "message": "Version is up to date",
        "data": {"latestVersion": "2.463.417712", "isForcedUpdate": False},
        "isForcedUpdate": False,
    }
    return jsonify(resp)


@app.route("/notifications/signalr/negotiate", methods=["GET"])
def signalr_negotiate():
    log_request()
    return jsonify({
        "connectionId": str(uuid.uuid4()),
        "connectionToken": str(uuid.uuid4()),
        "negotiateVersion": 1,
        "connectionId2": str(uuid.uuid4()),
        "availableTransports": [],
        "error": None,
    })


@app.route("/crash/upload", methods=["POST", "GET"])
def crash_upload():
    src = request.args.get("src", "unknown")
    log_request()
    data = request.get_data() or b""
    CRASH_SEQ[0] += 1
    fname = "%d_%d.dmp" % (int(time.time() * 1000), CRASH_SEQ[0])
    path = os.path.join(CRASH_DIR, fname)
    try:
        with open(path, "wb") as f:
            f.write(data)
    except Exception:
        pass
    log_line("requests.log", "crash src=%s bytes=%d file=%s" % (src, len(data), fname))
    return jsonify({"uploaded": True, "file": fname})


@app.route("/download/<path:name>", methods=["GET"])
def download(name):
    log_request()
    candidates = [os.path.join(DL_DIR, name)]
    try:
        candidates += glob.glob(os.path.join(DATA_DIR, "**", name), recursive=True)
    except Exception:
        pass
    for c in candidates:
        if c and os.path.isfile(c):
            return send_file(c, as_attachment=True, download_name=name)
    return jsonify({"data": [], "errors": None}), 404


@app.route("/_logs", methods=["GET"])
def logs_endpoint():
    key = request.args.get("key", "")
    fname = request.args.get("file", "")
    if key != UPLOAD_KEY:
        return jsonify({"error": "chave invalida"}), 403
    if fname not in LOG_FILES or not os.path.isfile(LOG_FILES[fname]):
        return jsonify({"error": "arquivo invalido"})
    try:
        with open(LOG_FILES[fname], "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()[-2000:]
    except Exception:
        lines = []
    return jsonify({"lines": lines})


# ---------------- catch-all ----------------

@app.route("/", methods=["GET"])
@app.route("/<path:sub>", methods=["GET", "POST", "PUT", "DELETE", "HEAD"])
def catch_all(sub=None):
    path = "/" + (sub or "")
    is_cdn = any(p.match(path) for p in CDN_PATTERNS) or ("noFilter" in path)
    known = path.startswith(KNOWN_PREFIXES)
    log_request(known=known)
    if is_cdn:
        # CORRECAO: repassar imagens do CDN (antes voltava JSON vazio -> imagem cinza)
        q = ("?" + request.query_string.decode()) if request.query_string else ""
        url = UPSTREAMS["cdn"] + path + q
        try:
            resp = rq.request(request.method, url, timeout=25)
            ct = resp.headers.get("Content-Type", "image/png")
            log_line("requests.log", "PROXY CDN %s -> %s (%db)" % (path[:80], resp.status_code, len(resp.content or b"")))
            return Response(resp.content, status=resp.status_code, content_type=ct)
        except Exception as e:
            log_line("requests.log", "PROXY CDN FAIL %s (%s)" % (path[:80], e))
            return Response(b"", status=502, content_type="image/png")
    return jsonify({"data": [], "errors": None})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
