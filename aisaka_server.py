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
    "/v1/enrollments", "/v1/get-enrollments", "/home", "/games", "/img/",
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
    return jsonify({"status": "online", "server": "AISAKA/CAELUS", "version": "0.2.0"})


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
    raw = request.get_data() or b""
    dec = raw
    if request.headers.get("Content-Encoding", "").lower() == "gzip" and raw:
        import gzip as _gz
        try:
            dec = _gz.decompress(raw)
        except Exception:
            dec = raw
    try:
        sample("v1_batch_request", json.loads(dec.decode("utf-8", errors="replace")))
    except Exception:
        try:
            log_line("response_samples.log", "v1_batch_request_raw: %s" % dec[:2000])
        except Exception:
            pass
    resp = proxy_json(UPSTREAMS["thumbnails"] + "/v1/batch", path_override="")
    # CORRECAO: reescrever URLs do CDN pra apontar pro nosso servidor,
    # porque o cliente nao consegue baixar direto do rbxcdn (da imagem cinza)
    try:
        txt = resp.get_data(as_text=True)
        base = server_base()
        for h in ("https://tr.rbxcdn.com/", "https://t0.rbxcdn.com/",
                  "https://t1.rbxcdn.com/", "https://t2.rbxcdn.com/",
                  "https://t3.rbxcdn.com/", "https://t4.rbxcdn.com/",
                  "https://t5.rbxcdn.com/", "https://t6.rbxcdn.com/",
                  "https://t7.rbxcdn.com/"):
            txt = txt.replace(h, base + "/")
        resp = Response(txt, status=resp.status_code, content_type="application/json")
    except Exception:
        pass
    try:
        sample("v1_batch_response", json.loads(resp.get_data(as_text=True)))
    except Exception:
        pass
    return resp


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
import os as _os


def _load_accounts():
    p = _os.path.join(DATA_DIR, "accounts.json")
    try:
        accs = json.load(open(p))
        if isinstance(accs, list) and accs:
            return accs
    except Exception:
        pass
    accs = [{"username": "administrador97", "password": "997169332",
             "userId": 1, "displayName": "administrador97", "birthday": "2011-05-11"}]
    try:
        json.dump(accs, open(p, "w"), indent=2)
    except Exception:
        pass
    return accs


def _find_account(username):
    if not username:
        return None
    u = username.strip().lower()
    for a in _load_accounts():
        if str(a.get("username", "")).lower() == u:
            return a
    return None


def _u_code(u):
    if not u:
        return 1, "Username is required"
    if len(u) < 3 or len(u) > 20:
        return 3, "Username is too short or too long"
    if u.startswith("_") or u.endswith("_"):
        return 4, "Username cannot start or end with an underscore"
    if "__" in u:
        return 5, "Username cannot have more than one underscore in a row"
    if " " in u:
        return 6, "Username cannot contain spaces"
    if not _re.match(r"^[A-Za-z0-9_]+$", u):
        return 7, "Username can only contain letters, numbers and underscores"
    return 0, "Username is valid"


def _p_code(p, u=""):
    if not p:
        return 1, "Password is required"
    if len(p) < 6:
        return 2, "Password is too short"
    if u and p.lower() == u.lower():
        return 3, "Password cannot be the same as the username"
    return 0, "Password is valid"


@app.route("/signup/is-username-valid", methods=["GET"])
def signup_is_username_valid():
    log_request()
    username = request.args.get("username", "")
    code, msg = _u_code(username)
    resp = {"code": code, "message": msg}
    sample("is_username_valid", {"username": username, "resp": resp})
    return jsonify(resp)


@app.route("/signup/is-password-valid", methods=["GET"])
def signup_is_password_valid():
    log_request()
    username = request.args.get("username", "")
    password = request.args.get("password", "")
    code, msg = _p_code(password, username)
    resp = {"IsValid": code == 0, "ErrorMessage": msg, "ErrorCode": code,
            "code": code, "message": msg}
    sample("is_password_valid", {"username": username, "resp": resp})
    return jsonify(resp)


@app.route("/v2/usernames/validate", methods=["GET", "POST"])
def v2_usernames_validate():
    log_request()
    username = request.args.get("username") or request.args.get("request.username") or ""
    code, msg = _u_code(username)
    return jsonify({"code": code, "message": msg})


@app.route("/v1/validators/username", methods=["GET"])
def v1_validators_username():
    log_request()
    username = request.args.get("username", "")
    return jsonify({"didGenerateNewUsername": False, "suggestedUsername": username})


@app.route("/v2/passwords/validate", methods=["GET"])
def v2_passwords_validate():
    log_request()
    username = request.args.get("username", "")
    password = request.args.get("password", "")
    code, msg = _p_code(password, username)
    return jsonify({"code": code, "message": msg})


def _do_login(username, password):
    try:
        open(_os.path.join(DATA_DIR, "last_login.txt"), "w").write(username or "")
    except Exception:
        pass
    acc = _find_account(username)
    r = jsonify({"user": {"id": (acc or {}).get("userId", 1),
                          "name": (acc or {}).get("username", "Player1"),
                          "displayName": (acc or {}).get("displayName", "Player1")}})
    r.headers["Set-Cookie"] = ".ROBLOSECURITY=EMULATED_SESSION_TOKEN_PLAYER1; Path=/; HttpOnly"
    return r


@app.route("/v2/login", methods=["POST"])
def v2_login():
    log_request()
    body = request.get_json(silent=True) or {}
    return _do_login(body.get("cvalue", ""), body.get("password", ""))


@app.route("/v1/login", methods=["POST"])
def v1_login():
    log_request()
    body = request.get_json(silent=True) or {}
    return _do_login(body.get("cvalue", ""), body.get("password", ""))


@app.route("/v2/signup", methods=["POST"])
def v2_signup():
    log_request()
    body = request.get_json(silent=True) or {}
    username = body.get("username", "Player1")
    password = body.get("password", "")
    code, msg = _u_code(username)
    if code != 0:
        return jsonify({"errors": [{"code": 5, "message": msg}]}), 403
    code, msg = _p_code(password, username)
    if code != 0:
        return jsonify({"errors": [{"code": 7, "message": msg}]}), 403
    accs = _load_accounts()
    p = _os.path.join(DATA_DIR, "accounts.json")
    if not _find_account(username):
        accs.append({"username": username, "password": password, "userId": len(accs) + 1,
                     "displayName": username, "birthday": body.get("birthday", "")})
    try:
        json.dump(accs, open(p, "w"), indent=2)
    except Exception:
        pass
    resp = {"userId": 1, "starterPlaceId": 1818}
    sample("v2_signup", {"username": username, "resp": resp})
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


# ---------------- enrollments (AB tests) ----------------
# CORRECAO: o cliente decide qual versao da tela usar (Lua nativo vs WebView)
# lendo o status desses testes. Antes devolviamos data vazio e a home
# ficava cinza (implementacao Lua aponta pra subdominios que nao existem).
# Devolver "NotEnrolled" faz o app usar as telas WebView (HTML do nosso servidor).
@app.route("/v1/enrollments", methods=["POST"])
@app.route("/v1/get-enrollments", methods=["POST"])
def v1_enrollments():
    log_request()
    body = request.get_json(silent=True)
    items = []
    if isinstance(body, list):
        for e in body:
            if isinstance(e, dict) and e.get("ExperimentName"):
                items.append({
                    "ExperimentName": e.get("ExperimentName"),
                    "SubjectType": e.get("SubjectType", "BrowserTracker"),
                    "SubjectTargetId": e.get("SubjectTargetId", 0),
                    "Status": "NotEnrolled",
                })
    sample("enrollments", {"pedidos": len(items)})
    return jsonify({"data": items, "errors": None})


# ---------------- telas WebView (Home / Jogos) ----------------
GAMES = [
    ("Adopt Me!", 920587237),
    ("Brookhaven RP", 4924922222),
    ("Tower of Hell", 1962086868),
    ("Blox Fruits", 275038597),
    ("Doors", 6516141729),
    ("Jailbreak", 606849621),
    ("Murder Mystery 2", 142823291),
    ("Natural Disaster Survival", 189707),
    ("Arsenal", 286090429),
    ("Work at a Pizza Place", 192800),
]

ICON_CACHE = {}
PLACEHOLDER_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
)


def _page(title, body, active="home"):
    nav = (
        '<div class="nav"><a class="logo" href="/home">CAELUS</a>'
        '<div class="tabs"><a href="/home" class="%s">Início</a>'
        '<a href="/games" class="%s">Jogos</a></div></div>'
        % ("on" if active == "home" else "", "on" if active == "games" else "")
    )
    return """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>%s</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{background:#191b1f;color:#e8e8e8;font-family:system-ui,-apple-system,Roboto,sans-serif;padding-bottom:32px}
.nav{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;background:#232529;position:sticky;top:0}
.logo{color:#fff;font-weight:800;font-size:18px;text-decoration:none;letter-spacing:1px}
.tabs a{color:#9aa0a6;text-decoration:none;font-size:14px;font-weight:600;margin-left:14px;padding:6px 2px}
.tabs a.on{color:#fff;border-bottom:2px solid #f5c518}
h2{font-size:16px;margin:18px 16px 10px;color:#fff}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;padding:0 16px}
.card{background:#232529;border-radius:12px;overflow:hidden;text-decoration:none;display:block}
.card img{width:100%%;aspect-ratio:1;object-fit:cover;background:#2e3138;display:block}
.card .nm{padding:9px 10px;font-size:13px;font-weight:600;color:#e8e8e8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.big{display:flex;gap:14px;padding:16px}
.big img{width:120px;height:120px;border-radius:12px;background:#2e3138}
.big .nm{font-size:20px;font-weight:800;color:#fff;margin-bottom:6px}
.big .by{font-size:12px;color:#9aa0a6}
.play{display:block;margin:18px 16px;padding:14px;background:#00b06f;color:#fff;text-align:center;
border-radius:10px;font-size:16px;font-weight:800;text-decoration:none}
.desc{padding:0 16px;font-size:13px;color:#b9bdc4;line-height:1.5;white-space:pre-wrap}
.hello{padding:14px 16px 0;font-size:13px;color:#9aa0a6}
</style></head><body>%s<div class="hello">Bem-vindo de volta!</div>%s</body></html>""" % (title, nav, body)


def _greet():
    try:
        u = open(_os.path.join(DATA_DIR, "last_login.txt")).read().strip()
        if u:
            return u
    except Exception:
        pass
    return ""


@app.route("/home", methods=["GET"])
@app.route("/home/", methods=["GET"])
def page_home():
    log_request()
    cards = "".join(
        '<a class="card" href="/games/%d"><img src="/img/gameicon/%d" alt=""><div class="nm">%s</div></a>'
        % (pid, pid, name) for name, pid in GAMES
    )
    body = "<h2>Populares</h2><div class=grid>%s</div>" % cards
    greet = _greet()
    if greet:
        body = body.replace("Bem-vindo de volta!", "Ei, %s!" % greet)
    return _page("Início", body, "home")


@app.route("/games", methods=["GET"])
@app.route("/games/", methods=["GET"])
def page_games():
    log_request()
    cards = "".join(
        '<a class="card" href="/games/%d"><img src="/img/gameicon/%d" alt=""><div class="nm">%s</div></a>'
        % (pid, pid, name) for name, pid in GAMES
    )
    body = "<h2>Todos os jogos</h2><div class=grid>%s</div>" % cards
    return _page("Jogos", body, "games")


@app.route("/games/<int:place_id>", methods=["GET"])
def page_game_details(place_id):
    log_request()
    name = "Jogo %d" % place_id
    for gname, gpid in GAMES:
        if gpid == place_id:
            name = gname
            break
    desc = "Bora jogar! Servidor Caelus/Roblox."
    by = "Roblox"
    body = (
        '<div class="big"><img src="/img/gameicon/%d"><div><div class="nm">%s</div>'
        '<div class="by">%s</div></div></div>'
        '<a class="play" href="robloxmobile://experiences/start?placeId=%d">▶ Jogar</a>'
        '<div class="desc">%s</div>'
        % (place_id, name, by, place_id, desc or "Sem descrição.")
    )
    return _page(name, body, "games")


@app.route("/img/gameicon/<int:place_id>", methods=["GET"])
def img_gameicon(place_id):
    if place_id in ICON_CACHE:
        return Response(ICON_CACHE[place_id], content_type="image/png")
    try:
        r = rq.get(
            "https://thumbnails.roblox.com/v1/places/gameicons?placeIds=%d&size=512x512&format=Png&isCircular=false"
            % place_id, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        d0 = ((r.json().get("data") or [{}])[0])
        url = d0.get("imageUrl") or d0.get("targetUrl")
        if url:
            img = rq.get(url, timeout=20).content
            if len(ICON_CACHE) < 200:
                ICON_CACHE[place_id] = img
            return Response(img, content_type="image/png")
    except Exception as e:
        log_line("requests.log", "IMG gameicon %d FAIL (%s)" % (place_id, e))
    return Response(PLACEHOLDER_PNG, content_type="image/png")


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
