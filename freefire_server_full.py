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
PLAYERS = {}  # cache em memória (opcional, dados são gerados)
ROOMS = {}
SOCKETS = {}

def gen_open_id():
    return str(uuid.uuid4()).replace("-", "")[:20]

def now():
    return int(time.time())

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
    return PLAYERS[open_id]


# ==================================================================
# ============== HTTP API SERVER (FLASK) ==============
# ==================================================================

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
        "notice": {"title": "Servidor Privado", "content": "Bem-vindo!", "show": False}
    })

@app.route('/app/feedback', methods=['POST', 'GET'])
def feedback():
    return jsonify({"success": True})

# --- GUEST REGISTER ---
@app.route('/oauth/guest/register', methods=['POST'])
def guest_register():
    app_id = request.form.get('app_id', '')
    nickname = request.form.get('nickname', f'Guest{secrets.randbelow(99999)}')
    gender = int(request.form.get('gender', '1'))
    password = request.form.get('password', '')
    source = int(request.form.get('source', '0'))

    if app_id != APP_ID:
        return jsonify({"code": 1001, "error": "invalid_app_id"})

    open_id = gen_open_id()
    at = create_token(open_id, nickname, "guest")
    rt = create_refresh_token(open_id, nickname, "guest")
    get_player(open_id, nickname)

    return jsonify({
        "open_id": open_id,
        "access_token": at,
        "refresh_token": rt,
        "expires_in": 86400 * 30,
        "token_type": "Bearer"
    })

# --- GUEST TOKEN GRANT ---
@app.route('/oauth/guest/token/grant', methods=['POST'])
def guest_grant():
    app_id = request.form.get('app_id', '')
    if app_id != APP_ID:
        return jsonify({"code": 2017, "error": "invalid_grant"})

    open_id = gen_open_id()
    nick = f'Guest{secrets.randbelow(99999)}'
    at = create_token(open_id, nick, "guest")
    rt = create_refresh_token(open_id, nick, "guest")
    get_player(open_id, nick)

    return jsonify({
        "open_id": open_id,
        "access_token": at,
        "refresh_token": rt,
        "expires_in": 86400 * 30,
        "token_type": "Bearer"
    })

# --- OAUTH TOKEN ---
@app.route('/oauth/token', methods=['POST'])
def oauth_token():
    gt = request.form.get('grant_type', '')
    rt = request.form.get('refresh_token', '')

    if gt == 'authorization_code':
        oid = gen_open_id()
        nick = f'Player{secrets.randbelow(99999)}'
        at = create_token(oid, nick, "oauth")
        nr = create_refresh_token(oid, nick, "oauth")
        get_player(oid, nick)
        return jsonify({
            "access_token": at,
            "refresh_token": nr,
            "open_id": oid,
            "expires_in": 86400 * 30,
            "token_type": "Bearer"
        })

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
        "nickname": d.get("nickname", "Player"),
        "gender": 1,
        "level": p["level"],
        "exp": p["exp"],
        "avatar": p["avatar"],
        "is_guest": d.get("type") == "guest",
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

# --- LOGIN SOCIAL (stubs) ---
@app.route('/oauth/token/facebook/exchange', methods=['POST'])
def fb(): return jsonify({"code": 2017, "error": "not_supported"})
@app.route('/oauth/token/google/exchange', methods=['POST'])
def gg(): return jsonify({"code": 2017, "error": "not_supported"})
@app.route('/oauth/token/line/exchange', methods=['POST'])
def ln(): return jsonify({"code": 2017, "error": "not_supported"})
@app.route('/oauth/token/twitter/exchange', methods=['POST'])
def tw(): return jsonify({"code": 2017, "error": "not_supported"})
@app.route('/oauth/token/vk/exchange/v2', methods=['POST'])
def vk(): return jsonify({"code": 2017, "error": "not_supported"})
@app.route('/oauth/token/wechat/exchange', methods=['POST'])
def wc(): return jsonify({"code": 2017, "error": "not_supported"})

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
@app.route('/google/init', methods=['POST'])
def g_init(): return jsonify({"success": True})

# --- OAUTH GARENA ---
@app.route('/oauth/garena', methods=['GET'])
def garena():
    oid = gen_open_id()
    nick = f'Player{secrets.randbelow(99999)}'
    at = create_token(oid, nick, "garena")
    return jsonify({"code": secrets.token_hex(16), "open_id": oid, "access_token": at})

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
    return jsonify({"access_token": create_token(gen_open_id(), "Player", "guest"), "expiry_time": now() + 86400 * 30})

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
