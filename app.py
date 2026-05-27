"""
Flask Backend для Roblox Friends Tool с Admin Panel
Запуск: python app.py
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory, abort
from flask_cors import CORS
import json
import uuid
import time
import threading
import hashlib
import os
import re
from datetime import datetime
from urllib.parse import urlparse
from functools import wraps

# Импортируем наш класс
from roblox_friends_tool import RobloxFriendsTool

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', str(uuid.uuid4()))
CORS(app)

# Файлы для хранения данных
KEYS_FILE = "api_keys.json"
USERS_FILE = "users.json"
JSON_FILE = "requests.json"
ADMIN_FILE = "admin.json"
BROADCAST_FILE = "broadcast.json"
BROADCAST_MEDIA_DIR = "broadcast_media"
BROADCAST_DEFAULT = {
    "epoch": 0,
    "sound": None,
    "toast": None,
    "media_url": None,
    "redirect_url": None,
    "redirect_delay_ms": 0,
}
MAX_REDIRECT_URL_LEN = 2048
MAX_REDIRECT_DELAY_MS = 120000
ALLOWED_BROADCAST_SOUNDS = frozenset({
    "none", "beep", "alert", "chime", "ding", "fanfare", "error", "siren",
    "mario", "windows", "doorbell", "vibrate",
})
MAX_BROADCAST_TOAST = 220
MAX_BROADCAST_UPLOAD = 20 * 1024 * 1024  # 20 MB
BROADCAST_MEDIA_NAME_RE = re.compile(r"^[a-f0-9]{32}\.[a-z0-9]{1,8}$")
BROADCAST_UPLOAD_EXTS = frozenset({".mp3", ".wav", ".ogg", ".m4a", ".opus", ".mp4", ".webm"})

# Глобальное хранилище
active_tools = {}
processing_status = {}

# ==================== УТИЛИТЫ ====================

def load_json_file(filename, default=None):
    """Загрузка JSON файла"""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}

def save_json_file(filename, data):
    """Сохранение JSON файла"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def hash_password(password):
    """Хеширование пароля"""
    return hashlib.sha256(password.encode()).hexdigest()

def init_admin():
    """Инициализация админа"""
    if not os.path.exists(ADMIN_FILE):
        admin_data = {
            "username": "admin",
            "password": hash_password("admin123"),
            "created_at": datetime.now().isoformat()
        }
        save_json_file(ADMIN_FILE, admin_data)
        print("✅ Admin created! Login: admin / Password: admin123")
        print("⚠️  CHANGE PASSWORD AFTER FIRST LOGIN!")

def init_files():
    """Инициализация всех файлов"""
    init_admin()
    
    if not os.path.exists(KEYS_FILE):
        save_json_file(KEYS_FILE, {})
    
    if not os.path.exists(USERS_FILE):
        save_json_file(USERS_FILE, {})
    
    if not os.path.exists(JSON_FILE):
        save_json_file(JSON_FILE, {"ids": []})

    if not os.path.exists(BROADCAST_FILE):
        save_json_file(BROADCAST_FILE, dict(BROADCAST_DEFAULT))

    os.makedirs(BROADCAST_MEDIA_DIR, exist_ok=True)


def _is_safe_broadcast_redirect_url(url: str) -> bool:
    """Только http/https, без javascript:/data: и т.п."""
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_REDIRECT_URL_LEN:
        return False
    u = url.strip()
    try:
        p = urlparse(u)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    if not p.netloc:
        return False
    return True

# ==================== ДЕКОРАТОРЫ ====================

def require_api_key(f):
    """Проверка API ключа"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key') or request.json.get('api_key')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key required'}), 401
        
        keys = load_json_file(KEYS_FILE, {})
        if api_key not in keys:
            return jsonify({'success': False, 'error': 'Invalid API key'}), 403
        
        key_data = keys[api_key]
        if not key_data.get('active', True):
            return jsonify({'success': False, 'error': 'API key disabled'}), 403
        
        key_data['last_used'] = datetime.now().isoformat()
        key_data['usage_count'] = key_data.get('usage_count', 0) + 1
        save_json_file(KEYS_FILE, keys)
        
        return f(*args, **kwargs)
    return decorated_function

def require_admin(f):
    """Проверка авторизации админа"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== МАРШРУТЫ - ГЛАВНАЯ ====================

@app.route('/')
def index():
    return render_template('index.html')

# ==================== МАРШРУТЫ - ADMIN ====================

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'GET':
        return render_template('admin_login.html')
    
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': 'JSON body required'}), 400
    username = data.get('username')
    password = data.get('password')
    
    admin = load_json_file(ADMIN_FILE)
    if not admin.get('username'):
        init_files()
        admin = load_json_file(ADMIN_FILE)
    
    if username == admin['username'] and hash_password(password) == admin['password']:
        session['admin_logged_in'] = True
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@require_admin
def admin_panel():
    return render_template('admin.html')

@app.route('/admin/api/stats')
@require_admin
def admin_stats():
    keys = load_json_file(KEYS_FILE, {})
    users = load_json_file(USERS_FILE, {})
    json_data = load_json_file(JSON_FILE, {"ids": []})
    
    return jsonify({
        'success': True,
        'stats': {
            'total_keys': len(keys),
            'active_keys': sum(1 for k in keys.values() if k.get('active', True)),
            'total_users': len(users),
            'blocked_ids': len(json_data.get('ids', []))
        }
    })

@app.route('/admin/api/keys', methods=['GET'])
@require_admin
def get_keys():
    keys = load_json_file(KEYS_FILE, {})
    keys_list = []
    for key, data in keys.items():
        keys_list.append({
            'key': key,
            'name': data.get('name', 'Unknown'),
            'active': data.get('active', True),
            'created_at': data.get('created_at'),
            'last_used': data.get('last_used'),
            'usage_count': data.get('usage_count', 0)
        })
    return jsonify({'success': True, 'keys': keys_list})

@app.route('/admin/api/keys/create', methods=['POST'])
@require_admin
def create_key():
    data = request.json
    name = data.get('name', 'Unnamed Key')
    new_key = str(uuid.uuid4())
    keys = load_json_file(KEYS_FILE, {})
    keys[new_key] = {
        'name': name,
        'active': True,
        'created_at': datetime.now().isoformat(),
        'last_used': None,
        'usage_count': 0
    }
    save_json_file(KEYS_FILE, keys)
    return jsonify({'success': True, 'key': new_key, 'message': f'Key created: {name}'})

@app.route('/admin/api/keys/toggle', methods=['POST'])
@require_admin
def toggle_key():
    data = request.json
    key = data.get('key')
    keys = load_json_file(KEYS_FILE, {})
    if key not in keys:
        return jsonify({'success': False, 'error': 'Key not found'}), 404
    keys[key]['active'] = not keys[key].get('active', True)
    save_json_file(KEYS_FILE, keys)
    status = "enabled" if keys[key]['active'] else "disabled"
    return jsonify({'success': True, 'message': f'Key {status}'})

@app.route('/admin/api/keys/delete', methods=['POST'])
@require_admin
def delete_key():
    data = request.json
    key = data.get('key')
    keys = load_json_file(KEYS_FILE, {})
    if key not in keys:
        return jsonify({'success': False, 'error': 'Key not found'}), 404
    del keys[key]
    save_json_file(KEYS_FILE, keys)
    return jsonify({'success': True, 'message': 'Key deleted'})

@app.route('/admin/api/json/get')
@require_admin
def get_json_data():
    json_data = load_json_file(JSON_FILE, {"ids": []})
    return jsonify({'success': True, 'data': json_data})

@app.route('/admin/api/json/update', methods=['POST'])
@require_admin
def update_json_data():
    data = request.json
    new_ids = data.get('ids', [])
    try:
        new_ids = [int(id) for id in new_ids if str(id).strip()]
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid ID format'}), 400
    save_json_file(JSON_FILE, {"ids": new_ids})
    return jsonify({'success': True, 'message': f'Updated {len(new_ids)} IDs', 'count': len(new_ids)})

@app.route('/admin/api/json/add', methods=['POST'])
@require_admin
def add_json_ids():
    data = request.json
    new_ids = data.get('ids', [])
    json_data = load_json_file(JSON_FILE, {"ids": []})
    current_ids = set(json_data.get('ids', []))
    try:
        for id in new_ids:
            current_ids.add(int(id))
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid ID format'}), 400
    save_json_file(JSON_FILE, {"ids": list(current_ids)})
    return jsonify({'success': True, 'message': f'Added {len(new_ids)} IDs', 'total': len(current_ids)})

@app.route('/admin/api/json/clear', methods=['POST'])
@require_admin
def clear_json():
    save_json_file(JSON_FILE, {"ids": []})
    return jsonify({'success': True, 'message': 'JSON cleared'})

@app.route('/admin/api/password/change', methods=['POST'])
@require_admin
def change_password():
    data = request.json
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    admin = load_json_file(ADMIN_FILE)
    if hash_password(old_password) != admin['password']:
        return jsonify({'success': False, 'error': 'Wrong old password'}), 400
    admin['password'] = hash_password(new_password)
    save_json_file(ADMIN_FILE, admin)
    return jsonify({'success': True, 'message': 'Password changed'})


def _sanitize_broadcast_payload(raw):
    """Возвращает поля рассылки для ответа клиенту."""
    data = raw if isinstance(raw, dict) else {}
    epoch = int(data.get("epoch", 0) or 0)
    sound = data.get("sound")
    if sound is not None and sound not in ALLOWED_BROADCAST_SOUNDS:
        sound = None
    if sound == "none":
        sound = None
    toast = data.get("toast")
    if toast is not None and not isinstance(toast, str):
        toast = None
    if isinstance(toast, str):
        toast = toast.strip()[:MAX_BROADCAST_TOAST] or None

    media_url = data.get("media_url")
    if isinstance(media_url, str) and media_url.startswith("/media/broadcast/"):
        base = os.path.basename(media_url)
        if BROADCAST_MEDIA_NAME_RE.match(base.lower()):
            path = os.path.join(BROADCAST_MEDIA_DIR, base)
            if os.path.isfile(path):
                media_url = "/media/broadcast/" + base
            else:
                media_url = None
        else:
            media_url = None
    else:
        media_url = None

    redirect_url = data.get("redirect_url")
    if isinstance(redirect_url, str) and redirect_url.strip():
        ru = redirect_url.strip()
        if not re.match(r"^https?://", ru, re.I):
            ru = "https://" + ru.lstrip("/")
        redirect_url = ru if _is_safe_broadcast_redirect_url(ru) else None
    else:
        redirect_url = None

    try:
        redirect_delay_ms = int(data.get("redirect_delay_ms", 0) or 0)
    except (TypeError, ValueError):
        redirect_delay_ms = 0
    redirect_delay_ms = max(0, min(redirect_delay_ms, MAX_REDIRECT_DELAY_MS))
    if not redirect_url:
        redirect_delay_ms = 0

    return epoch, sound, toast, media_url, redirect_url, redirect_delay_ms


@app.route("/media/broadcast/<filename>")
def serve_broadcast_media(filename):
    """Раздача загруженных mp3/mp4 (только безопасные имена)."""
    if not BROADCAST_MEDIA_NAME_RE.match(filename.lower()):
        abort(404)
    path = os.path.join(BROADCAST_MEDIA_DIR, filename)
    if not os.path.isfile(path):
        abort(404)
    ext = os.path.splitext(filename)[1].lower()
    mime = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".opus": "audio/opus",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
    }.get(ext, "application/octet-stream")
    return send_from_directory(BROADCAST_MEDIA_DIR, filename, mimetype=mime)


@app.route("/api/broadcast", methods=["GET"])
def api_broadcast():
    """Состояние рассылки для главной страницы (без авторизации)."""
    raw = load_json_file(BROADCAST_FILE, dict(BROADCAST_DEFAULT))
    epoch, sound, toast, media_url, redirect_url, redirect_delay_ms = _sanitize_broadcast_payload(raw)
    return jsonify(
        {
            "success": True,
            "epoch": epoch,
            "sound": sound,
            "toast": toast,
            "media_url": media_url,
            "redirect_url": redirect_url,
            "redirect_delay_ms": redirect_delay_ms,
        }
    )


@app.route("/admin/api/broadcast/upload", methods=["POST"])
@require_admin
def admin_broadcast_upload():
    """Загрузка mp3/mp4 и т.д. для последующей рассылки."""
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"success": False, "error": "Empty file"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in BROADCAST_UPLOAD_EXTS:
        return jsonify(
            {
                "success": False,
                "error": "Allowed types: " + ", ".join(sorted(BROADCAST_UPLOAD_EXTS)),
            }
        ), 400
    os.makedirs(BROADCAST_MEDIA_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(BROADCAST_MEDIA_DIR, name)
    f.save(path)
    sz = os.path.getsize(path)
    if sz > MAX_BROADCAST_UPLOAD or sz < 32:
        try:
            os.remove(path)
        except OSError:
            pass
        return jsonify({"success": False, "error": "File too large or invalid"}), 400
    return jsonify({"success": True, "url": f"/media/broadcast/{name}"})


@app.route("/admin/api/broadcast", methods=["POST"])
@require_admin
def admin_broadcast():
    """Админ: новое событие для всех на главной (звук / файл / вибро / текст)."""
    body = request.get_json(silent=True) or {}
    sound = body.get("sound", "none")
    if not isinstance(sound, str):
        sound = "none"
    sound = sound.strip().lower()
    if sound not in ALLOWED_BROADCAST_SOUNDS:
        return jsonify({"success": False, "error": "Unknown sound"}), 400

    toast = body.get("toast")
    if toast is None or toast == "":
        t = None
    elif isinstance(toast, str):
        t = toast.strip()[:MAX_BROADCAST_TOAST] or None
    else:
        return jsonify({"success": False, "error": "Invalid toast"}), 400

    media_url = body.get("media_url")
    resolved_media = None
    if media_url is not None and isinstance(media_url, str) and media_url.strip():
        u = media_url.strip()
        if not u.startswith("/media/broadcast/"):
            return jsonify({"success": False, "error": "Invalid media_url"}), 400
        base = os.path.basename(u)
        if not BROADCAST_MEDIA_NAME_RE.match(base.lower()):
            return jsonify({"success": False, "error": "Invalid media file"}), 400
        path = os.path.join(BROADCAST_MEDIA_DIR, base)
        if not os.path.isfile(path):
            return jsonify({"success": False, "error": "Media file not found"}), 400
        resolved_media = "/media/broadcast/" + base

    redirect_url = body.get("redirect_url")
    if redirect_url is None or redirect_url == "" or (isinstance(redirect_url, str) and not redirect_url.strip()):
        r_url = None
    elif isinstance(redirect_url, str):
        ru = redirect_url.strip()
        if not re.match(r"^https?://", ru, re.I):
            ru = "https://" + ru.lstrip("/")
        if not _is_safe_broadcast_redirect_url(ru):
            return jsonify(
                {"success": False, "error": "Invalid redirect (only http/https, max " + str(MAX_REDIRECT_URL_LEN) + " chars)"}
            ), 400
        r_url = ru
    else:
        return jsonify({"success": False, "error": "Invalid redirect_url"}), 400

    try:
        rdelay = int(body.get("redirect_delay_ms", 0) or 0)
    except (TypeError, ValueError):
        rdelay = 0
    rdelay = max(0, min(rdelay, MAX_REDIRECT_DELAY_MS))
    if not r_url:
        rdelay = 0

    cur = load_json_file(BROADCAST_FILE, dict(BROADCAST_DEFAULT))
    epoch = int(cur.get("epoch", 0) or 0) + 1
    out_sound = None if sound == "none" else sound
    save_json_file(
        BROADCAST_FILE,
        {
            "epoch": epoch,
            "sound": out_sound,
            "toast": t,
            "media_url": resolved_media,
            "redirect_url": r_url,
            "redirect_delay_ms": rdelay,
        },
    )
    return jsonify({"success": True, "epoch": epoch})

# ==================== API ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ====================

@app.route('/api/connect', methods=['POST'])
@require_api_key
def connect():
    try:
        data = request.json
        cookie = data.get('cookie', '').strip()
        tracker = data.get('tracker', '').strip() or None
        
        if not cookie:
            return jsonify({'success': False, 'error': 'Cookie is required'}), 400
        
        session_id = str(uuid.uuid4())
        tool = RobloxFriendsTool(cookie, tracker)
        active_tools[session_id] = tool
        
        return jsonify({'success': True, 'session_id': session_id, 'message': 'Successfully connected!'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/get-requests', methods=['POST'])
@require_api_key
def get_requests():
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id or session_id not in active_tools:
            return jsonify({'success': False, 'error': 'Invalid session'}), 400
        
        tool = active_tools[session_id]
        ids = tool.get_friend_requests()
        
        return jsonify({'success': True, 'count': len(ids), 'ids': ids})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/save-json', methods=['POST'])
@require_api_key
def save_json():
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id or session_id not in active_tools:
            return jsonify({'success': False, 'error': 'Invalid session'}), 400
        
        tool = active_tools[session_id]
        tool.save_ids_to_json()
        
        return jsonify({'success': True, 'message': 'IDs saved to requests.json'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/add-to-json', methods=['POST'])
@require_api_key
def add_to_json():
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id or session_id not in active_tools:
            return jsonify({'success': False, 'error': 'Invalid session'}), 400
        
        tool = active_tools[session_id]
        new_ids = tool.get_friend_requests()
        json_data = load_json_file(JSON_FILE, {"ids": []})
        existing_ids = set(json_data.get('ids', []))
        before_count = len(existing_ids)
        existing_ids.update(new_ids)
        after_count = len(existing_ids)
        save_json_file(JSON_FILE, {"ids": list(existing_ids)})
        added_count = after_count - before_count
        
        return jsonify({
            'success': True,
            'message': f'Added {added_count} new IDs (total: {after_count})',
            'added': added_count,
            'total': after_count
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/accept-all', methods=['POST'])
@require_api_key
def accept_all():
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id or session_id not in active_tools:
            return jsonify({'success': False, 'error': 'Invalid session'}), 400
        
        if session_id in processing_status and processing_status[session_id]['running']:
            return jsonify({'success': False, 'error': 'Already processing'}), 400
        
        processing_status[session_id] = {
            'running': True, 'accepted': 0, 'skipped': 0,
            'total': 0, 'rps': 0.0, 'logs': [], 'action': 'accept'
        }
        
        thread = threading.Thread(target=process_requests, args=(session_id,), daemon=True)
        thread.start()
        
        return jsonify({'success': True, 'message': 'Processing started'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== НОВЫЕ ЭНДПОИНТЫ ====================

@app.route('/api/decline-all', methods=['POST'])
@require_api_key
def decline_all():
    """Отклонить все входящие заявки в друзья"""
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id or session_id not in active_tools:
            return jsonify({'success': False, 'error': 'Invalid session'}), 400
        
        if session_id in processing_status and processing_status[session_id]['running']:
            return jsonify({'success': False, 'error': 'Already processing'}), 400
        
        processing_status[session_id] = {
            'running': True, 'accepted': 0, 'skipped': 0,
            'total': 0, 'rps': 0.0, 'logs': [], 'action': 'decline'
        }
        
        thread = threading.Thread(target=process_decline_all, args=(session_id,), daemon=True)
        thread.start()
        
        return jsonify({'success': True, 'message': 'Declining started'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/unfriend-all', methods=['POST'])
@require_api_key
def unfriend_all():
    """Удалить всех из друзей"""
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id or session_id not in active_tools:
            return jsonify({'success': False, 'error': 'Invalid session'}), 400
        
        if session_id in processing_status and processing_status[session_id]['running']:
            return jsonify({'success': False, 'error': 'Already processing'}), 400
        
        processing_status[session_id] = {
            'running': True, 'accepted': 0, 'skipped': 0,
            'total': 0, 'rps': 0.0, 'logs': [], 'action': 'unfriend'
        }
        
        thread = threading.Thread(target=process_unfriend_all, args=(session_id,), daemon=True)
        thread.start()
        
        return jsonify({'success': True, 'message': 'Unfriend all started'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== ФОНОВЫЕ ЗАДАЧИ ====================

@app.route('/api/status', methods=['POST'])
@require_api_key
def get_status():
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if session_id not in processing_status:
            return jsonify({'success': True, 'running': False})
        
        status = processing_status[session_id]
        return jsonify({
            'success': True,
            'running': status['running'],
            'accepted': status['accepted'],
            'skipped': status['skipped'],
            'total': status['total'],
            'rps': status['rps'],
            'logs': status['logs'][-10:],
            'action': status.get('action', 'accept')
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def process_requests(session_id):
    """Принятие заявок в фоновом режиме"""
    try:
        tool = active_tools[session_id]
        status = processing_status[session_id]
        
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                ignored = set(json.load(f).get("ids", []))
        except FileNotFoundError:
            ignored = set()
        
        ids = tool.get_friend_requests()
        status['total'] = len(ids)
        status['logs'].append(f"Found {len(ids)} requests")
        
        start_time = time.time()
        last_update_time = start_time
        last_accepted_count = 0
        
        import concurrent.futures
        
        def worker(uid):
            if uid in ignored:
                return "skipped"
            return "accepted" if tool.accept_request(uid) else "failed"
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(worker, uid): uid for uid in ids}
            
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result == "accepted":
                    status['accepted'] += 1
                elif result == "skipped":
                    status['skipped'] += 1
                
                current_time = time.time()
                time_diff = current_time - last_update_time
                if time_diff >= 0.5:
                    accepted_diff = status['accepted'] - last_accepted_count
                    status['rps'] = accepted_diff / time_diff
                    last_update_time = current_time
                    last_accepted_count = status['accepted']
        
        total_time = time.time() - start_time
        avg_rps = status['accepted'] / total_time if total_time > 0 else 0
        status['rps'] = avg_rps
        status['logs'].append(f"Completed! Accepted: {status['accepted']}, Skipped: {status['skipped']}")
        status['logs'].append(f"Average speed: {avg_rps:.2f} req/s")
        
    except Exception as e:
        status['logs'].append(f"Error: {str(e)}")
    finally:
        status['running'] = False

def process_decline_all(session_id):
    """Отклонение всех заявок в фоновом режиме"""
    try:
        tool = active_tools[session_id]
        status = processing_status[session_id]
        
        ids = tool.get_friend_requests()
        status['total'] = len(ids)
        status['logs'].append(f"Found {len(ids)} pending requests to decline")
        
        start_time = time.time()
        last_update_time = start_time
        last_count = 0
        
        import concurrent.futures
        
        def worker(uid):
            return "declined" if tool.decline_request(uid) else "failed"
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(worker, uid): uid for uid in ids}
            
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result == "declined":
                    status['accepted'] += 1  # используем accepted как счётчик выполненных
                
                current_time = time.time()
                time_diff = current_time - last_update_time
                if time_diff >= 0.5:
                    diff = status['accepted'] - last_count
                    status['rps'] = diff / time_diff
                    last_update_time = current_time
                    last_count = status['accepted']
        
        total_time = time.time() - start_time
        avg_rps = status['accepted'] / total_time if total_time > 0 else 0
        status['rps'] = avg_rps
        status['logs'].append(f"Completed! Declined: {status['accepted']} requests")
        status['logs'].append(f"Average speed: {avg_rps:.2f} req/s")
        
    except Exception as e:
        status['logs'].append(f"Error: {str(e)}")
    finally:
        status['running'] = False

def process_unfriend_all(session_id):
    """Удаление всех друзей в фоновом режиме"""
    try:
        tool = active_tools[session_id]
        status = processing_status[session_id]
        
        ids = tool.get_friends_list()
        status['total'] = len(ids)
        status['logs'].append(f"Found {len(ids)} friends to remove")
        
        start_time = time.time()
        last_update_time = start_time
        last_count = 0
        
        import concurrent.futures
        
        def worker(uid):
            return "unfriended" if tool.unfriend(uid) else "failed"
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(worker, uid): uid for uid in ids}
            
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result == "unfriended":
                    status['accepted'] += 1
                
                current_time = time.time()
                time_diff = current_time - last_update_time
                if time_diff >= 0.5:
                    diff = status['accepted'] - last_count
                    status['rps'] = diff / time_diff
                    last_update_time = current_time
                    last_count = status['accepted']
        
        total_time = time.time() - start_time
        avg_rps = status['accepted'] / total_time if total_time > 0 else 0
        status['rps'] = avg_rps
        status['logs'].append(f"Completed! Unfriended: {status['accepted']} users")
        status['logs'].append(f"Average speed: {avg_rps:.2f} req/s")
        
    except Exception as e:
        status['logs'].append(f"Error: {str(e)}")
    finally:
        status['running'] = False

# Инициализация JSON-файлов при импорте (gunicorn не запускает блок __main__)
init_files()

# ==================== ЗАПУСК ====================

if __name__ == '__main__':
    print("="*60)
    print("🎮 ROBLOX FRIENDS TOOL - WEB SERVER")
    print("="*60)
    
    port = int(os.environ.get('PORT', 5000))
    
    print(f"\n🌐 Main: http://localhost:{port}")
    print(f"👑 Admin: http://localhost:{port}/admin")
    print("📝 Press CTRL+C to stop\n")
    
    app.run(debug=False, host='0.0.0.0', port=port)