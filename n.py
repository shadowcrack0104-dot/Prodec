#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================
  💎 VPN DECODER PRO  —  v9.1  (HOST READY + TEXT DECODE)
================================================================
  • Decode from FILE or TEXT (link/base64/json/hex)
  • Premium inline keyboards + QR codes
  • Live admin dashboard + CSV export
  • /start bulletproof (works on all hosts)
  • Rate limit bypass for owners
================================================================
"""

import os, io, re, sys, json, time, gzip, base64, struct, csv
import pickle, hashlib, sqlite3, asyncio, logging, threading
import contextlib, traceback, urllib.parse
from datetime import datetime
from pathlib import Path

import pytz
from Crypto.Cipher import AES, ChaCha20
from Crypto.Util.Padding import unpad

# ── OPTIONAL DEPS ──
try:
    from Crypto.Cipher import ChaCha20_Poly1305
    HAVE_POLY = True
except ImportError:
    HAVE_POLY = False

try:
    from argon2.low_level import hash_secret_raw, Type
    HAVE_ARGON2 = True
except ImportError:
    HAVE_ARGON2 = False

try:
    import msgpack
    HAVE_MSGPACK = True
except ImportError:
    HAVE_MSGPACK = False

try:
    import qrcode
    HAVE_QR = True
except ImportError:
    HAVE_QR = False

try:
    from dotenv import load_dotenv as _ld
    _ld()
except Exception:
    pass

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
from telegram.error import Conflict, NetworkError, TimedOut

# ══════════════════════════════════════════════════════════════
#  ⚙️  CONFIG
# ══════════════════════════════════════════════════════════════
BOT_TOKEN = os.getenv("BOT_TOKEN") or "9074615064:AAEdxaqujGCN_Oq9emrG7ovn_DB_-eKX2c8"

OWNER_IDS = set()
for x in (os.getenv("OWNER_IDS", "7986731915") or "").split(","):
    x = x.strip()
    if x.isdigit():
        OWNER_IDS.add(int(x))
if not OWNER_IDS:
    OWNER_IDS.add(7986731915)

DATABASE_PATH = os.getenv("DATABASE_PATH", "vpn_bot.db")
WHITEBOX_FILE = os.getenv("WHITEBOX_FILE", "whitebox_blob.txt")

BD_TZ = pytz.timezone("Asia/Dhaka")
BRAND     = "VPN DECODER PRO"
BRAND_VER = "9.1"
BRAND_OWN = "@King_X_vau"
BRAND_CH  = "@Trickbuzz_BD"

RATE_MIN_GAP   = 3
RATE_BURST_MAX = 5
RATE_BURST_WIN = 60
MAX_FILE_SIZE  = 10 * 1024 * 1024
COPY_TTL       = 3600

# ══════════════════════════════════════════════════════════════
#  🔧 LOGGING
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("vpn_pro.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger("VPNPro")


def bd_time():     return datetime.now(BD_TZ).strftime("%I:%M:%S %p")
def is_owner(uid): return uid in OWNER_IDS


def esc(s):
    return (str(s).replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;"))


# ══════════════════════════════════════════════════════════════
#  💾 DATABASE
# ══════════════════════════════════════════════════════════════
_lock = threading.Lock()


@contextlib.contextmanager
def _db():
    c = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def db_init():
    with _lock, _db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                uid INTEGER PRIMARY KEY,
                username TEXT, first_name TEXT,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                total_success INTEGER DEFAULT 0,
                total_failed  INTEGER DEFAULT 0,
                total_unlocks INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER, filename TEXT, ext TEXT,
                decoder TEXT, success INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        c.commit()


def db_user(uid):
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(r) if r else None


def db_upsert_user(uid, un, fn):
    with _lock, _db() as c:
        c.execute("""INSERT INTO users (uid, username, first_name)
                     VALUES (?,?,?) ON CONFLICT(uid) DO UPDATE SET
                     username=excluded.username,
                     first_name=excluded.first_name""", (uid, un, fn))
        c.commit()


def db_log(uid, fn, ext, decoder, success, unlock=False):
    with _lock, _db() as c:
        c.execute("INSERT OR IGNORE INTO users (uid) VALUES (?)", (uid,))
        c.execute("""INSERT INTO files (uid,filename,ext,decoder,success)
                     VALUES (?,?,?,?,?)""",
                  (uid, fn, ext, decoder, int(success)))
        c.execute("""UPDATE users SET
                     total_success = total_success + ?,
                     total_failed  = total_failed  + ?,
                     total_unlocks = total_unlocks + ?
                     WHERE uid=?""",
                  (1 if success else 0,
                   0 if success else 1,
                   1 if unlock else 0, uid))
        c.commit()


def db_admin_stats():
    with _lock, _db() as c:
        total_users   = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_files   = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        total_unlocks = c.execute(
            "SELECT COALESCE(SUM(total_unlocks),0) FROM users").fetchone()[0]
        total_success = c.execute(
            "SELECT COALESCE(SUM(total_success),0) FROM users").fetchone()[0]
        total_failed  = c.execute(
            "SELECT COALESCE(SUM(total_failed),0) FROM users").fetchone()[0]
        today_files = c.execute(
            "SELECT COUNT(*) FROM files WHERE date(created_at)=date('now')"
        ).fetchone()[0]
        rows = c.execute("""
            SELECT uid, username, first_name,
                   total_success, total_failed, total_unlocks, joined_at
            FROM users
            ORDER BY total_unlocks DESC, total_success DESC
        """).fetchall()
        return {
            "total_users": total_users,
            "total_files": total_files,
            "total_unlocks": total_unlocks,
            "total_success": total_success,
            "total_failed": total_failed,
            "today_files": today_files,
            "users": [dict(r) for r in rows],
        }


# ══════════════════════════════════════════════════════════════
#  🌑  DARKTUNNEL DECODER (.dark)
# ══════════════════════════════════════════════════════════════
_DT256 = b"$B&E)H@McQfThWmZq4t7w!z%C*F-JaNd"
_DT192 = b"F)J@NcRfUjXn2r4u7x!A%D*G"
_DTIV  = bytes.fromhex("232e39185523184a5723586242200e05")


def decode_dark(fb):
    if not HAVE_MSGPACK:
        return None

    def b64(d):
        c = d.replace("-", "+").replace("_", "/")
        if p := len(c) % 4: c += "=" * (4 - p)
        return base64.b64decode(c)

    def aes(d, k, iv):
        return AES.new(k, AES.MODE_CFB, iv=iv, segment_size=128).decrypt(d)

    def is_utf8(v):
        if not v: return False
        try:
            return bool(re.fullmatch(r"[^\x00-\x08\x0B\x0C\x0E-\x1F\x7F]*",
                                     v.decode("utf-8")))
        except UnicodeDecodeError:
            return False

    def tj(v):
        s = v.strip()
        if (s.startswith("{") and s.endswith("}")) or \
           (s.startswith("[") and s.endswith("]")):
            with contextlib.suppress(Exception):
                fixed = re.sub(r'(:\s*)(\$[A-Za-z0-9_]+)', r'\1"\2"', s)
                return norm(json.loads(fixed))
        return v

    def norm(v):
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items() if k != "Password"}
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, bytes):
            return tj(v.decode("utf-8")) if is_utf8(v) else list(v)
        if isinstance(v, str):
            return tj(v)
        return v

    def clean(v, k, iv):
        if isinstance(v, dict):
            o = {}
            for kk, xx in v.items():
                if isinstance(kk, str) and kk.startswith("Encrypted") and \
                        xx and isinstance(xx, (bytes, bytearray)):
                    try: o[kk] = aes(bytes(xx), k, iv)
                    except Exception: o[kk] = xx
                else:
                    o[kk] = clean(xx, k, iv)
            return o
        if isinstance(v, list):
            return [clean(x, k, iv) for x in v]
        return v

    with contextlib.suppress(Exception):
        raw = fb.decode("utf-8", errors="ignore").strip()
        if not raw: return None
        if "://" in raw: raw = raw.split("://", 1)[1]
        outer = json.loads(b64(raw).decode("utf-8"))
        if "encryptedLockedConfig" not in outer: return None
        do = aes(b64(outer["encryptedLockedConfig"]), _DT256, _DTIV)
        up = msgpack.unpackb(do, raw=False, strict_map_key=False)
        if "EncryptedLockedConfig" in up:
            di = aes(up["EncryptedLockedConfig"], _DT192, _DTIV)
            up["EncryptedLockedConfig"] = clean(
                msgpack.unpackb(di, raw=False, strict_map_key=False),
                _DT192, _DTIV)
        outer["encryptedLockedConfig"] = up
        return json.dumps(norm(outer), indent=2, ensure_ascii=False)
    return None


# ══════════════════════════════════════════════════════════════
#  🔒  EHI DECODER (.ehi)
# ══════════════════════════════════════════════════════════════
_E1 = bytes.fromhex("7e1210f7aab956f7a668bda6e57feddb7f84ad840aef8d27b1b969959be3ab6c")
_E2 = bytes.fromhex("b2bc617c32d8b9eb1943a5ffa8051eea")
_EE = b"null=V5kU5+FFrY\x00"
_EB = (
    bytes.fromhex("221d572349555f1d112133236b1f4a3f"),
    bytes.fromhex("5543494c53443e3f4a6a4539384e776a"),
    bytes.fromhex("374c2541575e4d531a3c327b75431e5f"),
)
_ES = (
    bytes.fromhex("2c5d1147bbad422b3b334d4d235f1a53"),
    bytes.fromhex("522b01433a5e8b2fc7549e1ad368e541"),
    bytes.fromhex("337a1035aaedf3458ca167e92d74b839"),
)
_EA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_EC = "RkLC2QaVMPYgGJW/A4f7qzDb9e+t6Hr0Zp8OlNyjuxKcTw1o5EIimhBn3UvdSFXs"
_ET = str.maketrans(_EC, _EA)


def decode_ehi(fb):
    if not (HAVE_ARGON2 and HAVE_POLY):
        return None

    def cb(s):
        c = s.replace("?", "")
        if r := len(c) % 4: c += "=" * (4 - r)
        return base64.b64decode(c.translate(_ET))

    def xl(cs, key):
        if not cs or not cs.strip(): return cs
        with contextlib.suppress(Exception):
            hx = cb(cs[::-1]).decode("ascii")
            if len(hx) % 2: hx = "0" + hx
            rb = bytes.fromhex(hx)
            kl = len(key)
            d = bytearray(b ^ ord(key[i % kl]) for i, b in enumerate(rb)
                          if (b ^ ord(key[i % kl])) != 0)
            pt = d.decode("utf-8")
            if pt and (sum(1 for c in pt if ord(c) < 32 and
                           ord(c) not in (9, 10, 13)) / len(pt)) > 0.5:
                return None
            return pt
        return None

    def dmsg(cs):
        if not cs or not cs.strip(): return cs
        with contextlib.suppress(Exception):
            p = cs + "=" * ((4 - len(cs) % 4) % 4)
            rb = base64.b64decode(p)
            u = rb.decode("utf-8", errors="replace").encode(
                "utf-16-be", errors="surrogatepass")
            n = len(u) // 2
            jc = struct.unpack(f">{n}H", u)
            kc = [ord(c) for c in "EHIMSG"]
            xc = [j ^ kc[i % len(kc)] for i, j in enumerate(jc)]
            return struct.pack(f">{n}H", *xc).decode(
                "utf-16-be", errors="surrogatepass"
            ).encode("utf-16", "surrogatepass").decode("utf-16")
        return cs

    def xx(data, key):
        if not data: return b""
        if r := len(data) % 4: data += b"\x00" * (4 - r)
        k = struct.unpack("<4I", key.ljust(16, b"\x00")[:16])
        n = len(data) // 4
        v = list(struct.unpack(f"<{n}I", data))
        dl = 0x9E3779B9
        s = ((6 + 52 // n) * dl) & 0xFFFFFFFF
        y = v[0]
        while s != 0:
            e = (s >> 2) & 3
            for p in range(n - 1, 0, -1):
                z = v[p - 1]
                mx = (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ \
                     ((s ^ y) + (k[(p & 3) ^ e] ^ z))
                y = v[p] = (v[p] - mx) & 0xFFFFFFFF
            z = v[n - 1]
            mx = (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^ \
                 ((s ^ y) + (k[e] ^ z))
            y = v[0] = (v[0] - mx) & 0xFFFFFFFF
            s = (s - dl) & 0xFFFFFFFF
        dec = struct.pack(f"<{n}I", *v)
        ln = v[-1]
        return dec[:ln] if 0 < ln <= n * 4 else dec.rstrip(b"\x00")

    def parse(fb):
        try:
            f = io.BytesIO(fb)
            def ru():
                lb = f.read(2)
                if len(lb) < 2: return ""
                return f.read(struct.unpack(">H", lb)[0]).decode(
                    "utf-8", errors="ignore")
            ru(); f.read(8); ru(); f.read(8)
            plb = f.read(4)
            if len(plb) < 4: return None
            pl = struct.unpack(">I", plb)[0]
            f.read(8)
            return f.read(pl)
        except struct.error:
            return None

    def gmk(cfg):
        p = "".join(str(x) for x in (
            cfg.get("configAesKey", ""), cfg.get("configIdentifier", ""),
            cfg.get("configSalt", ""), str(cfg.get("configTimestamp", 0)),
            str(cfg.get("configExpiryTimestamp", 0)),
            cfg.get("lockModes", ""), cfg.get("lockModesHash", ""),
            cfg.get("configHwid", ""),
            cfg.get("configLockMobileOperatorId", "")) if x)
        return hashlib.sha256(p.encode()).digest()

    def inner(parsed, salt):
        o = {}
        for k, v in parsed.items():
            if isinstance(v, str) and v.strip():
                dv = dmsg(v) if k == "configMessage" else xl(v, salt)
                if dv is not None: o[k] = dv
                elif k == "overwriteServerData": o[k] = v
            else: o[k] = v
        return o

    payload = parse(fb)
    if not payload: return None

    config, miv = None, None
    for iv in _EB + _ES:
        with contextlib.suppress(Exception):
            c1 = AES.new(_E1, AES.MODE_CBC, iv)
            l1 = unpad(c1.decrypt(payload), 16).decode("utf-8")
            pr = l1.split(":")
            if len(pr) >= 3:
                c2 = AES.new(_E2, AES.MODE_CBC, base64.b64decode(pr[0]))
                g = unpad(c2.decrypt(base64.b64decode(pr[2])), 16)
                fr = xx(g, _EE)
                if (st := fr.find(b"{")) != -1:
                    config = json.loads(fr[st:].decode("utf-8", errors="ignore"))
                    miv = iv
                    break
    if not config: return None

    salt = config.get("configSalt", "EVZJNI")
    if miv in _EB:
        pf = config
    else:
        td = config.get("configData")
        if not td: return None
        aaa = xl(td, salt)
        if not aaa: return None
        rp = base64.b64decode(aaa)
        if len(rp) <= 50: return None
        try:
            ak = hash_secret_raw(
                secret=gmk(config), salt=rp[0x0A:0x1A],
                time_cost=int.from_bytes(rp[1:5], "little"),
                memory_cost=int.from_bytes(rp[5:9], "little"),
                parallelism=rp[9], hash_len=32, type=Type.ID)
            nonce = rp[0x1A:0x1A + 12]
            c3 = ChaCha20_Poly1305.new(key=ak, nonce=nonce)
            c3.update(rp[:0x1A])
            dj = c3.decrypt_and_verify(rp[0x32:-16], rp[-16:])
            pf = json.loads(dj.decode("utf-8", errors="ignore"))
        except Exception:
            return None
    return json.dumps(inner(pf, salt), indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════
#  🎭  NPVT DECODER (.npvt)
# ══════════════════════════════════════════════════════════════
_wb_cache = None


def _load_wb():
    global _wb_cache
    if _wb_cache is not None: return _wb_cache
    if not os.path.exists(WHITEBOX_FILE): return None
    try:
        with open(WHITEBOX_FILE, "r") as f: blob = f.read().strip()
        if not blob: return None
        _wb_cache = pickle.loads(gzip.decompress(base64.b64decode(blob)))
        return _wb_cache
    except Exception as e:
        logger.warning("whitebox load failed: %s", e); return None


def decode_npvt(fb):
    wb = _load_wb()
    if not wb: return None
    p2, p3, p4, p5 = wb

    def wbe(block):
        state = list(block)
        perm = [0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2, 7, 12, 1, 6, 11]
        for r in range(2):
            state = [state[perm[i]] for i in range(16)]
            if r == 1: break
            mid = [0] * 16
            for col in range(4):
                t0, t1, t2, t3 = [p3[r][col * 4 + i][state[col * 4 + i]]
                                  for i in range(4)]
                for row in range(4):
                    idx = col * 24 + row * 6
                    sh, sl = 28 - (row * 8), 24 - (row * 8)
                    x1h = p2[r][idx][(t0 >> sh) & 0xF][(t1 >> sh) & 0xF]
                    x2h = p2[r][idx + 1][(t2 >> sh) & 0xF][(t3 >> sh) & 0xF]
                    hi = p2[r][idx + 4][x1h][x2h]
                    x1l = p2[r][idx + 2][(t0 >> sl) & 0xF][(t1 >> sl) & 0xF]
                    x2l = p2[r][idx + 3][(t2 >> sl) & 0xF][(t3 >> sl) & 0xF]
                    lo = p2[r][idx + 5][x1l][x2l]
                    mid[col * 4 + row] = ((hi << 4) | lo) & 0xFF
            new = [0] * 16
            for col in range(4):
                t0, t1, t2, t3 = [p5[r][col * 4 + i][mid[col * 4 + i]]
                                  for i in range(4)]
                for row in range(4):
                    idx = col * 24 + row * 6
                    sh, sl = 28 - (row * 8), 24 - (row * 8)
                    x1h = p2[r][idx][(t0 >> sh) & 0xF][(t1 >> sh) & 0xF]
                    x2h = p2[r][idx + 1][(t2 >> sh) & 0xF][(t3 >> sh) & 0xF]
                    hi = p2[r][idx + 4][x1h][x2h]
                    x1l = p2[r][idx + 2][(t0 >> sl) & 0xF][(t1 >> sl) & 0xF]
                    x2l = p2[r][idx + 3][(t2 >> sl) & 0xF][(t3 >> sl) & 0xF]
                    lo = p2[r][idx + 5][x1l][x2l]
                    new[col * 4 + row] = ((hi << 4) | lo) & 0xFF
            state = new
        for i in range(16): state[i] = p4[i][state[i]]
        return bytes(state)

    def dl(bp):
        try:
            raw = list(base64.b64decode(bp))
            iv = bytearray(raw[:16]); ct = raw[16:]
            out = bytearray(); ks = None
            for j in range(len(ct)):
                off = j % 16
                if off == 0:
                    ks = wbe(iv)
                    for k in range(15, -1, -1):
                        iv[k] = (iv[k] + 1) & 0xFF
                        if iv[k] != 0: break
                out.append(ct[j] ^ ks[off])
            return out.decode("utf-8")
        except Exception:
            return None

    try:
        raw = fb.decode("utf-8", errors="ignore").strip()
        if raw.startswith("NPVTSUB1"): raw = raw[8:].strip()
        elif raw.startswith("NPVT1"): raw = raw[5:].strip()
        if not raw: return None
        pls = raw.split(",")
        if len(pls) < 2: return None
        d = dl(pls[1])
        if not d: return None
        try:
            rj = json.loads(d)
            if isinstance(rj, list) and rj: rj = rj[0]
        except Exception:
            rj = {"raw_data": d}
        return json.dumps(rj, indent=2, ensure_ascii=False)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
#  🔐  SSC DECODER (.ssc)
# ══════════════════════════════════════════════════════════════
_SN = struct.pack("<Q", 0xF7479D9F87F3D074)
_S1 = bytes.fromhex("c8a6a8ea102d5a0baf8fdb1b39cd615c0d07c1edcbde4e82cfdd309bc4587f6b")
_S2 = bytes.fromhex("7f9db48ffde449ad19f9ed44b8b27eee334ab4a85b972dca8ff20e4e8ed44e4e")
_S3 = bytes.fromhex("d39394517a48971f6e8555e994bee5bd835e5ab2f85fbd76bbd99800f32b967e")
_SM = {
    "a":"CONFIGS","b":"NOTE","c":"EXPIRY DATE","e":"CONFIGNAME",
    "f":"PAYLOAD ENABLED","g":"PAYLOAD","h":"PROXY","i":"PROXY PORT",
    "j":"TYPE","k":"PROXY ENABLED","l":"ADDRESS","m":"PORT",
    "n":"IS PREMIUM","o":"USERNAME","p":"PASSWORD","q":"TIMEOUT",
    "r":"PROTOCOL","s":"VERSION","t":"ENCRYPTION","u":"COMPRESSIONLEVEL",
    "v":"DNS","w":"NSSERVER","x":"PUBKEY","y":"ISDEFAULT","z":"LOCALPORT",
}
_SE = {"g", "h", "l", "o", "p", "v", "x", "i", "w"}


def decode_ssc(fb):
    def cd(k, n, d):
        c = ChaCha20.new(key=k, nonce=n); c.seek(64); return c.decrypt(d)

    def cs(b):
        if not b: return ""
        b = b.split(b"\x00")[0]
        try: return b.decode("utf-8")
        except UnicodeDecodeError as e:
            return b[:e.start].decode("utf-8", errors="ignore")

    def san(k, v):
        if not isinstance(v, str): return v
        v = "".join(c for c in v if ord(c) >= 32)
        if k in {"ADDRESS", "DNS", "H", "NSSERVER"}:
            if m := re.search(r'(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?', v):
                return m.group(0)
            return "".join(c for c in v if c.isalnum() or c in ".-_")
        if k in {"USERNAME", "PASSWORD"}:
            if v.isalnum(): return v
            if m := re.match(r'^[a-zA-Z0-9!@#$%^&*()._-]+', v):
                return m.group(0)
        if k == "PAYLOAD":
            return v.split("\x00")[0] if "[crlf]" in v else v.strip()
        return v.strip()

    def dn(uk):
        if not uk or len(uk) != 32: return None
        return bytes.fromhex(f"{uk[16:32][::-1]}68{uk[0:16]}")[:8]

    def cj(tb):
        if not tb: return None
        with contextlib.suppress(Exception):
            t = tb.decode("utf-8", errors="ignore").split("\x00")[0]
            if (s := t.find("{")) != -1 and (e := t.rfind("}")) != -1:
                cand = t[s:e + 1]
                try: return json.loads(cand)
                except json.JSONDecodeError as ex:
                    if ex.msg.startswith("Extra data"):
                        return json.loads(cand[:ex.pos])
        return None

    def pc(obj):
        if isinstance(cfgs := obj.get("a"), list):
            out = []
            for it in cfgs:
                if uk := it.get("b"):
                    if inn := dn(uk):
                        for f in _SE & it.keys():
                            ev = it[f]
                            if isinstance(ev, str) and len(ev) > 16:
                                with contextlib.suppress(Exception):
                                    db = cd(_S3, inn, bytes.fromhex(ev))
                                    pl = cs(db)
                                    it[f] = "".join(c for c in pl if c.isalnum()
                                                    or c in ".-:_") \
                                            if f in {"l", "v", "w", "h"} else pl
                ni = {}
                for k, v in it.items():
                    nk = _SM.get(k, k)
                    nv = san(nk, v)
                    if nv == "" and k in _SE: continue
                    ni[nk] = nv
                out.append(ni)
            obj["a"] = out
        return obj

    with contextlib.suppress(Exception):
        c = fb.decode("utf-8-sig", errors="ignore").strip()
        if c.startswith("ssc://"): c = c[6:][::-1]
        hx = "".join(c.split())
        if len(hx) % 2 != 0: return None
        l1 = cd(_S1, _SN, bytes.fromhex(hx))
        l1j = cj(l1)
        if not l1j: return None
        tgt = None
        if "c" in l1j and isinstance(l1j.get("a"), str):
            l2n = bytes.fromhex(l1j["a"][:16])
            l2 = cd(_S2, l2n, bytes.fromhex(l1j["c"]))
            l2j = cj(l2)
            if l2j:
                tgt = {_SM.get(k, k): san(_SM.get(k, k), v) if k != "a" else v
                       for k, v in l2j.items()}
        elif "a" in l1j and isinstance(l1j["a"], list):
            tgt = l1j
        if not tgt: return None
        fs = pc(tgt)
        out = {"Profiles": []}
        for i, cf in enumerate(fs.get("a", []), 1):
            out["Profiles"].append({
                "profile": i,
                "host": f"{cf.get('ADDRESS','')}:{cf.get('PORT','')}",
                "user_pass": f"{cf.get('USERNAME','')}:{cf.get('PASSWORD','')}",
                "payload": cf.get("PAYLOAD", ""),
                "proxy": f"{cf.get('PROXY','')}:{cf.get('PROXY PORT','')}",
                "dns": cf.get("DNS", ""),
            })
        return json.dumps(out, indent=2, ensure_ascii=False)
    return None


# ══════════════════════════════════════════════════════════════
#  ⚙️  HC DECODER (.hc) + UNLOCKER
# ══════════════════════════════════════════════════════════════
_HK = [bytes.fromhex(x) for x in [
    "2be4342943c6f91ff58987f41a1aafd179eeb4e053f5cea55b11d6a7db58bd7d",
    "3380aa278b744ba5b529a7f32fa803e48749280dae378345d9b526cf1dbce372",
    "cea9305c95168b162a335b137c61983b8df54e6375da01136547890f14c5fac3",
    "4beeace0e42bae8f29470cf40cf2dfacd5f4e1f751912bf52e803c8c85792193",
    "f8e5f6ebea90558eb32229da24fd0fb7d813091dafe89bb2954fda33b4c60f63",
    "81342f558a6273bac4548d473f54c4ffc7c41747dee81369acab9c787d41ab9c",
    "45635e6fc70486e2fd10d3c2b4780f02d0b4c5f4aa929fc54f86bb8fa4417944",
    "3d632a251c9820f2baf83e15498d27548fc67921cb437f8ce48505989378adea",
]]
_HR = [
    b"JN1k3YHc2.6_v235", b"JN1k3YHc_2.7_v71", b"JN1k3YHc2.7.ps69",
    b"JN1k3YHc2.7.6950", b"Jn1K3yHc2.8.ps08", b"Jn1K3yHc2.9.ps6c",
    b"Zk:L7>WKaiK*s9>D", b"!<f!&WIlM**R.B0X", b"b4a5opinx2uloec6",
]
_HJO = bytes([0xD5,0xD4,0xD3,0xD2,0xD1,0xD0,0xCF,0xCE,0xCD,0xCC,
              0xBD,0xBC,0xBB,0xBA,0xB9,0xB8,0xB7,0xB6,0xB5,0xB4])
_HJN = bytes([8,9,10,11,12,13,14,15,17,17,5,4,3,2,1,0,255,254,253,252])
_HT = {
    0:"payload",1:"proxy",2:"lockAllConfig",3:"blockedByRoot",
    4:"expiryTime",5:"noteEnabled",6:"notes",7:"sshField",
    8:"mobileDataAndLockProvider",9:"unlockUserAndPass",10:"ovpnConfig",
    11:"ovpnUserAndPass",12:"sni",13:"unlockUserAndPass2",
    14:"unknown14",15:"blockedByHwid",16:"cloudconfig",
    17:"psiphon",18:"name",19:"blockArea",20:"connectionMode",
    21:"blockedByPassword",22:"unknown22",23:"extraSniffer",
    24:"psiphon2",25:"v2rayEnabled",26:"v2rayConfig",27:"version",
    28:"slowdnsEnabled",29:"slowdnsServer",30:"slowdnsPublickey",31:"dnsResolver",
}
_HB = "⠁⠃⠉⠙⠑⠋⠛⠓⠊⠚⠅⠇⠍⠝⠕⠏⠟⠗⠎⠞⠥⠧⠺⠭⠽⠵⠼⠁⠼⠃⠼⠉⠼⠙⠼⠑⠼⠋⠼⠛⠼⠓⠼⠊⠼⠚"
_HSN = b"\xdb" * 8
_HRX = bytes(range(2, 22))


def _hc_raw(fb):
    def ch(s):
        if not s: return ""
        c = re.sub(r'[^0-9a-fA-F]', '', s)
        return "0" + c if len(c) % 2 else c

    def ih(s):
        return bool(s and len(s) >= 16 and re.fullmatch(r'^[0-9a-fA-F]+$', s))

    def ip(s, strict=False):
        if not s: return False
        if len(s) < 4: return True
        pc = sum(1 for c in s if c.isprintable() or c in "\t\n\r")
        return (pc / len(s)) > (0.90 if strict else 0.80)

    def z3a(data, iv):
        if not data: return ""
        o = bytearray()
        for m in re.finditer(r'(-?\d+)\.(-?\d+)', data):
            v1, v2 = int(m.group(1)) - iv, int(m.group(2)) - iv
            with contextlib.suppress(Exception):
                if (d := 1 << v2) != 0:
                    o.append((v1 // d) % 256)
        return o.decode("utf-8", errors="ignore")

    def dbr(ct):
        try:
            return bytes(
                (_HB.index(ct[i]) * 16 + _HB.index(ct[i + 1])) & 255
                for i in range(0, len(ct) - 1, 2)
            ).decode("utf-8")
        except ValueError:
            return ct

    def pc(rv, ssh=False):
        if not rv: return rv
        if ssh and rv[0] in _HB: rv = dbr(rv)
        pat = r'^([\w\.-]+):([\d\-]+)@(.+):(.+)$' if ssh else r'^([^:]+):(.+)$'
        if m := re.match(pat, rv):
            g = m.groups()
            ue, pe = g[-2:]
            ud = z3a(ue, len(re.findall(r'(-?\d+)\.(-?\d+)', ue)))
            pd = z3a(pe, len(re.findall(r'(-?\d+)\.(-?\d+)', pe)))
            fu, fp = ud or ue, pd or pe
            return f"{g[0]}:{g[1]}@{fu}:{fp}" if ssh else f"{fu}:{fp}"
        return rv

    def abc(raw, k, n=_HSN):
        if not raw: return ""
        with contextlib.suppress(Exception):
            d = bytes.fromhex(ch(raw))
            if len(d) > 16:
                c = ChaCha20.new(key=k, nonce=n); c.seek(64)
                return c.decrypt(d[:-16]).decode("utf-8", errors="ignore")
        return ""

    def rst(enc):
        with contextlib.suppress(Exception):
            b64 = bytes(b ^ _HRX[i % 20]
                        for i, b in enumerate(enc.encode("utf-8")))
            ct = base64.b64decode(b64)
            for k in _HR:
                with contextlib.suppress(Exception):
                    d = unpad(AES.new(k, AES.MODE_ECB).decrypt(ct),
                              AES.block_size)
                    ds = d.decode("utf-8", errors="ignore")
                    if "[splitConfig]" in ds: return ds
        return None

    def jkl(inp, new=False):
        if not inp: return inp
        k = _HJN if new else _HJO
        with contextlib.suppress(Exception):
            p = len(inp) % 4
            ps = inp + "=" * (4 - p) if p else inp
            d = bytearray(base64.b64decode(ps, validate=True))
            for i, b in enumerate(d):
                kk = k[i % 20]
                d[i] = (((b ^ 0xFF) & 0xCA) | (b & 0x35)) ^ \
                       (((kk ^ 0xFF) & 0xCA) | (kk & 0x35))
            return base64.b64decode(d.decode("utf-8"),
                                     validate=True).decode("utf-8")
        return inp

    def df(tok, nonce):
        if not tok or tok in {"true","false","lifeTime",
                              "[splitPsiphon][splitPsiphon]"} or \
                tok.startswith("<"):
            return tok
        cands = []
        chex_ = ch(tok)
        if ih(chex_) and len(chex_) >= 32:
            with contextlib.suppress(Exception):
                cands.append(bytes.fromhex(chex_))
        if len(tok) > 16:
            with contextlib.suppress(Exception):
                cands.append(tok.encode("latin-1"))
            with contextlib.suppress(Exception):
                cands.append(tok.encode("utf-8"))
        for db in (c for c in dict.fromkeys(cands) if len(c) > 16):
            ct = db[:-16]
            for ck in _HK:
                with contextlib.suppress(Exception):
                    c = ChaCha20.new(key=ck, nonce=nonce); c.seek(64)
                    ds = c.decrypt(ct).decode("utf-8", errors="ignore")
                    for nw in (True, False):
                        if (o := jkl(ds, nw)) and o != ds and ip(o): return o
                    if (ip(ds, True) and any(x in ds
                                             for x in ("HTTP","@",":","{"))) \
                            or ds.isalnum():
                        return ds
        for nw in (True, False):
            if (o := jkl(tok, nw)) != tok and ip(o): return o
        return tok

    def extract(fb, hk):
        with contextlib.suppress(Exception):
            kb = bytes.fromhex(hk); kl = len(kb)
            try:
                ed = fb.decode("utf-8", errors="ignore").encode(
                    "latin-1", errors="ignore")
            except Exception:
                ed = fb
            return bytes(b ^ kb[i % kl] for i, b in enumerate(ed)).decode("utf-8")
        return None

    hp = extract(fb, "e382e4b8adc386f09f9293")
    if not hp: return None
    with contextlib.suppress(Exception):
        outer = abc(hp, _HK[5])
        if not outer or not outer.startswith("{"): return None
        jo = json.loads(outer)
        if not isinstance(jo, dict): return None
        cfg = jo.get("cfg", {})
        isnew = isinstance(cfg, dict) and "content" in cfg
        meta, prot = {}, {}
        if isnew:
            for k, name in {'b': 'hwid', 'f': 'area'}.items():
                if v := str(jo.get(k) or cfg.get(k) or ""):
                    meta[name] = prot[name] = v
            tc, sp = cfg.get("content"), "[splitConfig]"
        else:
            oa = jo.get("a") if isinstance(jo.get("a"), dict) else {}
            for k, name in {'bb': 'hwid', 'e': 'password',
                            'fe': 'area', 'ed': 'provider'}.items():
                if v := (jo.get(k) if k == 'e' else oa.get(k)):
                    if dv := abc(str(v), _HK[7]):
                        meta[name] = prot[name] = dv
            tc, sp = jo.get("xy") or oa.get("xy"), jo.get("uv") or oa.get("uv")
        if not tc or not sp: return None
        th = lambda s: s.encode().hex() if s else ""
        h, p, pr, a = (meta.get("hwid"), meta.get("password"),
                       meta.get("provider"), meta.get("area"))
        dh = (th(h) * 2) if h and not any((p, pr, a)) else \
             (th(p) + th(h) + th(pr) + th(a))
        nonce = bytearray(_HSN)
        if dh:
            with contextlib.suppress(Exception):
                for i, b in enumerate(bytes.fromhex(dh)[:8]): nonce[i] = b
        xy = None
        if isnew:
            xy = rst(str(tc))
            if not xy:
                for k in _HK:
                    if (t := abc(str(tc), k)) and sp in t:
                        xy = t; break
        else:
            xy = abc(str(tc), _HK[1])
        if not xy: return None
        cd = {}
        for i, tok in enumerate(xy.split(str(sp))):
            if i in {22, 24}: continue
            lbl = _HT.get(i, f"field_{i}")
            o = tok
            if isnew:
                o = df(tok, nonce)
            else:
                if ih(tok): o = abc(tok, _HK[7], nonce)
                o = jkl(o, False)
            if i == 7: o = pc(o, True)
            elif i == 11: o = pc(o, False)
            if o:
                if isinstance(o, str):
                    o = o.replace(
                        "88a05e8772eac3e5703e0cd26c6e6f23de72fb09f7ee5a43283d1681f19d",
                        "")
                    with contextlib.suppress(Exception):
                        if o.startswith(("{", "[")): o = json.loads(o)
                if not (isinstance(o, str) and ih(o)):
                    cd[lbl] = o
        return {"protections": prot, "config": cd}
    return None


def decode_hc(fb):
    r = _hc_raw(fb)
    if not r: return None
    return json.dumps({"Protections": r["protections"],
                        "Config": r["config"]},
                       indent=2, ensure_ascii=False)


def unlock_hc(fb):
    r = _hc_raw(fb)
    if not r: return None
    cfg = dict(r["config"]); prot = dict(r["protections"]); orig = {}
    for k in ("lockAllConfig", "blockedByRoot", "blockedByHwid",
              "blockedByPassword", "mobileDataAndLockProvider",
              "blockArea", "expiryTime"):
        if k in cfg: orig[k] = cfg[k]
    cfg["lockAllConfig"] = False
    cfg["blockedByRoot"] = False
    cfg["blockedByHwid"] = False
    cfg["blockedByPassword"] = False
    cfg["mobileDataAndLockProvider"] = False
    cfg["blockArea"] = False
    cfg["expiryTime"] = 4070908800000
    if "unlockUserAndPass" in cfg: cfg["unlockUserAndPass"] = True
    if "unlockUserAndPass2" in cfg: cfg["unlockUserAndPass2"] = True
    prot.pop("hwid", None); prot.pop("password", None)
    prot.pop("area", None); prot.pop("provider", None)
    return {"original_locks": orig, "config": cfg, "protections": prot}


# ══════════════════════════════════════════════════════════════
#  🗂️  REGISTRY
# ══════════════════════════════════════════════════════════════
DECODERS = {
    "dark":  ("🌑 DarkTunnel",     "DarkTunnel",     decode_dark),
    "ehi":   ("🔒 HTTP Injector",  "HTTP Injector",  decode_ehi),
    "npvt":  ("🎭 NPVT Whitebox",  "NPVT Whitebox",  decode_npvt),
    "ssc":   ("🔐 SC VPN",         "SC VPN",         decode_ssc),
    "hc":    ("⚙️ HTTP Custom",    "HTTP Custom",    decode_hc),
}


def detect_and_decode(fb, filename):
    fl = (filename or "").lower()
    ext = Path(fl).suffix.lstrip(".")

    if ext in DECODERS:
        label, _, fn = DECODERS[ext]
        try:
            o = fn(fb)
            if o: return label, o
        except Exception as e:
            logger.warning("ext-decode fail [%s]: %s", ext, e)

    with contextlib.suppress(Exception):
        h = fb[:200].decode("utf-8", errors="ignore").strip()
        hl = h.lower()
        if h.startswith(("NPVTSUB1", "NPVT1")):
            if o := decode_npvt(fb): return DECODERS["npvt"][0], o
        if hl.startswith("ssc://"):
            if o := decode_ssc(fb): return DECODERS["ssc"][0], o
        if "encryptedlockedconfig" in hl:
            if o := decode_dark(fb): return DECODERS["dark"][0], o
        if hl.startswith("{") and ("cfg" in hl or '"xy"' in hl):
            if o := decode_hc(fb): return DECODERS["hc"][0], o

    for k, (label, _, fn) in DECODERS.items():
        if k == ext: continue
        try:
            o = fn(fb)
            if o: return label, o
        except Exception:
            continue

    return None, None


# ══════════════════════════════════════════════════════════════
#  🧠  SMART EXTRACTION
# ══════════════════════════════════════════════════════════════
UUID_RE = re.compile(
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)
IP_RE = re.compile(r'(?:\d{1,3}\.){3}\d{1,3}')


def deep_flatten(obj, out=None, prefix=""):
    if out is None: out = {}
    if len(out) > 500: return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            kk = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                deep_flatten(v, out, kk)
            else:
                sv = str(v).strip()
                if sv and not kk.startswith("_"):
                    out.setdefault(kk, sv)
                    out.setdefault(str(k), sv)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            deep_flatten(v, out, f"{prefix}[{i}]")
    return out


def find_first(flat, names, maxlen=500):
    for n in names:
        for fk, fv in flat.items():
            if n.lower() in fk.lower() and fv and len(str(fv)) <= maxlen:
                return fv
    return None


def extract_fields(decoded_json):
    try:
        data = json.loads(decoded_json)
    except Exception:
        return {}

    flat = deep_flatten(data, {})
    blob = json.dumps(data, ensure_ascii=False)
    out = {}

    if m := UUID_RE.search(blob):
        out["uuid"] = m.group(0)

    out["app"] = find_first(flat, ["app", "appName"]) or ""
    out["name"] = find_first(flat, ["name", "note", "configName", "ps"]) or ""

    ips = []
    for fk, fv in flat.items():
        if any(x in fk.lower() for x in ("address", "add", "host", "ip",
                                          "server", "remote")):
            if m := IP_RE.search(str(fv)):
                ips.append(m.group(0))
    if ips: out["ip"] = ips[0]

    for fk, fv in flat.items():
        if fk.lower() in ("host", "add", "address", "sni", "servername"):
            s = str(fv).strip()
            if s and not IP_RE.match(s) and "." in s and len(s) < 200:
                out["host"] = s
                break
    if "host" not in out:
        for fk, fv in flat.items():
            if "host" in fk.lower() or "sni" in fk.lower():
                s = str(fv).strip()
                if s and "." in s and len(s) < 200 and not IP_RE.match(s):
                    out["host"] = s
                    break

    out["port"] = find_first(flat, ["port", "serverPort", "sshPort"],
                              maxlen=10) or ""

    px = find_first(flat, ["proxy"])
    pxp = find_first(flat, ["proxyPort"], maxlen=10)
    if px:
        out["proxy"] = f"{px}:{pxp}" if pxp else str(px)

    net = find_first(flat, ["network", "net"], maxlen=20)
    if net:
        netmap = {"ws": "WebSocket (WS)", "grpc": "gRPC", "tcp": "TCP",
                  "http": "HTTP", "h2": "HTTP/2", "quic": "QUIC"}
        out["network"] = netmap.get(str(net).lower(), str(net))

    out["path"] = find_first(flat, ["path"], maxlen=300)

    sec = find_first(flat, ["security", "scy", "encryption"], maxlen=30)
    if sec: out["security"] = str(sec)

    tls = find_first(flat, ["tls"], maxlen=20)
    if tls: out["tls"] = str(tls)

    out["payload"] = find_first(flat, ["payload"], maxlen=3000)
    out["sni"] = find_first(flat, ["sni", "servername"], maxlen=200)
    out["ssh"] = find_first(flat, ["sshField"], maxlen=300)
    out["note"] = find_first(flat, ["note", "notes"], maxlen=500)
    out["uuid_alt"] = find_first(flat, ["uuid"], maxlen=60)

    if not out.get("uuid") and out.get("uuid_alt"):
        out["uuid"] = out["uuid_alt"]

    return {k: v for k, v in out.items() if v}


def build_vmess_link(decoded_json):
    try:
        data = json.loads(decoded_json)
    except Exception:
        return None
    sm = extract_fields(decoded_json)
    if not sm.get("uuid"): return None
    if not (sm.get("ip") or sm.get("host")): return None

    try:
        net = sm.get("network") or "ws"
        net_short = "ws" if "WS" in net else (
            "grpc" if "gRPC" in net else net.lower().replace(" ", "")
        )
        vm = {
            "v": "2",
            "ps": sm.get("name") or "VPN-Decoder",
            "add": sm.get("ip") or sm.get("host") or "",
            "port": str(sm.get("port") or "443"),
            "id": sm.get("uuid") or "",
            "aid": "0",
            "scy": sm.get("security") or "auto",
            "net": net_short,
            "type": "none",
            "host": sm.get("host") or sm.get("sni") or "",
            "path": sm.get("path") or "/",
            "tls": "tls" if sm.get("tls") else "",
            "sni": sm.get("sni") or "",
        }
        raw = json.dumps(vm, ensure_ascii=False, separators=(",", ":"))
        return "vmess://" + base64.b64encode(raw.encode()).decode()
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
#  📋  PREMIUM MESSAGE BUILDER
# ══════════════════════════════════════════════════════════════
def build_config_message(decoded_json, decoder_label):
    sm = extract_fields(decoded_json)

    L = []
    L.append("╔══════════════════════════════════╗")
    L.append("║    💎 <b>CONFIG DECRYPTED</b> 💎     ║")
    L.append("║      <i>VPN DECODER PRO v9</i>       ║")
    L.append("╚══════════════════════════════════╝")
    L.append("")
    L.append(f"🎯 <b>Format</b>  : {esc(decoder_label)}")
    L.append(f"🕐 <b>Time</b>    : <code>{bd_time()}</code>")
    L.append("")
    L.append("🌐 <b>SERVER INFORMATION</b>")
    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if sm.get("app"):
        L.append(f"  📦 <b>App</b>     : <code>{esc(sm['app'])}</code>")

    if sm.get("uuid"):
        L.append(f"  📡 <b>Type</b>    : <b>VMESS</b>")

    if sm.get("name"):
        L.append(f"  📝 <b>Name</b>    : <code>{esc(sm['name'])}</code>")

    if sm.get("ip"):
        L.append(f"  🌍 <b>IP</b>      : <code>{esc(sm['ip'])}</code>")

    if sm.get("host"):
        L.append(f"  ☁️ <b>Host</b>    : <code>{esc(sm['host'])}</code>")

    if sm.get("port"):
        L.append(f"  🔌 <b>Port</b>    : <b>{esc(sm['port'])}</b>")

    if sm.get("proxy"):
        L.append(f"  🧩 <b>Proxy</b>   : <code>{esc(sm['proxy'])}</code>")

    if sm.get("network"):
        L.append(f"  🛰 <b>Network</b> : <b>{esc(sm['network'])}</b>")

    if sm.get("path"):
        L.append(f"  📂 <b>Path</b>    : <code>{esc(sm['path'])}</code>")

    if sm.get("security"):
        L.append(f"  🔐 <b>Security</b>: <b>{esc(sm['security'])}</b>")

    if sm.get("sni"):
        L.append(f"  🎯 <b>SNI</b>     : <code>{esc(sm['sni'])}</code>")

    if sm.get("uuid"):
        L.append("")
        L.append("🆔 <b>UUID</b>")
        L.append(f"<code>{esc(sm['uuid'])}</code>")

    if sm.get("ssh"):
        L.append("")
        L.append("🔑 <b>SSH FIELD</b>")
        L.append(f"<code>{esc(sm['ssh'][:500])}</code>")

    if sm.get("payload"):
        L.append("")
        L.append("📨 <b>PAYLOAD</b>")
        L.append(f"<code>{esc(sm['payload'][:800])}</code>")

    if len(L) <= 12:
        try:
            data = json.loads(decoded_json)
            L.append("")
            L.append("📄 <b>RAW OUTPUT</b>")
            L.append("<pre>" + esc(json.dumps(data, indent=2,
                                              ensure_ascii=False)[:3500]) + "</pre>")
        except Exception:
            L.append("")
            L.append("<code>" + esc(decoded_json[:3500]) + "</code>")

    L.append("")
    vlink = build_vmess_link(decoded_json)
    if vlink:
        L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
        L.append("🔗 <b>READY V2RAY LINK</b>")
        L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
        L.append(f"<code>{esc(vlink[:800])}</code>")
        if len(vlink) > 800:
            L.append("<i>...full link in copy button</i>")

    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append(f"💎 <b>{BRAND}</b> · v{BRAND_VER}")
    L.append(f"👤 {BRAND_OWN} · 📢 {BRAND_CH}")

    return "\n".join(L)


# ══════════════════════════════════════════════════════════════
#  🎛️  PREMIUM INLINE BUTTONS
# ══════════════════════════════════════════════════════════════
_COPY_STORE = {}


def _cleanup_copy_store():
    now = time.time()
    expired = [k for k, v in _COPY_STORE.items()
               if now - v.get("ts", 0) > COPY_TTL]
    for k in expired:
        _COPY_STORE.pop(k, None)


def build_premium_buttons(decoded_json, sm):
    uid_key = int(time.time() * 1000) % 100000000
    rows = []

    vlink = build_vmess_link(decoded_json)
    if vlink:
        row1 = [InlineKeyboardButton("📋 Copy V2Ray Link",
                                       callback_data=f"cplink_{uid_key}")]
        if HAVE_QR:
            row1.append(InlineKeyboardButton("📱 QR Code",
                                              callback_data=f"qrlink_{uid_key}"))
        rows.append(row1)

    row2 = []
    if sm.get("uuid"):
        row2.append(InlineKeyboardButton("🆔 UUID",
                                           callback_data=f"cpuuid_{uid_key}"))
    if sm.get("payload"):
        row2.append(InlineKeyboardButton("📨 Payload",
                                           callback_data=f"cppay_{uid_key}"))
    if sm.get("host") or sm.get("ip"):
        row2.append(InlineKeyboardButton("🌐 Host",
                                           callback_data=f"cphost_{uid_key}"))
    if row2:
        rows.append(row2)

    rows.append([
        InlineKeyboardButton("📦 Full Config",
                             callback_data=f"cpfull_{uid_key}"),
        InlineKeyboardButton("📄 .txt File",
                             callback_data=f"cptxt_{uid_key}"),
    ])

    rows.append([
        InlineKeyboardButton("👤 Owner", url=f"https://t.me/{BRAND_OWN[1:]}"),
        InlineKeyboardButton("📢 Channel", url=f"https://t.me/{BRAND_CH[1:]}"),
    ])

    _COPY_STORE[uid_key] = {
        "full": decoded_json,
        "link": vlink or "",
        "uuid": sm.get("uuid", ""),
        "payload": sm.get("payload", ""),
        "note": sm.get("note") or sm.get("name", ""),
        "host": sm.get("host") or sm.get("ip", ""),
        "ts": time.time(),
    }

    if len(_COPY_STORE) > 200:
        _cleanup_copy_store()

    return InlineKeyboardMarkup(rows), uid_key


# ══════════════════════════════════════════════════════════════
#  🎛️  KEYBOARDS
# ══════════════════════════════════════════════════════════════
def reply_menu(uid=None):
    kb = [
        [KeyboardButton("👤  Profile"),   KeyboardButton("📊  Stats")],
        [KeyboardButton("🗂️  Formats"),   KeyboardButton("ℹ️  Help")],
        [KeyboardButton("📞  Support")],
    ]
    if uid and is_owner(uid):
        kb.append([KeyboardButton("🛡️  Admin Panel")])
    return ReplyKeyboardMarkup(
        kb,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=False,
    )


# ══════════════════════════════════════════════════════════════
#  ⏱️  RATE LIMIT
# ══════════════════════════════════════════════════════════════
_last = {}
_burst = {}


def rate_ok(uid):
    if is_owner(uid): return True, 0
    now = time.time()
    last = _last.get(uid, 0)
    if now - last < RATE_MIN_GAP:
        return False, int(RATE_MIN_GAP - (now - last)) + 1
    q = _burst.setdefault(uid, [])
    q[:] = [t for t in q if now - t < RATE_BURST_WIN]
    if len(q) >= RATE_BURST_MAX:
        return False, int(RATE_BURST_WIN - (now - q[0])) + 1
    _last[uid] = now
    q.append(now)
    return True, 0


async def safe_edit(msg, text, **kw):
    with contextlib.suppress(Exception):
        await msg.edit_text(text, **kw)


# ══════════════════════════════════════════════════════════════
#  🧠  TEXT CONFIG DETECTION
# ══════════════════════════════════════════════════════════════
def looks_like_config(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 16:
        return False

    tl = t.lower()

    # 1) Known URI schemes
    if any(tl.startswith(p) for p in (
        "vmess://", "vless://", "trojan://", "ss://",
        "ssc://", "npvt1,", "npvtsub1,", "dark://",
        "hysteria://", "hy2://", "tuic://", "wireguard://",
    )):
        return True

    # 2) Base64 blob
    if len(t) > 80 and " " not in t:
        b64 = sum(1 for c in t if c.isalnum() or c in "+/=_-")
        if b64 / len(t) > 0.9:
            return True

    # 3) JSON config
    if (t.startswith("{") and t.endswith("}")) or \
       (t.startswith("[") and t.endswith("]")):
        if any(k in tl for k in (
            '"cfg"', '"xy"', '"encryptedlockedconfig"',
            '"configs"', '"payload"', '"uuid"', '"outbounds"',
            '"inbounds"', '"server"', '"address"', '"network"'
        )):
            return True

    # 4) Hex
    if len(t) >= 64 and re.fullmatch(r"[0-9a-fA-F\s]+", t):
        return True

    return False


async def try_text_decode(upd, ctx, raw_text):
    uid = upd.effective_user.id
    ok, wait = rate_ok(uid)
    if not ok:
        await upd.message.reply_text(
            f"⏳ <b>Wait {wait}s</b>", parse_mode=ParseMode.HTML)
        return

    status = await upd.message.reply_text(
        "🔍 <b>Analyzing text...</b>",
        parse_mode=ParseMode.HTML)

    candidates = [raw_text.encode("utf-8")]

    # Base64 decode
    with contextlib.suppress(Exception):
        raw = raw_text.strip()
        pad = raw + "=" * ((4 - len(raw) % 4) % 4)
        dec = base64.b64decode(pad, validate=False)
        if len(dec) > 16:
            candidates.append(dec)

    # Hex decode
    with contextlib.suppress(Exception):
        clean = re.sub(r"\s+", "", raw_text)
        if re.fullmatch(r"[0-9a-fA-F]+", clean) and len(clean) % 2 == 0:
            candidates.append(bytes.fromhex(clean))

    # URL decode
    with contextlib.suppress(Exception):
        candidates.append(urllib.parse.unquote(raw_text).encode("utf-8"))

    # Strip prefixes for non-scheme configs
    with contextlib.suppress(Exception):
        if raw_text.lower().startswith("npvtsub1,"):
            candidates.append(raw_text[9:].encode("utf-8"))

    for fb in candidates:
        label, decoded = await asyncio.to_thread(
            detect_and_decode, fb, "")
        if decoded:
            await asyncio.to_thread(
                db_log, uid, "text", "txt", label, True)
            with contextlib.suppress(Exception):
                await status.delete()
            await send_result(upd, decoded, label)
            return

    await safe_edit(
        status,
        "❌ <b>Could not decode</b>\n\n"
        "Supported text formats:\n"
        "  • <code>vmess://...</code>\n"
        "  • <code>vless://...</code>\n"
        "  • <code>trojan://...</code>\n"
        "  • <code>ssc://...</code>\n"
        "  • <code>NPVTSUB1,...</code>\n"
        "  • Base64 encoded config\n"
        "  • JSON config\n"
        "  • Hex encoded config",
        parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════
#  🏠  COMMANDS
# ══════════════════════════════════════════════════════════════
async def cmd_start(upd, ctx):
    uid = upd.effective_user.id
    u = upd.effective_user
    with contextlib.suppress(Exception):
        await asyncio.to_thread(db_upsert_user, uid,
                                 u.username or "", u.first_name or "")

    text = (
        "╔══════════════════════════════════╗\n"
        "║   💎  <b>V P N   D E C O D E R</b>  💎    ║\n"
        f"║         <b>P R O</b>  ·  <i>v{BRAND_VER}</i>          ║\n"
        "╚══════════════════════════════════╝\n\n"
        f"👋 Welcome, <b>{esc(u.first_name)}</b>\n"
        f"🕐 <code>{bd_time()}</code>\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃   🎯  <b>SUPPORTED FORMATS</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "  🌑 <b>DarkTunnel</b>      <code>.dark</code>\n"
        "  🔒 <b>HTTP Injector</b>   <code>.ehi</code>\n"
        "  🎭 <b>NPVT Whitebox</b>   <code>.npvt</code>\n"
        "  🔐 <b>SC VPN</b>          <code>.ssc</code>\n"
        "  ⚙️ <b>HTTP Custom</b>     <code>.hc</code>\n\n"
        "💡 <i>Send a config FILE or paste LINK/TEXT</i>\n"
        "🎁 Auto-detect + auto-unlock\n\n"
        f"⚡ {BRAND_CH} · 👤 {BRAND_OWN}"
    )
    await upd.message.reply_text(
        text, reply_markup=reply_menu(uid),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True)


async def cmd_help(upd, ctx):
    text = (
        "ℹ️ <b>HELP GUIDE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📖 <b>How to use:</b>\n\n"
        "  1️⃣ Send a config file OR\n"
        "  2️⃣ Paste a config link/text\n"
        "  3️⃣ Auto-detect + auto-unlock\n"
        "  4️⃣ Get clean output + V2Ray link\n"
        "  5️⃣ Tap copy buttons / QR Code\n\n"
        "📝 <b>Text formats:</b>\n"
        "  <code>vmess://...</code>\n"
        "  <code>vless://...</code>\n"
        "  <code>trojan://...</code>\n"
        "  <code>ssc://...</code>\n"
        "  <code>NPVTSUB1,...</code>\n"
        "  Base64 / JSON / Hex\n\n"
        "🎛️ <b>Commands:</b>\n\n"
        "  /start    · Main menu\n"
        "  /help     · This guide\n"
        "  /formats  · Supported files\n"
        "  /stats    · Your stats\n"
        "  /myid     · Your ID"
    )
    await upd.message.reply_text(text, parse_mode=ParseMode.HTML,
                                  disable_web_page_preview=True)


async def cmd_formats(upd, ctx):
    text = (
        "🗂️ <b>SUPPORTED FORMATS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📁 <b>File formats:</b>\n"
        "  🌑 <code>.dark</code>      DarkTunnel\n"
        "  🔒 <code>.ehi</code>       HTTP Injector\n"
        "  🎭 <code>.npvt</code>      NPVT Whitebox\n"
        "  🔐 <code>.ssc</code>       SC VPN\n"
        "  ⚙️ <code>.hc</code>        HTTP Custom\n\n"
        "📝 <b>Text formats:</b>\n"
        "  <code>vmess://</code>  <code>vless://</code>\n"
        "  <code>trojan://</code> <code>ssc://</code>\n"
        "  <code>NPVTSUB1,</code>\n"
        "  Base64 / JSON / Hex\n\n"
        "💎 Auto-unlock enabled for all"
    )
    await upd.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_stats(upd, ctx):
    uid = upd.effective_user.id
    st = await asyncio.to_thread(db_user, uid)
    if not st:
        await upd.message.reply_text("❌ No data yet — send a file!")
        return
    tot = st["total_success"] + st["total_failed"]
    sr = round(st["total_success"] / tot * 100, 1) if tot else 0
    u = upd.effective_user
    bar = "█" * int(sr / 10) + "░" * (10 - int(sr / 10))
    text = (
        "╔══════════════════════════════════╗\n"
        "║       👤 <b>MY PROFILE</b>           ║\n"
        "╚══════════════════════════════════╝\n\n"
        f"  🧑 <b>{esc(u.first_name)}</b>\n"
        f"  🆔 <code>{uid}</code>\n\n"
        "📊 <b>STATISTICS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  ✅ Success : <b>{st['total_success']}</b>\n"
        f"  ❌ Failed  : <b>{st['total_failed']}</b>\n"
        f"  🔓 Unlocks : <b>{st['total_unlocks']}</b>\n"
        f"  📦 Total   : <b>{tot}</b>\n\n"
        f"  🎯 Success Rate: <b>{sr}%</b>\n"
        f"  <code>{bar}</code>\n\n"
        f"  📅 Joined : <b>{st['joined_at']}</b>"
    )
    await upd.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_contact(upd, ctx):
    text = (
        "📞 <b>SUPPORT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  👤 Owner   : <b>{BRAND_OWN}</b>\n"
        f"  📢 Channel : <b>{BRAND_CH}</b>\n\n"
        "💬 <i>Feel free to reach out!</i>"
    )
    await upd.message.reply_text(text, parse_mode=ParseMode.HTML,
                                  disable_web_page_preview=True)


async def cmd_myid(upd, ctx):
    await upd.message.reply_text(
        f"🆔 Your ID:\n\n<code>{upd.effective_user.id}</code>",
        parse_mode=ParseMode.HTML)


async def cmd_admin(upd, ctx):
    uid = upd.effective_user.id
    if not is_owner(uid):
        await upd.message.reply_text("⛔ Access denied.")
        return

    stats = await asyncio.to_thread(db_admin_stats)
    text = build_admin_text(stats)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 All Users", callback_data="adm_users"),
         InlineKeyboardButton("🔄 Refresh", callback_data="adm_refresh")],
        [InlineKeyboardButton("📥 Export CSV", callback_data="adm_csv")],
    ])

    await upd.message.reply_text(
        text, reply_markup=kb,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True)


def build_admin_text(stats):
    L = []
    L.append("╔══════════════════════════════════╗")
    L.append("║      🛡️ <b>ADMIN DASHBOARD</b> 🛡️       ║")
    L.append("╚══════════════════════════════════╝")
    L.append("")
    L.append("📊 <b>OVERVIEW</b>")
    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append(f"  👥 Users         : <b>{stats['total_users']}</b>")
    L.append(f"  📦 Total Files   : <b>{stats['total_files']}</b>")
    L.append(f"  📅 Today Files   : <b>{stats['today_files']}</b>")
    L.append(f"  🔓 Total Unlocks : <b>{stats['total_unlocks']}</b>")
    L.append(f"  ✅ Success       : <b>{stats['total_success']}</b>")
    L.append(f"  ❌ Failed        : <b>{stats['total_failed']}</b>")

    tot = stats['total_success'] + stats['total_failed']
    if tot > 0:
        sr = round(stats['total_success'] / tot * 100, 1)
        L.append(f"  🎯 Success Rate  : <b>{sr}%</b>")

    L.append("")
    L.append("🏆 <b>TOP UNLOCKERS</b>")
    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    for i, r in enumerate(stats["users"][:10], 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"  {i}."
        fname = r["first_name"] or "—"
        L.append(
            f"{medal} <b>{esc(fname)}</b> (<code>{r['uid']}</code>)\n"
            f"     🔓 <b>{r['total_unlocks']}</b> | "
            f"✅ {r['total_success']} | ❌ {r['total_failed']}"
        )

    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append(f"🕐 <code>{bd_time()}</code>")

    return "\n".join(L)


# ══════════════════════════════════════════════════════════════
#  📤  RESULT SENDER
# ══════════════════════════════════════════════════════════════
async def send_result(upd, decoded, label):
    msg = build_config_message(decoded, label)
    sm = extract_fields(decoded)
    kb, _ = build_premium_buttons(decoded, sm)

    if len(msg) <= 4000:
        await upd.message.reply_text(
            msg, reply_markup=kb,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True)
    else:
        mid = msg.rfind("🔗 <b>READY V2RAY LINK</b>")
        if mid < 0: mid = 3900
        await upd.message.reply_text(
            msg[:mid], parse_mode=ParseMode.HTML,
            disable_web_page_preview=True)
        await upd.message.reply_text(
            msg[mid:], reply_markup=kb,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True)


# ══════════════════════════════════════════════════════════════
#  📄  DOCUMENT HANDLER
# ══════════════════════════════════════════════════════════════
async def process_document(upd, ctx, doc, uid, username):
    fn = doc.file_name or "unknown"

    ok, wait = rate_ok(uid)
    if not ok:
        await upd.message.reply_text(
            f"⏳ <b>Wait {wait}s</b>", parse_mode=ParseMode.HTML)
        return

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await upd.message.reply_text(
            f"❌ File too large (max {MAX_FILE_SIZE // (1024*1024)} MB)")
        return

    status = await upd.message.reply_text(
        "🔍 <b>Decrypting...</b>",
        parse_mode=ParseMode.HTML)

    try:
        tf = await ctx.bot.get_file(doc.file_id)
        fb = bytes(await tf.download_as_bytearray())

        label, decoded = await asyncio.to_thread(detect_and_decode, fb, fn)

        if not decoded:
            await asyncio.to_thread(db_log, uid, fn,
                                     Path(fn).suffix.lstrip("."),
                                     "unknown", False)
            await safe_edit(
                status,
                f"❌ <b>DECRYPT FAILED</b>\n\n"
                f"📄 <code>{esc(fn)}</code>\n\n"
                f"⚠️ Unsupported or corrupted file",
                parse_mode=ParseMode.HTML)
            return

        ext = Path(fn).suffix.lstrip(".").lower()
        unlocked_raw = None
        if ext == "hc":
            with contextlib.suppress(Exception):
                unlocked_raw = await asyncio.to_thread(unlock_hc, fb)

        await asyncio.to_thread(db_log, uid, fn, ext, label, True,
                                 bool(unlocked_raw))

        with contextlib.suppress(Exception):
            await status.delete()

        await send_result(upd, decoded, label)

        logger.info("OK uid=%s file=%s decoder=%s", uid, fn, label)

    except Exception as e:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(db_log, uid, fn,
                                     Path(fn).suffix.lstrip("."),
                                     "error", False)
        logger.exception("decode err uid=%s", uid)
        await safe_edit(status, f"⚠️ Error: <code>{esc(e)}</code>",
                        parse_mode=ParseMode.HTML)


async def on_doc(upd, ctx):
    u = upd.effective_user
    with contextlib.suppress(Exception):
        await asyncio.to_thread(db_upsert_user, u.id,
                                 u.username or "", u.first_name or "")
    await process_document(upd, ctx, upd.message.document,
                            u.id, u.first_name or "User")


# ══════════════════════════════════════════════════════════════
#  💬  TEXT HANDLER
# ══════════════════════════════════════════════════════════════
async def on_text(upd, ctx):
    t = (upd.message.text or "").strip()
    if not t:
        return

    # Menu buttons
    if t in ("👤  Profile", "📊  Stats"):  await cmd_stats(upd, ctx); return
    if t == "🗂️  Formats":                  await cmd_formats(upd, ctx); return
    if t == "ℹ️  Help":                     await cmd_help(upd, ctx); return
    if t == "📞  Support":                  await cmd_contact(upd, ctx); return
    if t == "🛡️  Admin Panel":              await cmd_admin(upd, ctx); return

    # Smart config text detection
    if looks_like_config(t):
        await try_text_decode(upd, ctx, t)
        return

    # Fallback
    await upd.message.reply_text(
        "📎 <b>Send a VPN config file</b>\n"
        "or paste a config <b>link/text</b>.\n\n"
        "✅ Supported text:\n"
        "  <code>vmess://...</code>\n"
        "  <code>vless://...</code>\n"
        "  <code>trojan://...</code>\n"
        "  <code>ssc://...</code>\n"
        "  <code>NPVTSUB1,...</code>\n"
        "  Base64 / JSON / Hex\n\n"
        "🤖 Auto-detect enabled",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True)


# ══════════════════════════════════════════════════════════════
#  🎛️  CALLBACK
# ══════════════════════════════════════════════════════════════
async def on_cb(upd, ctx):
    q = upd.callback_query
    await q.answer()
    d = q.data
    uid = q.from_user.id

    if "_" not in d: return
    action, key_s = d.rsplit("_", 1)
    try:
        key = int(key_s)
    except ValueError:
        key = 0

    # Admin actions
    if action == "adm_refresh":
        if not is_owner(uid):
            await q.answer("⛔ Denied", show_alert=True); return
        stats = await asyncio.to_thread(db_admin_stats)
        text = build_admin_text(stats)
        with contextlib.suppress(Exception):
            await q.edit_message_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=q.message.reply_markup,
                disable_web_page_preview=True)
        await q.answer("✅ Refreshed")
        return

    if action == "adm_users":
        if not is_owner(uid):
            await q.answer("⛔ Denied", show_alert=True); return
        stats = await asyncio.to_thread(db_admin_stats)
        L = ["👥 <b>ALL USERS</b>",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for i, r in enumerate(stats["users"], 1):
            L.append(
                f"\n<b>{i}.</b> <b>{esc(r['first_name'] or '—')}</b>\n"
                f"   🆔 <code>{r['uid']}</code>\n"
                f"   📛 @{esc(r['username'] or '—')}\n"
                f"   🔓 {r['total_unlocks']} | "
                f"✅ {r['total_success']} | ❌ {r['total_failed']}"
            )
        text = "\n".join(L)
        for i in range(0, len(text), 3800):
            await ctx.bot.send_message(uid, text[i:i+3800],
                                        parse_mode=ParseMode.HTML)
        await q.answer("✅ Sent")
        return

    if action == "adm_csv":
        if not is_owner(uid):
            await q.answer("⛔ Denied", show_alert=True); return
        stats = await asyncio.to_thread(db_admin_stats)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Rank", "UID", "Username", "First Name",
                    "Success", "Failed", "Unlocks", "Joined"])
        for i, r in enumerate(stats["users"], 1):
            w.writerow([i, r["uid"], r["username"], r["first_name"],
                        r["total_success"], r["total_failed"],
                        r["total_unlocks"], r["joined_at"]])
        fb = io.BytesIO(buf.getvalue().encode("utf-8"))
        fb.name = f"users_{int(time.time())}.csv"
        await ctx.bot.send_document(uid, fb,
                                     caption="📊 <b>Users Export</b>",
                                     parse_mode=ParseMode.HTML)
        await q.answer("✅ CSV sent")
        return

    # Copy actions
    entry = _COPY_STORE.get(key)
    if not entry:
        await q.answer("⚠️ Expired. Send file again.", show_alert=True)
        return

    try:
        if action == "cplink":
            v = entry["link"] or "N/A"
            await ctx.bot.send_message(
                uid, f"📋 <b>V2Ray Link</b>\n\n<code>{esc(v)}</code>",
                parse_mode=ParseMode.HTML)
            await q.answer("✅ Sent!")

        elif action == "cpuuid":
            v = entry["uuid"] or "N/A"
            await ctx.bot.send_message(
                uid, f"🆔 <b>UUID</b>\n\n<code>{esc(v)}</code>",
                parse_mode=ParseMode.HTML)
            await q.answer("✅ Sent!")

        elif action == "cppay":
            v = entry["payload"] or "N/A"
            await ctx.bot.send_message(
                uid, f"📨 <b>Payload</b>\n\n<code>{esc(v[:3800])}</code>",
                parse_mode=ParseMode.HTML)
            await q.answer("✅ Sent!")

        elif action == "cphost":
            v = entry["host"] or "N/A"
            await ctx.bot.send_message(
                uid, f"🌐 <b>Host</b>\n\n<code>{esc(v)}</code>",
                parse_mode=ParseMode.HTML)
            await q.answer("✅ Sent!")

        elif action == "cpnote":
            v = entry["note"] or "N/A"
            await ctx.bot.send_message(
                uid, f"📝 <b>Note</b>\n\n<code>{esc(v)}</code>",
                parse_mode=ParseMode.HTML)
            await q.answer("✅ Sent!")

        elif action == "qrlink":
            v = entry["link"]
            if not v:
                await q.answer("❌ No link available", show_alert=True)
                return
            if not HAVE_QR:
                await q.answer("⚠️ qrcode not installed", show_alert=True)
                return
            try:
                img = qrcode.make(v)
                buf = io.BytesIO()
                img.save(buf, "PNG")
                buf.seek(0)
                await ctx.bot.send_photo(
                    uid, buf,
                    caption="📱 <b>Scan to import V2Ray</b>",
                    parse_mode=ParseMode.HTML)
                await q.answer("✅ QR sent!")
            except Exception as e:
                await q.answer(f"❌ QR error: {e}", show_alert=True)

        elif action == "cpfull":
            await q.answer("📤 Sending...")
            buf = io.BytesIO(entry["full"].encode("utf-8"))
            buf.name = "full_config.json"
            await ctx.bot.send_document(
                uid, buf, caption="📦 <b>Full Config</b>",
                parse_mode=ParseMode.HTML)

        elif action == "cptxt":
            await q.answer("📤 Sending...")
            txt = (f"VPN Decoder PRO - Decrypted Config\n"
                   f"{'=' * 50}\n\n")
            txt += entry["full"]
            buf = io.BytesIO(txt.encode("utf-8"))
            buf.name = "config_decrypted.txt"
            await ctx.bot.send_document(
                uid, buf, caption="📄 <b>Config as Text</b>",
                parse_mode=ParseMode.HTML)

        else:
            await q.answer("Unknown action")

    except Exception as e:
        logger.warning("cb fail: %s", e)
        await q.answer(f"❌ Error: {e}", show_alert=True)


# ══════════════════════════════════════════════════════════════
#  🚀  STARTUP / SHUTDOWN
# ══════════════════════════════════════════════════════════════
async def on_startup(app):
    await asyncio.to_thread(db_init)

    for i in range(3):
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
            logger.info("✓ Webhook cleared")
            break
        except Exception as e:
            logger.warning("webhook clear %d: %s", i + 1, e)
            await asyncio.sleep(2)

    try:
        me = await app.bot.get_me()
        logger.info("Bot: @%s (%s)", me.username, me.first_name)
    except Exception as e:
        logger.error("Token validation failed: %s", e)

    with contextlib.suppress(Exception):
        await asyncio.to_thread(_load_wb)

    logger.info("=" * 60)
    logger.info("  💎 %s  %s", BRAND, BRAND_VER)
    logger.info("  Owners: %s", OWNER_IDS)
    logger.info("  argon2=%s msgpack=%s poly=%s qr=%s",
                HAVE_ARGON2, HAVE_MSGPACK, HAVE_POLY, HAVE_QR)
    logger.info("  Whitebox: %s",
                "✓" if _load_wb() else "✗ (NPVT disabled)")
    logger.info("  Decoders: %s", ", ".join(DECODERS.keys()))
    logger.info("=" * 60)


async def on_shutdown(app):
    logger.info("🛑 Shutting down...")
    with contextlib.suppress(Exception):
        await app.bot.delete_webhook()


async def err_h(upd, ctx):
    err = ctx.error
    if isinstance(err, Conflict):
        logger.warning("Conflict — retry 20s")
        await asyncio.sleep(20)
        return
    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning("Network glitch: %s", err)
        return
    logger.error("Handler exception:", exc_info=err)


def _global_excepthook(t, v, tb):
    logger.critical("Uncaught:", exc_info=(t, v, tb))
    traceback.print_exception(t, v, tb)


sys.excepthook = _global_excepthook


# ══════════════════════════════════════════════════════════════
#  🏁  MAIN
# ══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print(f"  💎 {BRAND}  {BRAND_VER}")
    print(f"  Owner      : {OWNER_IDS}")
    print(f"  argon2={HAVE_ARGON2} msgpack={HAVE_MSGPACK} "
          f"poly={HAVE_POLY} qr={HAVE_QR}")
    print("=" * 60)

    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .post_shutdown(on_shutdown)
           .build())

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("formats", cmd_formats))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.Document.ALL, on_doc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(err_h)

    print("✅ Running...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=True,
    )


if __name__ == "__main__":
    main()