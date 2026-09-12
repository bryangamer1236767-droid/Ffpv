"""
================================================================
 FREE FIRE PRIVATE SERVER v2.1.0 - STATELESS TOKENS
================================================================
 Servidor HTTP (Flask) + Game Server (TCP Socket)
 Tokens sem estado (stateless) - funciona com múltiplos workers

 COMO USAR:
 1. pip install flask gunicorn
 2. python3 freefire_server_full.py
 3. Deploy: gunicorn freefire_server_full:app --workers 1
================================================================
"""

import os
import json
import time
import uuid
import hashlib
import secrets
import base64
import hmac
import socket
import struct
import threading
from flask import Flask, request, jsonify

app = Flask(__name__)

import logging, sys
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
_reqlog = logging.getLogger("reqlog")

@app.before_request
def _log_every_request():
    try:
        body = request.get_data(as_text=True)
        if len(body) > 2500:
            body = body[:2500] + "...(truncado)"
        _reqlog.info("=== %s %s | args=%s | body=%s | ua=%s",
                      request.method, request.path, dict(request.args), body,
                      request.headers.get("User-Agent", "")[:80])
    except Exception as e:
        _reqlog.info("erro ao logar request: %s", e)

@app.errorhandler(404)
def _log_404(e):
    _reqlog.info("!!! 404 NAO MAPEADO: %s %s", request.method, request.path)
    return jsonify({"status": "error", "code": 404, "message": "Application not found"}), 404

# ==================================================================
# CONFIGURAÇÃO
# ==================================================================
APP_ID = "com.dts.freefireth"
APP_KEY = "freefire"
CLIENT_VERSION = "1.71.0"
HTTP_PORT = int(os.environ.get("PORT", 5000))
GAME_IP = os.environ.get("GAME_SERVER_IP", "127.0.0.1")
GAME_PORT = int(os.environ.get("GAME_SERVER_PORT", 2205))

# Chave secreta para assinar tokens (HMAC)
SECRET_KEY = os.environ.get("SECRET_KEY", "freefire_private_server_2024_hmac_key_fixed_v2")

# ==================================================================
# TOKENS STATELESS (não precisam de memória entre workers)
# Formato: base64(json_payload).base64(hmac_signature)
# ==================================================================
def create_token(open_id, nickname, token_type="guest"):
    payload = {
        "open_id": open_id,
        "nickname": nickname,
        "type": token_type,
        "created": now(),
        "expire": now() + 86400 * 30
    }
    payload_json = json.dumps(payload, separators=(',', ':'))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"

def verify_token(token):
    if not token or '.' not in token:
        return None
    try:
        parts = token.split('.')
        if len(parts) != 2:
            return None
        payload_b64, sig = parts
        expected_sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("expire", 0) < now():
            return None
        return payload
    except:
        return None

def create_refresh_token(open_id, nickname, token_type="guest"):
    payload = {
        "open_id": open_id,
        "nickname": nickname,
        "type": token_type,
        "rt": True,
        "expire": now() + 86400 * 60
    }
    payload_json = json.dumps(payload, separators=(',', ':'))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"

def verify_refresh_token(token):
    d = verify_token(token)
    if d and d.get("rt"):
        return d
    return None

# ==================================================================
# DADOS DE JOGADOR (gerados deterministicamente a partir do open_id)
# ==================================================================
PLAYERS = {}  # dados dos jogadores (persistidos em players.json)
ROOMS = {}
SOCKETS = {}

import json as _json, os as _os
_DATA_DIR = _os.environ.get("DATA_DIR", "/data") if _os.path.isdir(_os.environ.get("DATA_DIR", "/data")) else _os.path.dirname(_os.path.abspath(__file__))
_DB_FILE = _os.environ.get("PLAYERS_DB", _os.path.join(_DATA_DIR, "players.json"))

def save_players():
    try:
        with open(_DB_FILE, "w") as f:
            _json.dump(PLAYERS, f)
    except Exception:
        pass

def load_players():
    global PLAYERS
    try:
        with open(_DB_FILE) as f:
            PLAYERS = _json.load(f)
    except Exception:
        PLAYERS = {}

load_players()


# --- Helper: le parametro de form, JSON body ou query string ---
def param(name, default=''):
    v = request.form.get(name)
    if v: return v
    if request.is_json:
        try:
            v = (request.get_json(silent=True) or {}).get(name)
            if v is not None: return v
        except Exception:
            pass
    v = request.args.get(name)
    return v if v is not None else default

def gen_open_id():
    return str(int(uuid.uuid4().hex, 16))[:18].zfill(18)

def now():
    return int(time.time())


# ===== SISTEMA DE CONTAS (usuario e senha) =====
ACCOUNTS_FILE = os.path.join(_DATA_DIR, "accounts.json")

def load_accounts():
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_accounts(accs):
    try:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(accs, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def hash_password(pw, salt=None):
    if salt is None:
        salt = secrets.token_hex(8)
    h = hashlib.sha256((salt + ":" + pw).encode()).hexdigest()
    return salt, h

def account_open_id(username):
    # ID totalmente numerico (formato que o jogo espera de uma conta real)
    h = hashlib.sha256(("ffacc:" + username.lower()).encode()).hexdigest()
    num = str(int(h, 16))[:17]
    return num.zfill(17)

def make_auth_code(open_id, nickname):
    # Codigo de autorizacao assinado (funciona com varios workers, sem estado)
    payload = base64.urlsafe_b64encode(json.dumps({
        "open_id": open_id, "nickname": nickname, "t": now(), "kind": "authcode"
    }).encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return payload + "." + sig

def read_auth_code(code):
    try:
        payload, sig = code.rsplit(".", 1)
        expected = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        d = json.loads(base64.urlsafe_b64decode(payload.encode()))
        if d.get("kind") != "authcode" or now() - d.get("t", 0) > 600:
            return None
        return d
    except Exception:
        return None

def account_token_response(open_id, nickname):
    # token IDENTICO ao do fluxo guest (que o jogo aceita): type=guest.
    # O C# do jogo le o payload do token; "oauth" era rejeitado.
    at = create_token(open_id, nickname, "guest")
    rt = create_refresh_token(open_id, nickname, "guest")
    get_player(open_id, nickname)
    return {
        "open_id": open_id,
        "platform": "guest",
        "access_token": at,
        "refresh_token": rt,
        "expiry_time": now() + 86400 * 30,
        "expires_in": 86400 * 30,
        "token_type": "Bearer"
    }

GUEST_IPS_FILE = "/data/guest_ips.json"
DEVICE_GUEST_FILE = "/data/device_guest.json"
GUEST_IPS = {}

def load_guest_ips():
    global GUEST_IPS
    try:
        with open(GUEST_IPS_FILE) as f:
            GUEST_IPS = json.load(f)
    except Exception:
        GUEST_IPS = {}

def save_guest_ips():
    try:
        os.makedirs("/data", exist_ok=True)
        with open(GUEST_IPS_FILE, "w") as f:
            json.dump(GUEST_IPS, f)
    except Exception:
        pass

def note_guest_ip(open_id):
    # associa o IP da requisicao ao guest que o dispositivo registrou
    try:
        ip = (request.headers.get("X-Forwarded-For", "") or request.remote_addr or "?").split(",")[0].strip()
        GUEST_IPS[ip] = {"open_id": open_id, "t": now()}
        save_guest_ips()
    except Exception:
        pass

def guest_open_id_for_request():
    # 1) guest registrado pelo PhoneHome do PROPRIO aparelho (infalivel);
    #    2) por IP; 3) ultimo guest visto; 4) jogador Guest* salvo
    try:
        try:
            with open(DEVICE_GUEST_FILE) as f:
                dg = json.load(f)
            if dg.get("open_id") and now() - dg.get("t", 0) < 86400 * 60:
                return dg["open_id"]
        except Exception:
            pass
        ip = request.remote_addr or "?"
        g = GUEST_IPS.get(ip)
        if g and now() - g.get("t", 0) < 86400 * 30:
            return g["open_id"]
        best = None
        best_t = -1
        for v in GUEST_IPS.values():
            if v.get("t", 0) > best_t:
                best_t = v["t"]
                best = v.get("open_id")
        if best:
            return best
        # mapa vazio (ex: dados de antes do deploy): guest do aparelho do dono
        # (criado pelo jogo no primeiro boot: Guest30238, id deterministico do uid)
        DEVICE_GUEST_OPEN_ID = "278017725955930413"
        if DEVICE_GUEST_OPEN_ID in PLAYERS:
            return DEVICE_GUEST_OPEN_ID
        for oid, p in PLAYERS.items():
            nick = (p.get("nickname") or "")
            if nick.startswith("Guest"):
                return oid
        return None
    except Exception:
        return None

def create_guest_account(custom_nick=None, seed=None):
    # ID deterministico: mesmo dispositivo (uid) = mesma conta pra sempre
    if seed:
        open_id = str(int(hashlib.sha256(str(seed).encode()).hexdigest(), 16))[:18].zfill(18)
    else:
        open_id = gen_open_id()
    nickname = custom_nick or f'Guest{secrets.randbelow(99999)}'
    at = create_token(open_id, nickname, "guest")
    rt = create_refresh_token(open_id, nickname, "guest")
    get_player(open_id, nickname)
    return {
        "open_id": open_id,
        "platform": "guest",
        "access_token": at,
        "refresh_token": rt,
        "expiry_time": now() + 86400 * 30,
        "expires_in": 86400 * 30,
        "token_type": "Bearer"
    }

def get_player(open_id, nickname="Player"):
    if open_id not in PLAYERS:
        PLAYERS[open_id] = {
            "nickname": nickname,
            "level": 80,
            "exp": 999999,
            "diamond": 99999,
            "gold": 99999,
            "coins": 99999,
            "tokens": 9999,
            "rank": 10,
            "region": "BR",
            "avatar": 1,
            "vip": 9,
            "skins": list(range(1, 500)),
            "characters": list(range(1, 50)),
            "weapons": list(range(1, 100)),
            "pets": list(range(1, 30)),
            "items": [],
            "created": now()
        }
        save_players()
    return PLAYERS[open_id]


load_players()
load_guest_ips()

# codigos oauth capturados do servidor RZIM (login ponte)
RZIM_CODES = {}
RZIM_LAST = {}
RZIM_SESSIONS = {}

# ==================================================================
# ============== HTTP API SERVER (FLASK) ==============
# ==================================================================

# --- FACEBOOK GRAPH API STUB (app settings, chamado pelo FacebookSdk) ---
# Cobre QUALQUER versao da Graph API (v2.5, v9.0, v12.0, etc) - o SDK negocia
# a versao dinamicamente e nao podemos travar em uma so, senao o app trava
# tentando repetidamente e acaba fechando sozinho.
FB_APP_ID = "2036793259884297"

@app.route(f'/<ver>/{FB_APP_ID}', methods=['GET'])
@app.route(f'/<path:_prefix>/<ver>/{FB_APP_ID}', methods=['GET'])
def fb_graph_app_settings(ver, _prefix=None):
    print(f"[FB_GRAPH] ver={ver} app_id={FB_APP_ID} args={dict(request.args)}", flush=True)
    return jsonify({
        "android_dialog_configs": {},
        "android_sdk_error_categories": [],
        "gdpv4_nux_content": {},
        "gdpv4_nux_enabled": False,
        "id": FB_APP_ID,
        "supports_implicit_sdk_logging": True
    })

@app.route(f'/<ver>/{FB_APP_ID}/mobile_sdk_gk', methods=['GET'])
@app.route(f'/<path:_prefix>/<ver>/{FB_APP_ID}/mobile_sdk_gk', methods=['GET'])
def fb_graph_gatekeepers(ver, _prefix=None):
    print(f"[FB_GRAPH_GK] ver={ver} args={dict(request.args)}", flush=True)
    return jsonify({
        "data": [],
        "id": FB_APP_ID
    })

@app.route(f'/<ver>/{FB_APP_ID}/activities', methods=['POST'])
@app.route(f'/<path:_prefix>/<ver>/{FB_APP_ID}/activities', methods=['POST'])
def fb_graph_activities(ver, _prefix=None):
    print(f"[FB_GRAPH_ACTIVITIES] ver={ver} (analytics - ignorado)", flush=True)
    return jsonify({"success": True})

# --- INFO DA APP ---
@app.route('/app/info/get', methods=['GET'])
def app_info():
    return jsonify({
        "client_log": False,
        "status": 0,
        "maintenance": False,
        "version": CLIENT_VERSION,
        "game_server": f"{GAME_IP}:{GAME_PORT}",
        "update_url": "",
        "notice": {"title": "Servidor Privado", "content": "Bem-vindo ao servidor!", "show": True}
    })

@app.route('/app/feedback', methods=['POST', 'GET'])
def feedback():
    return jsonify({"success": True})

# --- GUEST REGISTER ---
@app.route('/oauth/guest/register', methods=['POST'])
def guest_register():
    nickname = param('nickname') or None
    seed = param('uid') or param('device_id') or None
    resp = create_guest_account(nickname, seed)
    note_guest_ip(resp["open_id"])
    return jsonify(resp)

# --- GUEST TOKEN GRANT ---
@app.route('/oauth/guest/token/grant', methods=['POST', 'GET'])
def guest_grant():
    # O jogo envia: uid, password, response_type, client_type, client_id, client_secret
    # NAO exige app_id (o SDK nunca envia nessa rota)
    seed = param('uid') or param('client_id') or None
    resp = create_guest_account(None, seed)
    note_guest_ip(resp["open_id"])
    return jsonify(resp)

# --- OAUTH TOKEN ---
@app.route('/oauth/token', methods=['POST'])
def oauth_token():
    gt = request.form.get('grant_type', '')
    rt = request.form.get('refresh_token', '')

    if gt == 'authorization_code':
        code = param('code')
        if code and code.startswith("rzim_"):
            info = RZIM_CODES.get(code)
            _reqlog.info("[RZIM-EXCHANGE] code=%s info=%s", code[:12], info)
            if info:
                sess = RZIM_SESSIONS.get(info.get("username", ""))
                if sess:
                    _reqlog.info("[RZIM-EXCHANGE] devolvendo sessao deles: open_id=%s", sess["open_id"])
                    return jsonify({
                        "open_id": sess["open_id"],
                        "access_token": sess["access_token"],
                        "refresh_token": sess.get("refresh_token") or create_refresh_token(sess["open_id"], info.get("username", "rzim"), "oauth"),
                        "expires_in": 86400 * 30,
                        "token_type": "Bearer",
                    })
            return jsonify({"code": 2017, "error": "invalid_grant"})
        d = read_auth_code(code) if code else None
        if d:
            return jsonify(account_token_response(d["open_id"], d["nickname"]))
        return jsonify(create_guest_account())

    elif gt == 'refresh_token':
        d = verify_refresh_token(rt)
        if d:
            at = create_token(d["open_id"], d["nickname"], d.get("type", "oauth"))
            nr = create_refresh_token(d["open_id"], d["nickname"], d.get("type", "oauth"))
            return jsonify({
                "access_token": at,
                "refresh_token": nr,
                "expires_in": 86400 * 30,
                "token_type": "Bearer"
            })

    return jsonify({"code": 2017, "error": "invalid_grant"})


# --- SITE DE LOGIN (abre dentro do jogo, botao do Facebook) ---
LOGIN_PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>Kryno FF - Login</title>
<script src="https://js.hcaptcha.com/1/api.js" async defer></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0d0b14; color:#fff; font-family:system-ui,-apple-system,sans-serif;
       min-height:100vh; display:flex; align-items:center; justify-content:center; padding:16px; }
.card { width:100%; max-width:360px; }
.logo { text-align:center; margin-bottom:20px; }
.logo h1 { font-size:30px; font-weight:800; letter-spacing:1px;
           color:#fff; text-shadow:0 0 18px #a855f7, 0 0 40px #7c3aed; }
.logo p { color:#8b8598; font-size:12px; margin-top:4px; }
.tabs { display:flex; gap:8px; margin-bottom:16px; }
.tab { flex:1; padding:11px; text-align:center; border-radius:12px; font-size:14px; font-weight:700;
       background:#171326; color:#8b8598; border:1px solid #251f38; cursor:pointer; transition:.2s; }
.tab.active { background:linear-gradient(135deg,#7c3aed,#a855f7); color:#fff;
              box-shadow:0 0 16px rgba(168,85,247,.45); border-color:#a855f7; }
form { display:none; }
form.active { display:block; }
label { display:block; font-size:12px; color:#b0a8c4; margin:12px 0 5px; font-weight:600; }
input { width:100%; padding:13px 14px; border-radius:12px; border:1px solid #2a2340;
        background:#171326; color:#fff; font-size:15px; outline:none; }
input:focus { border-color:#a855f7; box-shadow:0 0 12px rgba(168,85,247,.35); }
.btn { width:100%; margin-top:18px; padding:14px; border:none; border-radius:12px; font-size:15px;
       font-weight:800; color:#fff; background:linear-gradient(135deg,#7c3aed,#c084fc);
       box-shadow:0 0 18px rgba(168,85,247,.5); cursor:pointer; }
.btn:active { transform:scale(.98); }
.err { margin-top:12px; padding:10px 12px; border-radius:10px; background:#3b1220;
       color:#fda4af; font-size:13px; border:1px solid #7f1d2e; display:none; }
.foot { margin-top:16px; text-align:center; color:#5c5570; font-size:11px; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <h1>KRYNO FF</h1>
    <p>Bem-vindo ao servidor privado desenvolvido por Bryan</p>
  </div>
  <div class="tabs">
    <div class="tab" id="tab-login" onclick="showTab('login')">Entrar</div>
    <div class="tab active" id="tab-reg" onclick="showTab('reg')">Criar conta</div>
  </div>
  <form id="f-reg" class="active" method="post" action="/oauth/login/do">
    <input type="hidden" name="action" value="register">
    <input type="hidden" name="redirect_uri" value="__REDIR__">
    <input type="hidden" name="client_id" value="__CID__">
    <label>Usuário</label>
    <input name="username" maxlength="16" placeholder="seu_usuario" required>
    <label>Senha</label>
    <input type="password" name="password" maxlength="32" placeholder="••••••••" required>
    <label>Nick no jogo (opcional)</label>
    <input name="nickname" maxlength="16" placeholder="Como vai aparecer no jogo">
    <div class="err" id="err">__MSG__</div>
    <button class="btn" type="submit">CRIAR CONTA E ENTRAR</button>
  </form>
  <form id="f-login" method="post" action="/oauth/login/do">
    <input type="hidden" name="action" value="login">
    <input type="hidden" name="redirect_uri" value="__REDIR__">
    <input type="hidden" name="client_id" value="__CID__">
    <label>Usuário</label>
    <input name="username" maxlength="16" placeholder="seu_usuario" required>
    <label>Senha</label>
    <input type="password" name="password" maxlength="32" placeholder="••••••••" required>
    <div class="err" id="err2">__MSG__</div>
    <div class="h-captcha" data-sitekey="94bea0ac-d6dc-45ec-adce-d2fe24f06076" data-theme="dark"></div>
    <button class="btn" type="submit">ENTRAR NO JOGO</button>
  </form>
  <div class="foot">Servidor Privado por Bryan - Online - v1.71</div>
</div>
<script>
function showTab(t){
  document.getElementById('f-reg').className = t==='reg' ? 'active' : '';
  document.getElementById('f-login').className = t==='login' ? 'active' : '';
  document.getElementById('tab-reg').className = 'tab' + (t==='reg' ? ' active' : '');
  document.getElementById('tab-login').className = 'tab' + (t==='login' ? ' active' : '');
}
if ('__MSG__' !== '') {
  document.getElementById('err').style.display='block';
  document.getElementById('err2').style.display='block';
  showTab('__TAB__');
}
</script>
</body>
</html>"""

def redirect_js(rr, code):
    url = rr + (("&" if "?" in rr else "?") + "code=" + code)
    return ('<!DOCTYPE html><html><head><meta charset="UTF-8"></head>'
            '<body style="background:#12122b;color:#e8e0ff;font-family:sans-serif;text-align:center;padding-top:40px">'
            '<h3>Conectado! Voltando ao jogo...</h3>'
            '<p><a style="color:#a78bfa" href="' + url + '">Toque aqui se nao voltar sozinho</a></p>'
            '<script>setTimeout(function(){location.href=' + json.dumps(url) + ';},400);</script>'
            '</body></html>'), 200

def render_login_page(redirect_uri="", client_id="", msg="", tab="reg"):
    redir = redirect_uri or ("gop" + APP_ID.replace(".", "").replace("com", "", 1) + "://auth/")
    html = LOGIN_PAGE.replace("__REDIR__", redir).replace("__CID__", client_id or "100067")
    html = html.replace("__MSG__", msg).replace("__TAB__", tab)
    return html

def make_auto_cookie(username):
    sig = hmac.new(SECRET_KEY.encode(), ("auto:" + username).encode(), hashlib.sha256).hexdigest()[:32]
    return username + "." + sig

def read_auto_cookie(val):
    try:
        if not val or "." not in val:
            return None
        username, sig = val.rsplit(".", 1)
        expected = hmac.new(SECRET_KEY.encode(), ("auto:" + username).encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        return username
    except Exception:
        return None

@app.route('/oauth/login', methods=['GET', 'POST'])
def oauth_login():
    redir = param('redirect_uri')
    cid = param('client_id')
    # AUTO-LOGIN: se o aparelho ja logou antes (cookie assinado), entra direto sem digitar nada
    try:
        username = read_auto_cookie(request.cookies.get('ffauto'))
        if username and param('reauth') != '1':
            acc = load_accounts().get(username)
            if acc:
                code = make_auth_code(acc['open_id'], acc['nickname'])
                r = redir or ("gop" + APP_ID.replace(".", "").replace("com", "", 1) + "://auth/")
                sep = "&" if "?" in r else "?"
                _reqlog.info("[AUTO-LOGIN] cookie valido -> entrada direta da conta %s", username)
                from flask import redirect as _r2
                return _r2(r + sep + "code=" + code, code=302)
    except Exception as e:
        _reqlog.info("[AUTO-LOGIN] falhou: %s", e)
    return render_login_page(redir, cid)

@app.route('/oauth/login/do', methods=['POST'])
def oauth_login_do():
    action = param('action')
    username = param('username').strip().lower()
    password = param('password')
    nickname = param('nickname').strip()
    redir = param('redirect_uri')
    cid = param('client_id')
    accs = load_accounts()

    def fail(msg, tab):
        return render_login_page(redir, cid, msg, tab), 400

    if not username or not password:
        return fail("Preencha usuário e senha.", "reg" if action == "register" else "login")
    if not username.isalnum() and not all(ch.isalnum() or ch in "_-." for ch in username):
        return fail("Usuário só pode ter letras, números, _ - e .", "reg" if action == "register" else "login")

    # --- PONTE RZIM: login com conta do servidor do Barbosa ---
    hcap = (request.form.get('h-captcha-response') or '').strip()
    if action == "login" and hcap:
        try:
            import urllib.request as _ur, urllib.parse as _up, urllib.error as _ue
            data = _up.urlencode({
                "action": "login", "username": username, "password": password,
                "h-captcha-response": hcap, "client_id": cid or "100067",
                "response_type": "code", "display": "embedded", "locale": "pt_BR",
                "redirect_uri": redir or "gop100067://auth/",
            }).encode()
            req = _ur.Request("https://login.barbosasmobile.com/oauth/login", data=data)
            req.add_header("User-Agent", "Mozilla/5.0 (Linux; Android 15) Chrome/124 Mobile Safari/537.36")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            try:
                rsp = _ur.urlopen(req, timeout=15)
                body, status, loc = rsp.read().decode(errors="replace"), rsp.status, rsp.headers.get("Location", "")
            except _ue.HTTPError as _e:
                body, status, loc = _e.read().decode(errors="replace"), _e.code, _e.headers.get("Location", "")
            RZIM_LAST["status"] = status
            RZIM_LAST["loc"] = loc
            RZIM_LAST["body"] = body
            _reqlog.info("[RZIM] status=%s loc=%s body=%s", status, loc[:300], body[:3000])
            import re as _re
            m = _re.search(r'code=([A-Za-z0-9_\-\.\+\/=]{8,})', body + "|" + (loc or ""))
            if status in (200, 301, 302, 303) and m:
                his_code = m.group(1)
                import uuid as _uu
                our_code = "rzim_" + _uu.uuid4().hex[:20]
                RZIM_CODES[our_code] = {"his_code": his_code, "username": username}
                _reqlog.info("[RZIM] CODIGO CAPTURADO! our=%s his=%s...", our_code, his_code[:14])
                rr = redir or "gop100067://auth/"
                return redirect_js(rr, our_code)
            if "Login OK" in body:
                _reqlog.info("[RZIM] LOGIN OK - trocando code por tokens na hora...")
                import base64 as _b64, json as _js
                msr = _re.search(r'signed_request=([A-Za-z0-9_\-\.]+)', body)
                if msr:
                    try:
                        _sig, _pl = msr.group(1).split('.', 1)
                        _pl += "=" * (-len(_pl) % 4)
                        _sr = _js.loads(_b64.urlsafe_b64decode(_pl))
                        _hcode = _sr.get("code", "")
                        _huid = _sr.get("user_id", "")
                        _reqlog.info("[RZIM] user_id=%s code=%s...", _huid, _hcode[:16])
                        # tenta trocar no auth deles com combinacoes de parametros
                        import urllib.request as _ur2, urllib.parse as _up2
                        _toks = None
                        _secrets = ("3b7ace061a9b40ba84123ab8e4c56cd4", "B3EEABB8EE11C2BE770B684D95219ECB",
                                    "8cb9f10deded1953a1b2343835345e2b", "1a25a10c8a24acdbb07bd483eaa84718", "")
                        _combos = []
                        for _cid in ("dtsfreefireth", "100067"):
                            for _sec in _secrets:
                                _combos.append((_cid, _sec, "gopdtsfreefireth://auth/"))
                        for _cid in ("dtsfreefireth", "100067"):
                            for _sec in _secrets:
                                _combos.append((_cid, _sec, "gop100067://auth/"))
                        import urllib.request as _ur2, urllib.parse as _up2
                        for _cid, _sec, _rd in _combos:
                            try:
                                _d2 = _up2.urlencode({
                                    "grant_type": "authorization_code", "facebook_access_token": _hcode,
                                    "client_id": _cid, "redirect_uri": _rd, "source": "2",
                                    "client_secret": _sec}).encode()
                                _rq2 = _ur2.Request("https://connect.barbosasmobile.com/oauth/token/facebook/exchange", data=_d2)
                                _rq2.add_header("User-Agent", "Mozilla/5.0 (Linux; Android 15) Chrome/124 Mobile Safari/537.36")
                                _rq2.add_header("Content-Type", "application/x-www-form-urlencoded")
                                _rsp2 = _ur2.urlopen(_rq2, timeout=10)
                                _b2 = _rsp2.read().decode(errors="replace")
                                _reqlog.info("[RZIM-FBX] cid=%s sec=%s rd=%s -> %s", _cid, _sec[:8], _rd, _b2[:400])
                                if ("access_token" in _b2) or ("open_id" in _b2):
                                    try:
                                        _toks = _js.loads(_b2)
                                    except Exception:
                                        _toks = {"raw_body": _b2}
                                    break
                            except Exception as _e2:
                                _reqlog.info("[RZIM-FBX] erro cid=%s sec=%s: %s", _cid, _sec[:8], _e2)
                        # fallback: endpoint garena antigo
                        if not _toks:
                            for _cid, _sec in (("100067", "3b7ace061a9b40ba84123ab8e4c56cd4"),
                                               ("100067", "B3EEABB8EE11C2BE770B684D95219ECB"),
                                               ("dtsfreefireth", "3b7ace061a9b40ba84123ab8e4c56cd4")):
                                try:
                                    _d2 = _up2.urlencode({
                                        "grant_type": "authorization_code", "code": _hcode,
                                        "client_id": _cid, "redirect_uri": "gopdtsfreefireth://auth/",
                                        "client_secret": _sec}).encode()
                                    _rq2 = _ur2.Request("https://connect.barbosasmobile.com/oauth/token", data=_d2)
                                    _rq2.add_header("User-Agent", "Mozilla/5.0 (Linux; Android 15) Chrome/124 Mobile Safari/537.36")
                                    _rq2.add_header("Content-Type", "application/x-www-form-urlencoded")
                                    _rsp2 = _ur2.urlopen(_rq2, timeout=10)
                                    _b2 = _rsp2.read().decode(errors="replace")
                                    _reqlog.info("[RZIM-TOK] cid=%s sec=%s -> %s", _cid, _sec[:8], _b2[:300])
                                    if ("access_token" in _b2) or ("open_id" in _b2):
                                        try:
                                            _toks = _js.loads(_b2)
                                        except Exception:
                                            _toks = {"raw_body": _b2}
                                        break
                                except Exception as _e2:
                                    _reqlog.info("[RZIM-TOK] erro cid=%s: %s", _cid, _e2)

                        if _toks:
                            RZIM_SESSIONS[username] = {"open_id": str(_toks.get("open_id") or _huid),
                                                       "access_token": _toks.get("access_token", ""),
                                                       "refresh_token": _toks.get("refresh_token", ""),
                                                       "raw": _toks, "ts": now()}
                            _reqlog.info("[RZIM] SESSAO DO SERVIDOR DELES OBTIDA! open_id=%s", RZIM_SESSIONS[username]["open_id"])
                            import uuid as _uu
                            our_code = "rzim_" + _uu.uuid4().hex[:20]
                            RZIM_CODES[our_code] = {"username": username}
                            rr = redir or "gop100067://auth/"
                            return redirect_js(rr, our_code)
                        # OPENID GAMBLE: devolver sessao com o open_id REAL deles (user_id do JWT)
                        import uuid as _uu
                        RZIM_SESSIONS[username] = {"open_id": str(_huid), "access_token": _hcode,
                                                  "refresh_token": "", "platform": 4, "ts": now()}
                        our_code = "rzim_" + _uu.uuid4().hex[:20]
                        RZIM_CODES[our_code] = {"username": username}
                        _reqlog.info("[RZIM-OPENID] redirecionando com open_id deles %s (code=%s...)", _huid, our_code)
                        rr = redir or "gop100067://auth/"
                        return redirect_js(rr, our_code)
                    except Exception as _e3:
                        _reqlog.info("[RZIM] erro ao processar signed_request: %s", _e3)
                        return render_login_page(redir, cid, "Erro processando resposta deles: " + str(_e3)[:60], "login")
                _reqlog.info("[RZIM] LOGIN OK MAS SEM CODIGO NA PAGINA - corpo completo salvo")
            if status in (301, 302, 303) and ("code=" in (loc or "")):
                his_code = (loc.split("code=")[-1].split("&")[0]).strip()
                RZIM_CODES[his_code] = username
                _reqlog.info("[RZIM] CODIGO RECEBIDO! (%s...)", his_code[:12])
                return render_login_page(redir, cid, "RZIM aceitou o login! Codigo capturado - aguarde eu ligar a troca de token.", "login")
            if "captcha" in body.lower() or (loc or "").find("err=captcha") >= 0:
                return fail("O servidor deles recusou o captcha (valida o domínio).", "login")
            return render_login_page(redir, cid, "Resposta do servidor deles: " + body[:200], "login")
        except Exception as e:
            _reqlog.info("[RZIM] erro de rede: %s", e)
            return fail("Não consegui falar com o servidor deles: " + str(e)[:80], "login")

    if action == "register":
        if username in accs:
            return fail("Esse usuário já existe! Tenta entrar.", "login")
        if len(password) < 4:
            return fail("Senha muito curta (mínimo 4).", "reg")
        salt, h = hash_password(password)
        oid = account_open_id(username)
        accs[username] = {"salt": salt, "hash": h, "open_id": oid,
                          "nickname": (nickname or username)[:16], "created": now()}
        save_accounts(accs)
        code = make_auth_code(oid, accs[username]["nickname"])
    else:
        acc = accs.get(username)
        if not acc:
            return fail("Conta não encontrada. Cria uma conta!", "reg")
        salt, h = hash_password(password, acc["salt"])
        if not hmac.compare_digest(h, acc["hash"]):
            return fail("Senha errada!", "login")
        code = make_auth_code(acc["open_id"], acc["nickname"])

    if not redir:
        redir = "gop100067://auth/"
    from flask import make_response as _mr
    resp = _mr(redirect_js(redir, code)[0])
    # guarda cookie assinado: proximas entradas sao automaticas neste aparelho
    try:
        resp.set_cookie('ffauto', make_auto_cookie(username), max_age=90*24*3600, httponly=True)
        _reqlog.info("[AUTO-LOGIN] cookie gravado para a conta %s", username)
    except Exception as e:
        _reqlog.info("[AUTO-LOGIN] erro ao gravar cookie: %s", e)
    return resp

# --- OAUTH TOKEN EXCHANGE (geraico) ---
@app.route('/debug/players', methods=['GET'])
def debug_players():
    # lista resumida dos jogadores salvos (diagnostico)
    out = [{"open_id": oid, "nickname": p.get("nickname"), "account": p.get("account")}
           for oid, p in PLAYERS.items()]
    return jsonify(out)

@app.route('/debug/rzim_last', methods=['GET'])
def debug_rzim_last():
    return jsonify(RZIM_LAST)

@app.route('/debug/phone', methods=['POST', 'GET'])
def debug_phone():
    # telemetria do APK instrumentado (com/kryno/PhoneHome)
    body = request.get_data(as_text=True) or str(dict(request.args))
    _reqlog.info("!!! PHONE_HOME: %s", body[:1600] if body.startswith("CRASH_TRACE") else body[:400])
    try:
        d = json.loads(body)
        if d.get("ev") == "rsp":
            oid = str(d.get("openID") or "")
            at = str(d.get("at") or "")
            if oid and oid not in ("null", "None") and "." in at:
                payload = at.split(".")[0]
                pad = "=" * (-len(payload) % 4)
                p = json.loads(base64.urlsafe_b64decode(payload + pad))
                # so registra se o token eh um token de GUEST (nick Guest*) - evita
                # gravar o open_id que o proprio fluxo de conta entrega (round-trip)
                if str(p.get("nickname", "")).startswith("Guest"):
                    with open(DEVICE_GUEST_FILE, "w") as f:
                        json.dump({"open_id": oid, "t": now(), "at": at,
                                   "nick": p.get("nickname"), "expire": p.get("expire")}, f)
                    _reqlog.info("GUEST DO APARELHO REGISTRADO: %s (token cache salvo)", oid)
    except Exception as e:
        _reqlog.info("phone parse: %s", e)
    return "ok"

@app.route('/oauth/token/exchange', methods=['POST'])
def token_exchange():
    # O SDK GarenaMSDK envia: code=<authcode assinado>&redirect_uri=...&app_id=...&app_key=...
    # Precisa validar o code e devolver os tokens DA CONTA que logou na webview.
    code = request.form.get('code', '') or param('code') or ''
    if code.startswith("rzim_"):
        info = RZIM_CODES.get(code)
        if info:
            sess = RZIM_SESSIONS.get(info.get("username", ""))
            if sess:
                _reqlog.info("[RZIM-SDK-EXCHANGE] devolvendo sessao RZIM open_id=%s platform=%s",
                             sess["open_id"], sess.get("platform", 4))
                return jsonify({
                    "open_id": sess["open_id"],
                    "access_token": sess["access_token"],
                    "refresh_token": sess.get("refresh_token") or create_refresh_token(sess["open_id"], info.get("username", "rzim"), "guest"),
                    "platform": sess.get("platform", 4),
                    "mainPlatform": 0,
                    "expiry_time": now() + 86400 * 30,
                    "expires_in": 86400 * 30,
                    "token_type": "Bearer",
                })
        return jsonify({"code": 2017, "error": "invalid_grant"})
    d = read_auth_code(code) if code else None
    if d:
        # O jogo (servidor embutido 127.0.0.1) so aceita o open_id do guest registrado
        # no aparelho. Logar por conta = usar o open_id do guest + nick da conta.
        guest_oid = guest_open_id_for_request()
        acc_nick = d.get("nickname", "Player")
        if guest_oid and guest_oid != str(d["open_id"]):
            # aplica o nick da conta no jogador guest do aparelho
            get_player(guest_oid)
            PLAYERS[guest_oid]["nickname"] = acc_nick
            PLAYERS[guest_oid]["account"] = str(d["open_id"])
            save_players()
            _reqlog.info("EXCHANGE OK: conta %s assume guest %s", acc_nick, guest_oid)
            resp = account_token_response(guest_oid, acc_nick)
        else:
            resp = account_token_response(str(d["open_id"]), acc_nick)
            _reqlog.info("EXCHANGE OK: conta %s (open_id %s) -> tokens emitidos",
                         acc_nick, d["open_id"])
        # Forma EXATA do login guest (que o jogo aceita): mainPlatform=0.
        resp["platform"] = 0
        # REPLAY: se PhoneHome capturou o token guest em cache no aparelho,
        # devolver o MESMO token - o jogo nao consegue distinguir do login guest
        try:
            with open(DEVICE_GUEST_FILE) as f:
                dg = json.load(f)
            if dg.get("at") and dg.get("open_id") == resp.get("open_id"):
                resp["access_token"] = dg["at"]
                if dg.get("expire"):
                    resp["expiry_time"] = dg["expire"]
                    resp["expires_in"] = max(0, dg["expire"] - now())
                _reqlog.info("REPLAY: usando token guest em cache do aparelho (%s exp %s)",
                             dg.get("nick"), dg.get("expire"))
        except Exception as e:
            _reqlog.info("replay skip: %s", e)
        return jsonify(resp)
    _reqlog.info("EXCHANGE FALHOU: code invalido ou ausente: %s", code[:80])
    return jsonify({"error": "invalid_grant"})

# --- TOKEN INSPECT ---
@app.route('/oauth/token/inspect', methods=['GET'])
def token_inspect():
    t = request.args.get('token', '') or request.form.get('token', '')
    d = verify_token(t)
    if d:
        return jsonify({
            "active": True,
            "open_id": d["open_id"],
            "nickname": d["nickname"],
            "token_type": "Bearer",
            "exp": d.get("expire", 0)
        })
    return jsonify({"active": False, "code": 2017, "error": "invalid_token"})

# --- TOKEN REFRESH ---
@app.route('/oauth/token/refresh', methods=['POST'])
def token_refresh():
    rt = request.form.get('refresh_token', '')
    d = verify_refresh_token(rt)
    if d:
        at = create_token(d["open_id"], d["nickname"], d.get("type", "guest"))
        nr = create_refresh_token(d["open_id"], d["nickname"], d.get("type", "guest"))
        return jsonify({
            "access_token": at,
            "refresh_token": nr,
            "expires_in": 86400 * 30,
            "token_type": "Bearer"
        })
    return jsonify({"code": 2017, "error": "invalid_grant"})

# --- LOGOUT ---
@app.route('/oauth/logout', methods=['GET', 'POST'])
def logout():
    return jsonify({"success": "true"})

# --- USER INFO ---
@app.route('/oauth/user/info/get', methods=['POST', 'GET'])
def user_info():
    t = request.form.get('access_token', '') or request.args.get('access_token', '')
    d = verify_token(t)
    if not d:
        return jsonify({"code": 1004, "error": "invalid_token"})
    oid = d["open_id"]
    p = get_player(oid, d.get("nickname", "Player"))
    return jsonify({
        "open_id": oid,
        "platform": "guest",
        "icon": "",
        "nickname": d.get("nickname", "Player"),
        "gender": 1,
        "level": p["level"],
        "exp": p["exp"],
        "avatar": p["avatar"],
        "is_guest": True,
        "created_time": now(),
        "vip_level": p["vip"],
        "diamond": p["diamond"],
        "gold": p["gold"],
        "coins": p["coins"],
        "rank": p["rank"],
        "region": p["region"],
        "skin_ids": p["skins"][:50],
        "character_ids": p["characters"],
        "weapon_skin_ids": p["weapons"],
        "pet_ids": p["pets"],
        "badges": [],
        "achievements": []
    })

# --- FRIENDS ---
@app.route('/oauth/user/friends/get/v2', methods=['POST', 'GET'])
def friends():
    return jsonify({"friends": [], "total": 0, "pending": []})

@app.route('/oauth/user/friends/info/get/v2', methods=['POST', 'GET'])
def friends_info():
    return jsonify({"friends_info": []})

@app.route('/oauth/user/friends/inapp/get/v2', methods=['POST', 'GET'])
def friends_inapp():
    return jsonify({"friends": []})

# --- LOGIN SOCIAL (Redirecionados para criar conta Guest única) ---
@app.route('/oauth/token/facebook/exchange', methods=['POST'])
def fb(): return jsonify(create_guest_account(f'FB_{secrets.randbelow(9999)}'))

@app.route('/oauth/token/google/exchange', methods=['POST'])
def gg(): return jsonify(create_guest_account(f'Google_{secrets.randbelow(9999)}'))

@app.route('/oauth/token/line/exchange', methods=['POST'])
def ln(): return jsonify(create_guest_account(f'Line_{secrets.randbelow(9999)}'))

@app.route('/oauth/token/twitter/exchange', methods=['POST'])
def tw(): return jsonify(create_guest_account(f'TW_{secrets.randbelow(9999)}'))

@app.route('/oauth/token/vk/exchange/v2', methods=['POST'])
def vk(): return jsonify(create_guest_account(f'VK_{secrets.randbelow(9999)}'))

@app.route('/oauth/token/wechat/exchange', methods=['POST'])
def wc(): return jsonify(create_guest_account(f'WC_{secrets.randbelow(9999)}'))

# --- BIND ---
@app.route('/game/guest/bind', methods=['POST'])
def guest_bind():
    t = request.form.get('access_token', '')
    d = verify_token(t)
    if not d: return jsonify({"code": 1004, "error": "invalid_token"})
    return jsonify({"success": True, "open_id": d["open_id"]})

@app.route('/bind/app/platform/create', methods=['POST'])
def bind_create(): return jsonify({"success": True})
@app.route('/bind/app/platform/info/get', methods=['GET', 'POST'])
def bind_info(): return jsonify({"platforms": []})

# --- LOJA ---
@app.route('/info/pricing', methods=['GET'])
def pricing():
    return jsonify({"pricing": {"diamonds": [
        {"id": i, "name": f"{n} Diamonds", "price": p, "currency": "USD"}
        for i, n, p in [(1,"100",0.99),(2,"310",2.99),(3,"520",4.99),(4,"1060",9.99),(5,"2180",19.99),(6,"5600",49.99)]
    ]}})

@app.route('/info/app-items', methods=['GET'])
def app_items():
    return jsonify({"items": [
        {"id": "skin_001", "name": "AK47 Skin", "type": "weapon", "price": 500, "currency": "diamond"},
        {"id": "skin_002", "name": "M4A1 Skin", "type": "weapon", "price": 500, "currency": "diamond"},
        {"id": "char_001", "name": "Character A", "type": "character", "price": 1500, "currency": "diamond"}
    ]})

@app.route('/app/point/get_balance', methods=['POST', 'GET'])
def balance():
    t = request.form.get('access_token', '') or request.args.get('access_token', '')
    d = verify_token(t)
    if not d: return jsonify({"code": 1004, "error": "invalid_token"})
    p = get_player(d["open_id"], d.get("nickname", "Player"))
    return jsonify({"diamond": p["diamond"], "gold": p["gold"], "coins": p["coins"], "tokens": p["tokens"]})

@app.route('/options/get', methods=['GET'])
def options():
    return jsonify({"options": [{"id": "free", "name": "Free (Private Server)", "enabled": True}]})

@app.route('/pay/google/commit', methods=['POST'])
def pay():
    return jsonify({"success": True, "message": "Item desbloqueado!"})

@app.route('/pay/event/init', methods=['POST'])
def pay_init(): return jsonify({"success": True, "event_id": str(uuid.uuid4())})
@app.route('/pay/event/cancel', methods=['POST'])
def pay_cancel(): return jsonify({"success": True})

# --- EVENTOS ---
@app.route('/info/event/config', methods=['GET'])
def event_cfg(): return jsonify({"events": []})
@app.route('/info/event/pricing', methods=['GET'])
def event_price(): return jsonify({"event_pricing": {}})
@app.route('/info/rebates', methods=['GET'])
def rebates(): return jsonify({"rebates": []})
@app.route('/rebates/redeem', methods=['POST'])
def redeem(): return jsonify({"success": True})

# --- GAME / SDK ---
@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    t = request.form.get('access_token', '')
    d = verify_token(t)
    if not d: return jsonify({"code": 1004, "error": "invalid_token"})
    return jsonify({"status": "ok", "server_time": now(), "game_server": f"{GAME_IP}:{GAME_PORT}"})

@app.route('/api/msdk', methods=['POST', 'GET'])
def msdk():
    return jsonify({
        "status": 0,
        "server_time": now(),
        "game_server": {"ip": GAME_IP, "port": GAME_PORT},
        "config": {"version": CLIENT_VERSION, "maintenance": False, "notice": ""}
    })

@app.route('/game/user/request/send', methods=['POST'])
def req_send(): return jsonify({"success": True})

# --- REDIRECIONAMENTO DE ENDPOINTS GOOGLE ADICIONAIS ---
@app.route('/google/init', methods=['POST', 'GET'])
def g_init(): 
    return jsonify(create_guest_account(f'Google_{secrets.randbelow(9999)}'))

# --- OAUTH GARENA ---
@app.route('/oauth/garena', methods=['GET', 'POST'])
def garena():
    return jsonify(create_guest_account(f'Garena_{secrets.randbelow(9999)}'))

@app.route('/oauth/login', methods=['GET'])
def login(): return jsonify({"login_url": "", "guest_enabled": True})

# --- CRASH LOGS ---
@app.route('/crash/logs/', methods=['POST'])
def crash(): return jsonify({"success": True})

# --- ACCOUNT INFO ---
@app.route('/api/account_info_get', methods=['GET', 'POST'])
def account_info():
    t = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    d = verify_token(t) or verify_token(request.args.get('access_token', ''))
    if d:
        oid = d["open_id"]
        nick = d.get("nickname", f"Player{oid[:6]}")
    else:
        oid = request.args.get('open_id', gen_open_id())
        nick = f"Player{oid[:6]}"
    p = get_player(oid, nick)
    return jsonify({
        "open_id": oid,
        "account_id": oid,
        "nickname": p["nickname"],
        "level": p["level"],
        "exp": p["exp"],
        "diamonds": p["diamond"],
        "coins": p["coins"],
        "rank": p["rank"],
        "region": p["region"],
        "avatar": p["avatar"],
        "character_ids": p["characters"],
        "skin_ids": p["skins"][:50],
        "weapon_skin_ids": p["weapons"],
        "pet_ids": p["pets"],
        "is_banned": False,
        "is_guest": True
    })

# --- CHANNEL INFO ---
@app.route('/api/channel_info_get', methods=['GET', 'POST'])
def channel_info():
    return jsonify({"channel_id": "BR", "channel_name": "Brasil", "status": "active"})

# --- SESSION TOKEN ---
@app.route('/api/session_token_get', methods=['GET', 'POST'])
def session_token():
    return jsonify({"session_token": gen_open_id(), "open_id": gen_open_id(), "expiry_time": now() + 3600})

# --- REFRESH TOKEN GET ---
@app.route('/api/refresh_token_get', methods=['GET', 'POST'])
def refresh_get():
    account = create_guest_account()
    return jsonify({"access_token": account["access_token"], "expiry_time": now() + 86400 * 30})

# --- HEALTH ---
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "server": "Free Fire Private Server",
        "version": "2.1.0",
        "http_port": HTTP_PORT,
        "game_port": GAME_PORT,
        "game_ip": GAME_IP,
        "players": len(PLAYERS),
        "rooms": len(ROOMS),
        "connections": len(SOCKETS)
    })



# --- LIVEVER (checagem de versao/manutencao - cliente 2018) ---
@app.route('/livever.php', methods=['GET', 'POST'])
def livever():
    args = dict(request.args)
    version = args.get('version', '1.25.3')
    print(f"[LIVEVER] {request.method} args={args}", flush=True)
    # resposta clonada do versionscommon.barbosasmobile.com/live/ver.php (formato real HttpVerInfo)
    return jsonify({
        "code": 0,
        "is_server_open": True,
        "is_firewall_open": False,
        "billboard_msg": "Bem-vindo ao servidor privado desenvolvido por Bryan",
        "remote_version": version,
        "remote_option_version": version,
        "cdn_url": "https://cdn.barbosasmobile.com/",
        "server_url": "https://loginbp.barbosasmobile.com/",
        "is_review_server": False,
        "appstore_url": "",
        "force_to_restart_app": False,
        "country_code": "BR",
        "gdpr_version": 2,
        "maintenance_announcement": "",
        "maintenance_region": "",
        "client_ip": request.remote_addr or "0.0.0.0"
    })

# --- CLOUD CONFIG (versionscommon) — cliente 2018 pede aqui ---
@app.route('/live', methods=['GET', 'POST'])
@app.route('/live/', methods=['GET', 'POST'])
def live_config():
    body = request.get_data(as_text=True)[:500]
    print(f"[LIVE] {request.method} {request.full_path} args={dict(request.args)} body={body}", flush=True)
    return jsonify({
        "code": 0,
        "status": 0,
        "data": {"status": 0},
        "configs": {},
        "server_time": now()
    })

# --- CATCH ALL ---
@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def catch_all(path):
    return jsonify({"status": "ok", "server_time": now()})


# ==================================================================
# ============== GAME SERVER (TCP SOCKET - PORTA 2205) ==============
# ==================================================================
class GameServer:
    def __init__(self, host='0.0.0.0', port=2205):
        self.host = host
        self.port = port
        self.running = False
        self.clients = {}
        self.rooms = {}
        self.sock = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((self.host, self.port))
            self.sock.listen(100)
            self.running = True
            print(f"[GAME] Servidor de jogo iniciado em {self.host}:{self.port}")
            threading.Thread(target=self._accept_loop, daemon=True).start()
        except Exception as e:
            print(f"[GAME] Erro ao iniciar: {e}")

    def _accept_loop(self):
        while self.running:
            try:
                client, addr = self.sock.accept()
                print(f"[GAME] Conexao recebida de {addr}")
                self.clients[client] = {"addr": addr, "open_id": None, "nickname": None}
                threading.Thread(target=self._handle_client, args=(client, addr), daemon=True).start()
            except:
                pass

    def _handle_client(self, client, addr):
        try:
            welcome = self._make_packet(0x01, json.dumps({
                "status": "ok", "server_time": now(),
                "server_name": "Free Fire Private Server",
                "version": CLIENT_VERSION, "region": "BR",
                "max_players": 50, "online": len(self.clients)
            }).encode())
            client.sendall(welcome)

            while self.running:
                try:
                    data = client.recv(4096)
                    if not data: break
                    self._process_packet(client, data)
                except:
                    break
        except:
            pass
        finally:
            if client in self.clients: del self.clients[client]
            try: client.close()
            except: pass

    def _make_packet(self, msg_type, payload):
        if isinstance(payload, str): payload = payload.encode()
        return struct.pack('>BI', msg_type, len(payload)) + payload

    def _process_packet(self, client, data):
        if len(data) < 5: return
        msg_type = data[0]
        payload_len = struct.unpack('>I', data[1:5])[0]
        payload = data[5:5+payload_len] if payload_len > 0 else b''
        try: msg = json.loads(payload) if payload else {}
        except: msg = {}
        cinfo = self.clients.get(client, {})

        if msg_type == 0x01:  # Login
            oid = msg.get("open_id", gen_open_id())
            nick = msg.get("nickname", f"Player{oid[:6]}")
            cinfo["open_id"] = oid
            cinfo["nickname"] = nick
            p = get_player(oid, nick)
            client.sendall(self._make_packet(0x01, json.dumps({
                "status": "ok", "open_id": oid, "nickname": nick,
                "level": p["level"], "diamond": p["diamond"], "gold": p["gold"],
                "rank": p["rank"], "region": p["region"],
                "characters": p["characters"], "skins": p["skins"][:50],
                "weapons": p["weapons"], "pets": p["pets"]
            }).encode()))

        elif msg_type == 0x02:  # Heartbeat
            client.sendall(self._make_packet(0x02, json.dumps({"status": "ok", "time": now()}).encode()))

        elif msg_type == 0x03:  # Join Lobby
            client.sendall(self._make_packet(0x03, json.dumps({
                "status": "ok", "lobby_id": "BR_01",
                "online_players": len(self.clients), "rooms": len(self.rooms),
                "events": [], "news": {"title": "Bem-vindo", "content": "Servidor privado ativo"}
            }).encode()))

        elif msg_type == 0x04:  # Create Room
            rid = str(uuid.uuid4())[:8]
            mode = msg.get("mode", "classic")
            map_name = msg.get("map", "bermuda")
            self.rooms[rid] = {"id": rid, "owner": cinfo.get("open_id"),
                "players": [cinfo.get("open_id")], "mode": mode, "map": map_name,
                "max_players": 50, "status": "waiting", "created": now()}
            client.sendall(self._make_packet(0x04, json.dumps(
                {"status": "ok", "room_id": rid, "mode": mode, "map": map_name}).encode()))

        elif msg_type == 0x05:  # Join Room
            rid = msg.get("room_id", "")
            if rid in self.rooms and len(self.rooms[rid]["players"]) < self.rooms[rid]["max_players"]:
                self.rooms[rid]["players"].append(cinfo.get("open_id"))
                client.sendall(self._make_packet(0x05, json.dumps(
                    {"status": "ok", "room_id": rid, "players": len(self.rooms[rid]["players"])}).encode()))
            else:
                client.sendall(self._make_packet(0x05, json.dumps({"status": "not_found"}).encode()))

        elif msg_type == 0x07:  # Start Match
            client.sendall(self._make_packet(0x07, json.dumps({
                "status": "ok", "match_id": str(uuid.uuid4())[:12],
                "server_ip": GAME_IP, "server_port": GAME_PORT + 1,
                "map": "bermuda"
            }).encode()))

        elif msg_type == 0x08:  # Player Data
            oid = cinfo.get("open_id", gen_open_id())
            p = get_player(oid, cinfo.get("nickname", "Player"))
            client.sendall(self._make_packet(0x08, json.dumps({
                "nickname": p["nickname"], "level": p["level"],
                "diamond": p["diamond"], "gold": p["gold"], "coins": p["coins"],
                "rank": p["rank"], "skins": p["skins"][:50],
                "characters": p["characters"], "weapons": p["weapons"], "pets": p["pets"]
            }).encode()))

        elif msg_type == 0x09:  # Chat
            chat_pkt = self._make_packet(0x09, json.dumps({
                "nickname": cinfo.get("nickname", "Player"),
                "message": msg.get("message", ""), "time": now()
            }).encode())
            for c in self.clients:
                try: c.sendall(chat_pkt)
                except: pass

        elif msg_type == 0x0B:  # Matchmaking
            found = None
            for rid, room in self.rooms.items():
                if room["status"] == "waiting" and len(room["players"]) < room["max_players"]:
                    found = rid; break
            if not found:
                found = str(uuid.uuid4())[:8]
                self.rooms[found] = {"id": found, "owner": None, "players": [],
                    "mode": "classic", "map": "bermuda", "max_players": 50, "status": "waiting", "created": now()}
            self.rooms[found]["players"].append(cinfo.get("open_id"))
            client.sendall(self._make_packet(0x0B, json.dumps(
                {"status": "ok", "room_id": found, "players": len(self.rooms[found]["players"])}).encode()))

        elif msg_type == 0x0C:  # Room List
            rooms = [{"id": r["id"], "mode": r["mode"], "map": r["map"],
                       "players": len(r["players"]), "max": r["max_players"], "status": r["status"]}
                      for r in self.rooms.values()]
            client.sendall(self._make_packet(0x0C, json.dumps({"rooms": rooms}).encode()))

        else:
            client.sendall(self._make_packet(msg_type, json.dumps({"status": "ok"}).encode()))


# ==================================================================
# UDP SERVER
# ==================================================================
class UDPServer:
    def __init__(self, host='0.0.0.0', port=2206):
        self.host = host; self.port = port; self.running = False; self.sock = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((self.host, self.port))
            self.running = True
            print(f"[UDP] Servidor UDP iniciado em {self.host}:{self.port}")
            threading.Thread(target=self._loop, daemon=True).start()
        except Exception as e:
            print(f"[UDP] Erro: {e}")

    def _loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                if data:
                    self.sock.sendto(struct.pack('>BI', 0x02, now()), addr)
            except:
                pass


# ==================================================================
# INICIALIZAÇÃO
# ==================================================================
game_server = GameServer(port=GAME_PORT)
udp_server = UDPServer(port=GAME_PORT + 1)

if __name__ == '__main__':
    game_server.start()
    udp_server.start()
    print(f"""
    Free Fire Private Server v2.1.0
    HTTP: porta {HTTP_PORT}
    Game TCP: porta {GAME_PORT}
    Game UDP: porta {GAME_PORT + 1}
    Tokens: STATELESS (HMAC assinado)
    """)
    app.run(host='0.0.0.0', port=HTTP_PORT, debug=False)
