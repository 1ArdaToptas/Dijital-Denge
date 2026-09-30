import os
import sys
import json
import time
import threading
import copy
import re
import tempfile
import shutil
import subprocess
import winreg
import wave
import math
import winsound

from datetime import datetime, timedelta
import ctypes
from ctypes import wintypes

import os
import sys

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)
# Windows API handle'ları modül seviyesinde tanımlanır.
# Overlay fonksiyonları farklı metotlardan da bunlara erişebilsin.
if os.name == "nt":
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
else:
    user32 = None
    kernel32 = None


if not hasattr(wintypes, "LRESULT"):
    wintypes.LRESULT = ctypes.c_longlong
if not hasattr(wintypes, "HCURSOR"):
    wintypes.HCURSOR = wintypes.HANDLE

# Windows High-DPI: render Tk/CustomTkinter text and controls at the monitor DPI.
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

import customtkinter as ctk
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont


try:
    from winotify import Notification, audio as winotify_audio
    WIN_TOAST_AVAILABLE = True
except Exception:
    Notification = None
    winotify_audio = None
    WIN_TOAST_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _ensure_warning_sound():

    try:
        path = os.path.join(DATA_DIR, "dijital_denge_warning.wav")
        if os.path.exists(path):
            return path
        os.makedirs(DATA_DIR, exist_ok=True)
        rate = 44100

        parts = [(880, 0.22), (1175, 0.22), (880, 0.22), (1175, 0.32)]
        samples = bytearray()
        for freq, duration in parts:
            n = int(rate * duration)
            for i in range(n):
                t = i / rate

                fade = min(1.0, i / (rate * 0.012), (n - i) / (rate * 0.018))
                value = int(30000 * fade * math.sin(2 * math.pi * freq * t))
                samples += int(value).to_bytes(2, "little", signed=True)

            for _ in range(int(rate * 0.035)):
                samples += b"\x00\x00"
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(samples)
        return path
    except Exception as e:
        log_error("Uyarı sesi oluşturma", e)
        return None


def play_warning_sound():

    try:
        path = _ensure_warning_sound()
        if path:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return
    except Exception as e:
        log_error("Uyarı sesi", e)
    try:
        user32.MessageBeep(0x00000030)
    except Exception:
        pass



# ============================================================
# WINDOWS BAŞLANGIÇTA ÇALIŞTIRMA
# ============================================================
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "DijitalDenge"


def is_windows_startup_enabled():
    """Dijital Denge Windows ile otomatik başlıyor mu?"""
    if os.name != "nt":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False


def set_windows_startup(enabled):
    """Windows başlangıç kaydını ekler veya kaldırır."""
    if os.name != "nt":
        return False

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_REG_PATH,
            0,
            winreg.KEY_SET_VALUE
        ) as key:
            if enabled:

                if getattr(sys, "frozen", False):
                    command = f'"{sys.executable}"'
                else:
                    python_exe = sys.executable
                    pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
                    launcher = pythonw if os.path.isfile(pythonw) else python_exe
                    command = f'"{launcher}" "{os.path.abspath(__file__)}"'

                winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        log_error("Windows başlangıç ayarı", e)
        return False

# ============================================================
# WINDOWS API
# ============================================================
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Native Win32 fullscreen warning overlay state
_native_warning_hwnd = None
_native_warning_proc = None
_native_warning_class = None
_native_warning_app = None
_native_warning_thread = None
_native_warning_stop = None
_native_warning_ready = None

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# These run on every single tracker tick (every 5s, forever) and hand back
# pointer-sized handles (HWND/HANDLE). Without an explicit restype, ctypes
# assumes a 32-bit return and can truncate/corrupt the handle on 64-bit
# Windows. Window handles are usually small enough in practice that this
# rarely bites, but OpenProcess handles are real kernel pointers and are
# worth declaring correctly rather than relying on that being true forever.
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetDesktopWindow.restype = wintypes.HWND
user32.GetShellWindow.restype = wintypes.HWND
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]



def double_confirm_reset(parent, title, message, final_message):
    from tkinter import messagebox
    """Veri silme işlemleri için iki aşamalı güvenlik onayı."""
    first = messagebox.askyesno(
        translate(title, getattr(parent, "language", "Türkçe")),
        translate(message, getattr(parent, "language", "Türkçe")) + "\n\n" + translate("Devam etmek istediğinizden emin misiniz?", getattr(parent, "language", "Türkçe")),
        parent=parent,
        icon="warning"
    )
    if not first:
        return False

    second = messagebox.askyesno(
        translate("Son Onay", getattr(parent, "language", "Türkçe")),
        translate(final_message, getattr(parent, "language", "Türkçe")) + "\n\n" + translate("Bu işlem geri alınamaz.", getattr(parent, "language", "Türkçe")) + "\n\n" + translate("Gerçekten devam etmek istiyor musunuz?", getattr(parent, "language", "Türkçe")),
        parent=parent,
        icon="warning"
    )
    return bool(second)


class LASTINPUTINFO(ctypes.Structure):


    def _confirm_monthly_reset(self, action):
        month = datetime.now().strftime("%Y-%m")
        if double_confirm_reset(
            self,
            "Aylık Veri Sıfırlama",
            f"{month} ayındaki tüm kullanım verileri silinecek.",
            f"{month} ayındaki verileri kalıcı olarak silmek istediğinizden emin misiniz?"
        ):
            action()

    def _confirm_all_data_reset(self, action):
        if double_confirm_reset(
            self,
            "Tüm Verileri Sıfırla",
            "Sistemde tutulan TÜM kullanım verileri silinecek.",
            "Tüm kullanım geçmişini ve arşivlenmiş verileri kalıcı olarak silmek istediğinizden emin misiniz?"
        ):
            action()

    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def get_idle_seconds():
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if user32.GetLastInputInfo(ctypes.byref(lii)):
        now = kernel32.GetTickCount() & 0xFFFFFFFF
        last = lii.dwTime & 0xFFFFFFFF
        return ((now - last) & 0xFFFFFFFF) / 1000.0
    return 0.0


def get_active_process_name():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == 0:
        return None
    h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h_process:
        return None
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h_process, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
    finally:
        kernel32.CloseHandle(h_process)
    return None


def get_foreground_title():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def is_fullscreen():
    hwnd = user32.GetForegroundWindow()
    if not hwnd or hwnd in (user32.GetDesktopWindow(), user32.GetShellWindow()):
        return False
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left <= 0 and rect.top <= 0 and
            rect.right >= user32.GetSystemMetrics(0) and
            rect.bottom >= user32.GetSystemMetrics(1))


# ============================================================
# AYARLAR / SABİTLER
# ============================================================
APP_NAME = "Dijital Denge"
DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "DijitalDenge")
LOG_FILE = os.path.join(DATA_DIR, "dijital_denge.log")
DATA_FILE = os.path.join(DATA_DIR, "kullanim_verisi.json")
ARCHIVE_FILE = os.path.join(DATA_DIR, "kullanim_arsiv.json")
ARCHIVE_AFTER_DAYS = 365
_archived_days_cache = None
os.makedirs(DATA_DIR, exist_ok=True)

CURRENT_SCHEMA_VERSION = 30

DEFAULT_IDLE_THRESHOLD_SECONDS = 180
DEFAULT_REMINDER_MINUTES = 45
CONTINUOUS_WARNING_SECONDS = 2 * 60 * 60

OVERLAY_TEST_ON_START = False
CONTINUOUS_REMINDER_REPEAT_SECONDS = 30 * 60

CATEGORY_LIST = [
    "Mühendislik", "Geliştirme", "Çalışma", "Belge", "Tasarım",
    "Tarayıcı", "İletişim", "Oyun", "Medya", "Sistem", "Diğer"
]

APP_CATEGORIES = {
    "solidworks.exe": "Mühendislik", "autocad.exe": "Mühendislik", "catia.exe": "Mühendislik",
    "ansys.exe": "Mühendislik", "ansysproducts.exe": "Mühendislik", "fluent.exe": "Mühendislik",
    "abaqus.exe": "Mühendislik", "matlab.exe": "Mühendislik", "mastercam.exe": "Mühendislik",
    "mathcad.exe": "Mühendislik", "code.exe": "Geliştirme", "devenv.exe": "Geliştirme",
    "python.exe": "Geliştirme", "pycharm64.exe": "Geliştirme", "idea64.exe": "Geliştirme",
    "windowsterminal.exe": "Geliştirme", "cmd.exe": "Geliştirme", "powershell.exe": "Geliştirme",
    "git-bash.exe": "Geliştirme", "postman.exe": "Geliştirme", "obsidian.exe": "Çalışma",
    "notion.exe": "Çalışma", "onenote.exe": "Çalışma", "winword.exe": "Belge", "excel.exe": "Belge",
    "powerpnt.exe": "Belge", "acrobat.exe": "Belge", "acrord32.exe": "Belge", "figma.exe": "Tasarım",
    "photoshop.exe": "Tasarım", "illustrator.exe": "Tasarım", "chrome.exe": "Tarayıcı", "brave.exe": "Tarayıcı",
    "msedge.exe": "Tarayıcı", "firefox.exe": "Tarayıcı", "opera.exe": "Tarayıcı", "discord.exe": "İletişim",
    "teams.exe": "İletişim", "slack.exe": "İletişim", "telegram.exe": "İletişim", "whatsapp.exe": "İletişim",
    "outlook.exe": "İletişim", "steam.exe": "Oyun", "epicgameslauncher.exe": "Oyun", "r5apex.exe": "Oyun",
    "metroexodus.exe": "Oyun", "skyrimse.exe": "Oyun", "rimworldwin64.exe": "Oyun", "spotify.exe": "Medya",
    "vlc.exe": "Medya", "netflix.exe": "Medya", "explorer.exe": "Sistem"
}

CATEGORY_KEYWORD_FALLBACK = [
    ("code", "Geliştirme"), ("studio", "Geliştirme"), ("terminal", "Geliştirme"),
    ("figma", "Tasarım"), ("photoshop", "Tasarım"), ("illustrator", "Tasarım"),
    ("notion", "Çalışma"), ("obsidian", "Çalışma"), ("word", "Belge"), ("excel", "Belge"),
    ("powerpoint", "Belge"), ("chrome", "Tarayıcı"), ("firefox", "Tarayıcı"), ("edge", "Tarayıcı"),
    ("opera", "Tarayıcı"), ("discord", "İletişim"), ("teams", "İletişim"), ("slack", "İletişim"),
    ("whatsapp", "İletişim"), ("steam", "Oyun"), ("game", "Oyun"), ("spotify", "Medya"), ("vlc", "Medya")
]

CATEGORY_COLORS = {
    "Mühendislik": "#38bdf8", "Geliştirme": "#818cf8", "Çalışma": "#22c55e", "Belge": "#14b8a6",
    "Tasarım": "#f472b6", "Tarayıcı": "#f59e0b", "İletişim": "#ec4899", "Oyun": "#ef4444",
    "Medya": "#a855f7", "Sistem": "#64748b", "Diğer": "#94a3b8"
}
TURKCE_GUN_KISALTMALARI = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def turkce_gun_kisaltmasi(d):
    return TURKCE_GUN_KISALTMALARI[d.weekday()]


def get_category(app_name):
    if not app_name:
        return "Bilinmeyen"
    lowered = app_name.lower()
    with data_lock:
        custom_apps = bellek_db.get("custom_app_categories", {})
    if lowered in custom_apps:
        return custom_apps[lowered]
    if lowered in APP_CATEGORIES:
        return APP_CATEGORIES[lowered]
    for keyword, category in CATEGORY_KEYWORD_FALLBACK:
        if keyword in lowered:
            return category
    return "Diğer"


def get_all_categories():
    with data_lock:
        custom = bellek_db.get("custom_categories", {})
    return list(dict.fromkeys(CATEGORY_LIST + list(custom.keys())))


def format_seconds(seconds, language=None):
    language = language or CURRENT_LANGUAGE
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if language == "Türkçe":
        if h: return f"{h} sa {m} dk"
        if m: return f"{m} dk {s} sn"
        return f"{s} sn"
    units = {
        "English": ("h", "min", "sec"), "Deutsch": ("Std.", "Min.", "Sek."),
        "Français": ("h", "min", "s"), "Español": ("h", "min", "s"),
        "Italiano": ("h", "min", "s"), "Português": ("h", "min", "s"),
        "Nederlands": ("u", "min", "sec"),
        "العربية": ("س", "د", "ث"), "Čeština": ("h", "min", "s"),
        "Magyar": ("ó", "p", "mp"), "日本語": ("時間", "分", "秒"),
        "한국어": ("시간", "분", "초"), "Polski": ("godz.", "min", "s"),
        "Русский": ("ч", "мин", "с"), "简体中文": ("小时", "分钟", "秒")
    }
    hu, mu, su = units.get(language, units["English"])
    if h: return f"{h} {hu} {m} {mu}"
    if m: return f"{m} {mu} {s} {su}"
    return f"{s} {su}"


def format_short(seconds, language=None):
    language = language or CURRENT_LANGUAGE
    seconds = max(0, int(seconds))
    total_minutes = seconds // 60
    h, m = divmod(total_minutes, 60)
    if language == "Türkçe":
        return f"{h}s {m:02d}dk" if h else f"{m}dk"
    labels = {"English":"h","Deutsch":"Std.","Français":"h","Español":"h","Italiano":"h","Português":"h","Nederlands":"u",
              "العربية":"س","Čeština":"h","Magyar":"ó","日本語":"時間","한국어":"시간","Polski":"godz.","Русский":"ч","简体中文":"小时"}
    mins = {"English":"min","Deutsch":"Min.","Français":"min","Español":"min","Italiano":"min","Português":"min","Nederlands":"min",
            "العربية":"د","Čeština":"min","Magyar":"p","日本語":"分","한국어":"분","Polski":"min","Русский":"мин","简体中文":"分钟"}
    hu, mu = labels.get(language,"h"), mins.get(language,"min")
    return f"{h}{hu} {m:02d}{mu}" if h else f"{m}{mu}"


def log_error(context, exc):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {context}: {type(exc).__name__}: {exc}\n")
    except Exception:
        pass


# ============================================================
# VERİ YAPISI / AYARLAR
# ============================================================
data_lock = threading.RLock()
disk_write_lock = threading.Lock()

def yeni_gun():
    return {
        "apps": {}, "categories": {}, "sessions": [], "breaks": [],
        "total_active_seconds": 0, "longest_continuous_seconds": 0,
        "web": {}, "web_tracking_seconds": 0
    }


def yeni_veri():
    return {
        "version": CURRENT_SCHEMA_VERSION, "days": {},
        "settings": {
            "idle_threshold_seconds": DEFAULT_IDLE_THRESHOLD_SECONDS,
            "reminder_enabled": True, "reminder_minutes": DEFAULT_REMINDER_MINUTES,
            "sound_enabled": True, "web_tracking_enabled": False, "theme": "dark", "language": "Türkçe",
            "daily_goal_seconds": 6 * 3600,
            "category_goals": {"Mühendislik": 3 * 3600, "Oyun": 3600}
        },
        "custom_categories": {},
        "custom_app_categories": {}
    }


def migrate_data(raw):
    if not isinstance(raw, dict):
        return yeni_veri()
    base = yeni_veri()
    base.update({k: v for k, v in raw.items() if k in ("days", "settings", "custom_categories", "custom_app_categories")})
    base["version"] = CURRENT_SCHEMA_VERSION
    base.setdefault("days", {})
    base.setdefault("settings", yeni_veri()["settings"])
    base.setdefault("custom_categories", {})
    base.setdefault("custom_app_categories", {})
    for key, default in yeni_veri()["settings"].items():
        base["settings"].setdefault(key, default)
    for day in base["days"].values():
        if isinstance(day, dict):
            for key, default in yeni_gun().items():
                day.setdefault(key, copy.deepcopy(default))
    return base


bellek_db = yeni_veri()


def veri_baslat():
    global bellek_db
    with data_lock:
        if not os.path.exists(DATA_FILE):
            bellek_db = yeni_veri(); return
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                bellek_db = migrate_data(json.load(f))
        except Exception as e:
            log_error("Veri okunamadı", e)
            bellek_db = yeni_veri()


def ensure_day(date_key):
    bellek_db.setdefault("days", {})
    bellek_db["days"].setdefault(date_key, yeni_gun())


def _load_archived_days():

    global _archived_days_cache
    if _archived_days_cache is not None:
        return _archived_days_cache
    if not os.path.exists(ARCHIVE_FILE):
        _archived_days_cache = {}
        return _archived_days_cache
    try:
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _archived_days_cache = raw if isinstance(raw, dict) else {}
    except Exception as e:
        log_error("Arşiv okunamadı", e)
        _archived_days_cache = {}
    return _archived_days_cache


def get_db_snapshot():
    with data_lock:
        return copy.deepcopy(bellek_db)


def get_today_snapshot():

    today = datetime.now().strftime("%Y-%m-%d")
    with data_lock:
        day = bellek_db.get("days", {}).get(today)
        return today, (copy.deepcopy(day) if day else yeni_gun())


def get_days_snapshot(prefix=None):

    with data_lock:
        days = bellek_db.get("days", {})
        if prefix is None:
            return copy.deepcopy(days)
        return {k: copy.deepcopy(v) for k, v in days.items() if k.startswith(prefix)}


def get_day_snapshot(date_key):

    with data_lock:
        day = bellek_db.get("days", {}).get(date_key)
        if day is not None:
            return copy.deepcopy(day)
    archived = _load_archived_days()
    day = archived.get(date_key)
    return copy.deepcopy(day) if day is not None else yeni_gun()


def get_days_snapshot(date_keys):

    requested = set(date_keys)
    with data_lock:
        days = bellek_db.get("days", {})
        result = {k: copy.deepcopy(days[k]) for k in requested if k in days}
    missing = requested - set(result)
    if missing:
        archived = _load_archived_days()
        for k in missing:
            if k in archived:
                result[k] = copy.deepcopy(archived[k])
    return result


def get_days_stats_snapshot(date_keys, full_keys=None):

    requested = set(date_keys)
    full_keys = set(full_keys or ())
    result = {}

    def compact(day):
        return {
            "total_active_seconds": day.get("total_active_seconds", 0),
            "longest_continuous_seconds": day.get("longest_continuous_seconds", 0),
            "breaks": list(day.get("breaks", [])),
            "categories": dict(day.get("categories", {})),
            "apps": dict(day.get("apps", {})),
        }

    with data_lock:
        days = bellek_db.get("days", {})
        for key in requested:
            day = days.get(key)
            if day is not None:
                result[key] = copy.deepcopy(day) if key in full_keys else compact(day)
    missing = requested - set(result)
    if missing:
        archived = _load_archived_days()
        for key in missing:
            day = archived.get(key)
            if day is not None:
                result[key] = copy.deepcopy(day) if key in full_keys else compact(day)
    return result


def get_custom_app_categories_snapshot():
    with data_lock:
        return copy.deepcopy(bellek_db.get("custom_app_categories", {}))


def veri_diske_yaz():
    """Veriyi güvenli şekilde diske yazar; uzun disk işlemi data_lock'u tutmaz."""
    try:

        with data_lock:
            snapshot = copy.deepcopy(bellek_db)

        with disk_write_lock:
            os.makedirs(DATA_DIR, exist_ok=True)
            fd, temp_file = tempfile.mkstemp(prefix="kullanim_", suffix=".tmp", dir=DATA_DIR)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_file, DATA_FILE)
            finally:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass
    except Exception as e:
        log_error("Veri yazma hatası", e)


def archive_old_days(max_age_days=ARCHIVE_AFTER_DAYS):

    global _archived_days_cache
    cutoff = datetime.now().date() - timedelta(days=max_age_days)
    moved = {}
    try:
        with data_lock:
            days = bellek_db.get("days", {})
            for key in list(days.keys()):
                try:
                    day_date = datetime.strptime(key, "%Y-%m-%d").date()
                except (TypeError, ValueError):
                    continue
                if day_date < cutoff:
                    moved[key] = days.pop(key)

            if not moved:
                return 0

            existing = _load_archived_days()
            existing.update(moved)
            fd, temp_file = tempfile.mkstemp(prefix="arsiv_", suffix=".tmp", dir=DATA_DIR)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, separators=(",", ":"))
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_file, ARCHIVE_FILE)
            finally:
                if os.path.exists(temp_file):
                    os.remove(temp_file)

            _archived_days_cache = existing
            return len(moved)
    except Exception as e:
        log_error("Eski günleri arşivleme", e)
        return 0


def get_settings():
    with data_lock:
        return copy.deepcopy(bellek_db.get("settings", {}))


def update_settings(**kwargs):
    with data_lock:
        bellek_db.setdefault("settings", {}).update(kwargs)


# ============================================================
# CANLI DURUM
# ============================================================
live_lock = threading.RLock()
live_status = {"continuous_seconds": 0, "is_afk": False, "warning_active": False, "current_app": None}
tray_icon_ref = {"icon": None}
calisiyor = True

shutdown_event = threading.Event()


def set_live_status(**kwargs):
    with live_lock:
        live_status.update(kwargs)


def get_live_status():
    with live_lock:
        return dict(live_status)


# ============================================================
# SİSTEM SICAKLIK / KAYNAK BİLGİSİ
# ============================================================
def temp_status(temp):
    if temp is None:
        return "Sensör kullanılamıyor"
    if temp >= 90:
        return "Çok sıcak • kontrol et"
    if temp >= 80:
        return "Yüksek sıcaklık"
    if temp >= 70:
        return "Isınıyor"
    return "Normal aralık"


# ============================================================
# TRACKER
# ============================================================
class ActivityTracker:
    def __init__(self):
        self.last_tick = time.monotonic()
        self.current_app = None
        self.current_category = None
        self.app_session_start = None
        self.app_session_seconds = 0.0
        self.continuous_start = None
        self.continuous_seconds = 0.0
        self.break_start = None
        self.web_session_key = None
        self.web_session_seconds = 0.0
        self.disk_counter = 0.0
        self.reminder_counter = 0.0
        self.warning_repeat_counter = 0.0
        self.warning_fired = False
        self.today_key = datetime.now().strftime("%Y-%m-%d")

    @property
    def settings(self):
        return get_settings()

    @property
    def language(self):

        return self.settings.get("language", "Türkçe")

    def check_new_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today == self.today_key:
            return
        self.finish_app_session(); self.finish_continuous_session()
        self.today_key = today
        self.current_app = self.current_category = None
        self.app_session_start = None; self.app_session_seconds = 0
        self.continuous_start = None; self.continuous_seconds = 0
        self.break_start = None; self.web_session_key = None; self.web_session_seconds = 0
        self.warning_repeat_counter = self.reminder_counter = 0
        self.warning_fired = False
        set_live_status(continuous_seconds=0, warning_active=False, current_app=None)

    def start_app_session(self, app_name):
        self.current_app = app_name
        self.current_category = self.category_for(app_name)
        self.app_session_start = datetime.now()
        self.app_session_seconds = 0.0

    def category_for(self, app_name):
        return get_category(app_name)

    def start_continuous_session(self):
        if self.continuous_start is None:
            self.continuous_start = datetime.now()
            self.continuous_seconds = 0.0

    def finish_app_session(self):
        if not self.current_app or self.app_session_seconds <= 0:
            return
        with data_lock:
            ensure_day(self.today_key)
            bellek_db["days"][self.today_key]["sessions"].append({
                "type": "application", "app": self.current_app,
                "category": self.current_category, "start": self.app_session_start.isoformat() if self.app_session_start else None,
                "duration_seconds": int(self.app_session_seconds)
            })
        self.app_session_start = None; self.app_session_seconds = 0
        self.current_app = self.current_category = None

    def finish_continuous_session(self):
        if self.continuous_start is None or self.continuous_seconds <= 0:
            self.continuous_start = None; self.continuous_seconds = 0
            self.warning_repeat_counter = self.reminder_counter = 0; self.warning_fired = False
            set_live_status(continuous_seconds=0, warning_active=False)
            return
        with data_lock:
            ensure_day(self.today_key)
            day = bellek_db["days"][self.today_key]
            duration = int(self.continuous_seconds)
            day["sessions"].append({"type": "continuous", "start": self.continuous_start.isoformat(), "duration_seconds": duration})
            day["longest_continuous_seconds"] = max(day["longest_continuous_seconds"], duration)
        self.continuous_start = None; self.continuous_seconds = 0
        self.warning_repeat_counter = self.reminder_counter = 0; self.warning_fired = False
        set_live_status(continuous_seconds=0, warning_active=False)

    def add_usage(self, app_name, seconds):
        if seconds <= 0: return
        date_key = datetime.now().strftime("%Y-%m-%d")
        category = self.category_for(app_name)
        with data_lock:
            ensure_day(date_key); day = bellek_db["days"][date_key]
            day["apps"][app_name] = day["apps"].get(app_name, 0) + seconds
            day["categories"][category] = day["categories"].get(category, 0) + seconds
            day["total_active_seconds"] += seconds
            day["longest_continuous_seconds"] = max(day["longest_continuous_seconds"], int(self.continuous_seconds))

    def register_break(self, start, end):
        if not start or not end: return
        duration = (end - start).total_seconds()
        threshold = int(self.settings.get("idle_threshold_seconds", DEFAULT_IDLE_THRESHOLD_SECONDS))
        if duration < threshold: return
        if duration < 1800: kind = "Kısa Mola"
        elif duration < 7200: kind = "Uzun Mola"
        else: kind = "Bilgisayar Dışı"
        with data_lock:
            ensure_day(self.today_key)
            bellek_db["days"][self.today_key]["breaks"].append({
                "start": start.isoformat(), "end": end.isoformat(), "duration_seconds": int(duration), "type": kind
            })

    def _close_warning_overlay(self):
        """Native overlay'i kendi Win32 thread'ine kapattır."""
        global _native_warning_hwnd, _native_warning_stop
        try:
            if _native_warning_stop is not None:
                _native_warning_stop.set()
            hwnd = _native_warning_hwnd
            if hwnd and user32.IsWindow(hwnd):
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception as e:
            log_error("Native overlay kapatma", e)
        self._warning_overlay = None

    def _snooze_warning_overlay(self):
        self._close_warning_overlay()
        self._warning_overlay_after_id = self.after(10 * 60 * 1000, self.show_fullscreen_warning)

    def show_fullscreen_warning(self):

        global _native_warning_hwnd, _native_warning_proc, _native_warning_class
        global _native_warning_app, _native_warning_thread, _native_warning_stop, _native_warning_ready

        if os.name != "nt":
            return
        if _native_warning_thread and _native_warning_thread.is_alive():
            return

        self._warning_overlay_after_id = None
        _native_warning_app = self
        _native_warning_stop = threading.Event()
        _native_warning_ready = threading.Event()

        def overlay_thread():
            global _native_warning_hwnd, _native_warning_proc, _native_warning_class
            try:
                WNDPROC = ctypes.WINFUNCTYPE(
                    wintypes.LRESULT, wintypes.HWND, wintypes.UINT,
                    wintypes.WPARAM, wintypes.LPARAM
                )

                WM_PAINT = 0x000F
                WM_LBUTTONUP = 0x0202
                WM_NCHITTEST = 0x0084
                WM_ERASEBKGND = 0x0014
                WM_DESTROY = 0x0002
                WM_CLOSE = 0x0010
                WM_MOUSEACTIVATE = 0x0021
                MA_NOACTIVATE = 3
                HTCLIENT = 1
                WS_POPUP = 0x80000000
                WS_EX_TOPMOST = 0x00000008
                WS_EX_TOOLWINDOW = 0x00000080
                WS_EX_NOACTIVATE = 0x08000000
                WS_EX_LAYERED = 0x00080000
                SW_SHOWNOACTIVATE = 4
                SWP_NOSIZE = 0x0001
                SWP_NOMOVE = 0x0002
                SWP_SHOWWINDOW = 0x0040
                HWND_TOPMOST = ctypes.c_void_p(-1)
                LWA_ALPHA = 0x2

                class WNDCLASSW(ctypes.Structure):
                    _fields_ = [
                        ('style', wintypes.UINT),
                        ('lpfnWndProc', WNDPROC),
                        ('cbClsExtra', ctypes.c_int),
                        ('cbWndExtra', ctypes.c_int),
                        ('hInstance', wintypes.HINSTANCE),
                        ('hIcon', wintypes.HICON),
                        ('hCursor', wintypes.HCURSOR),
                        ('hbrBackground', wintypes.HBRUSH),
                        ('lpszMenuName', wintypes.LPCWSTR),
                        ('lpszClassName', wintypes.LPCWSTR),
                    ]

                class PAINTSTRUCT(ctypes.Structure):
                    _fields_ = [
                        ('hdc', wintypes.HDC),
                        ('fErase', wintypes.BOOL),
                        ('rcPaint', wintypes.RECT),
                        ('fRestore', wintypes.BOOL),
                        ('fIncUpdate', wintypes.BOOL),
                        ('rgbReserved', wintypes.BYTE * 32),
                    ]

                gdi32 = ctypes.windll.gdi32
                user32.GetMessageW.restype = wintypes.BOOL
                user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
                user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
                user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
                user32.PostQuitMessage.argtypes = [ctypes.c_int]
                user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
                user32.RegisterClassW.restype = wintypes.ATOM
                user32.CreateWindowExW.restype = wintypes.HWND
                user32.CreateWindowExW.argtypes = [
                    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID
                ]
                user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                user32.UpdateWindow.argtypes = [wintypes.HWND]
                user32.SetWindowPos.argtypes = [
                    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, wintypes.UINT
                ]
                user32.SetLayeredWindowAttributes.argtypes = [
                    wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD
                ]
                user32.DefWindowProcW.restype = wintypes.LRESULT
                user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                user32.BeginPaint.restype = wintypes.HDC
                user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
                user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
                user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT]
                user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL]
                user32.DestroyWindow.argtypes = [wintypes.HWND]
                user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

                gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
                gdi32.CreateFontW.restype = wintypes.HFONT
                gdi32.SelectObject.restype = wintypes.HGDIOBJ
                gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
                gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
                gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
                gdi32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
                gdi32.RoundRect.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]

                def rgb(r, g, b):
                    return r | (g << 8) | (b << 16)

                DT_CENTER = 0x00000001
                DT_VCENTER = 0x00000004
                DT_SINGLELINE = 0x00000020
                DT_WORDBREAK = 0x00000010

                def draw_text(hdc, text, rect, size, bold=False, color=None, flags=DT_CENTER | DT_VCENTER | DT_SINGLELINE):
                    if color is None:
                        color = rgb(245, 247, 250)
                    hf = gdi32.CreateFontW(-size, 0, 0, 0, 700 if bold else 400,
                                           0, 0, 0, 1, 0, 0, 0, 0, 'Segoe UI')
                    old = gdi32.SelectObject(hdc, hf)
                    gdi32.SetBkMode(hdc, 1)
                    gdi32.SetTextColor(hdc, color)
                    rr = wintypes.RECT(rect.left, rect.top, rect.right, rect.bottom)
                    user32.DrawTextW(hdc, text, -1, ctypes.byref(rr), flags)
                    gdi32.SelectObject(hdc, old)
                    gdi32.DeleteObject(hf)

                def proc(hwnd, msg, wparam, lparam):
                    global _native_warning_hwnd
                    if msg == WM_MOUSEACTIVATE:
                        return MA_NOACTIVATE
                    if msg == WM_NCHITTEST:
                        return HTCLIENT
                    if msg == WM_PAINT:
                        ps = PAINTSTRUCT()
                        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                        rc = wintypes.RECT()
                        user32.GetClientRect(hwnd, ctypes.byref(rc))
                        bg = gdi32.CreateSolidBrush(rgb(12, 18, 28))
                        user32.FillRect(hdc, ctypes.byref(rc), bg)
                        gdi32.DeleteObject(bg)

                        card = gdi32.CreateSolidBrush(rgb(31, 41, 55))
                        old_brush = gdi32.SelectObject(hdc, card)
                        gdi32.RoundRect(hdc, 12, 12, rc.right - 12, rc.bottom - 12, 22, 22)
                        gdi32.SelectObject(hdc, old_brush)
                        gdi32.DeleteObject(card)

                        dot = gdi32.CreateSolidBrush(rgb(239, 68, 68))
                        old_brush = gdi32.SelectObject(hdc, dot)
                        gdi32.RoundRect(hdc, 38, 36, 56, 54, 9, 9)
                        gdi32.SelectObject(hdc, old_brush)
                        gdi32.DeleteObject(dot)

                        draw_text(hdc, translate('DİJİTAL DENGE', getattr(_native_warning_app, 'language', 'Türkçe')), wintypes.RECT(65, 27, rc.right - 60, 63), 18, True)
                        draw_text(hdc, '×', wintypes.RECT(rc.right - 55, 25, rc.right - 20, 65), 25, True, rgb(203, 213, 225))
                        native_lang = getattr(_native_warning_app, 'language', 'Türkçe')
                        native_message = getattr(
                            _native_warning_app,
                            '_native_warning_message',
                            translate('2 saattir mola vermeden kullanıyorsun. Kısa bir mola ver.', native_lang)
                        )
                        draw_text(hdc, native_message,
                                  wintypes.RECT(35, 80, rc.right - 35, 130), 18, True)
                        draw_text(hdc, translate('Kısa bir mola vermen iyi olabilir.', native_lang),
                                  wintypes.RECT(35, 130, rc.right - 35, 165), 13, False, rgb(203, 213, 225))

                        b1 = gdi32.CreateSolidBrush(rgb(71, 85, 105))
                        old_brush = gdi32.SelectObject(hdc, b1)
                        gdi32.RoundRect(hdc, 35, 195, 230, 245, 11, 11)
                        gdi32.SelectObject(hdc, old_brush)
                        gdi32.DeleteObject(b1)
                        b2 = gdi32.CreateSolidBrush(rgb(2, 132, 199))
                        old_brush = gdi32.SelectObject(hdc, b2)
                        gdi32.RoundRect(hdc, 242, 195, rc.right - 35, 245, 11, 11)
                        gdi32.SelectObject(hdc, old_brush)
                        gdi32.DeleteObject(b2)
                        draw_text(hdc, translate('10 dk ertele', getattr(_native_warning_app, 'language', 'Türkçe')), wintypes.RECT(35, 195, 230, 245), 12, True)
                        draw_text(hdc, translate('Tamam', getattr(_native_warning_app, 'language', 'Türkçe')), wintypes.RECT(242, 195, rc.right - 35, 245), 12, True)
                        user32.EndPaint(hwnd, ctypes.byref(ps))
                        return 0

                    if msg == WM_LBUTTONUP:
                        x = ctypes.c_short(lparam & 0xffff).value
                        y = ctypes.c_short((lparam >> 16) & 0xffff).value
                        app = _native_warning_app
                        if app:
                            if 242 <= x <= 490 and 195 <= y <= 245:
                                app.after(0, app._close_warning_overlay)
                            elif 35 <= x <= 230 and 195 <= y <= 245:
                                app.after(0, app._snooze_warning_overlay)
                            elif x > 450 and y < 70:
                                app.after(0, app._close_warning_overlay)
                        return 0

                    if msg == WM_CLOSE:
                        user32.DestroyWindow(hwnd)
                        return 0
                    if msg == WM_ERASEBKGND:
                        return 1
                    if msg == WM_DESTROY:
                        _native_warning_hwnd = None
                        user32.PostQuitMessage(0)
                        return 0
                    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

                _native_warning_proc = WNDPROC(proc)
                _native_warning_class = f'DijitalDengeNativeWarning_{os.getpid()}'
                hinst = kernel32.GetModuleHandleW(None)

                wc = WNDCLASSW()
                wc.style = 0
                wc.lpfnWndProc = _native_warning_proc
                wc.hInstance = hinst
                wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
                wc.lpszClassName = _native_warning_class
                atom = user32.RegisterClassW(ctypes.byref(wc))
                if not atom:
                    # Aynı sınıf önceden kayıtlıysa yine de CreateWindow çalışabilir.
                    pass

                hwnd_fore = user32.GetForegroundWindow()
                mon = user32.MonitorFromWindow(hwnd_fore, 2)

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ('cbSize', wintypes.DWORD),
                        ('rcMonitor', wintypes.RECT),
                        ('rcWork', wintypes.RECT),
                        ('dwFlags', wintypes.DWORD),
                    ]

                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(mi)
                if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
                    mi.rcMonitor = wintypes.RECT(0, 0, 1920, 1080)

                mw, mh = 520, 285
                x = mi.rcMonitor.left + ((mi.rcMonitor.right - mi.rcMonitor.left) - mw) // 2
                y = mi.rcMonitor.top + ((mi.rcMonitor.bottom - mi.rcMonitor.top) - mh) // 2

                hwnd = user32.CreateWindowExW(
                    WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED,
                    _native_warning_class,
                    'Dijital Denge',
                    WS_POPUP,
                    x, y, mw, mh,
                    None, None, hinst, None
                )
                if not hwnd:
                    raise RuntimeError(f'Win32 overlay HWND oluşturulamadı (hata={ctypes.get_last_error()})')

                _native_warning_hwnd = hwnd
                self._warning_overlay = hwnd
                user32.SetLayeredWindowAttributes(hwnd, 0, 250, LWA_ALPHA)
                user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, mw, mh,
                                    SWP_SHOWWINDOW)
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, mw, mh,
                                    SWP_SHOWWINDOW)
                user32.UpdateWindow(hwnd)
                _native_warning_ready.set()

                # Kendi message loop'u: WM_PAINT ve tıklamalar burada işlenir.
                msg = wintypes.MSG()
                while not _native_warning_stop.is_set():
                    result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if result <= 0:
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

            except Exception as e:
                log_error('Native overlay thread', e)
                try:
                    _native_warning_ready.set()
                except Exception:
                    pass
            finally:
                _native_warning_hwnd = None

        _native_warning_thread = threading.Thread(target=overlay_thread, name='DijitalDengeOverlay', daemon=True)
        _native_warning_thread.start()

    def _keep_native_warning_topmost(self):
        """Eski API uyumluluğu: overlay zaten kendi thread'inde TOPMOST tutulur."""
        global _native_warning_hwnd
        try:
            if _native_warning_hwnd and user32.IsWindow(_native_warning_hwnd):
                user32.SetWindowPos(_native_warning_hwnd, wintypes.HWND(-1), 0, 0, 0, 0,
                                    0x0001 | 0x0002 | 0x0040)
        except Exception:
            pass

    def show_windows_toast(self, title, message):
        """WhatsApp benzeri gercek Windows bildirim banner'i gosterir."""
        if not WIN_TOAST_AVAILABLE:
            log_error("Windows Toast", "winotify kurulu degil")
            return False
        try:
            icon_path = os.path.join(BASE_DIR, "assets", "dijital_denge_icon2.png")
            if not os.path.exists(icon_path):
                icon_path = os.path.join(BASE_DIR, "assets", "dijital_denge_logo.png")
            kwargs = {"app_id": APP_NAME, "title": title, "msg": message}
            if os.path.exists(icon_path):
                kwargs["icon"] = icon_path
            toast = Notification(**kwargs)
            try:
                toast.set_audio(winotify_audio.Default, loop=False)
            except Exception:
                pass
            toast.show()
            return True
        except Exception as e:
            log_error("Windows Toast", e)
            return False

    def test_windows_toast(self):
        """Bildirim sistemini 2 saat beklemeden test etmek icin."""
        self.show_windows_toast(
            APP_NAME,
            translate("2 saattir mola vermeden kullanıyorsun. Kısa bir mola ver.", self.language)
        )

    def trigger_warning(self):
        set_live_status(warning_active=True)
        if not self.settings.get("reminder_enabled", True):
            return

        message = translate("2 saattir mola vermeden kullanıyorsun. Kısa bir mola ver.", self.language)
        self._native_warning_message = message


        try:
            self.show_fullscreen_warning()
        except Exception as e:
            log_error("Native bildirim", e)


        shown = self.show_windows_toast(APP_NAME, message)

        # Ses ayari aciksa ek ses de ver.
        if self.settings.get("sound_enabled", True):
            try:
                play_warning_sound()
            except Exception:
                pass


        if not shown:
            icon = tray_icon_ref.get("icon")
            if icon:
                try:
                    icon.notify(message, APP_NAME)
                except Exception as e:
                    log_error("Tray bildirimi", e)

    def extract_web_site(self, app, title):
        if app.lower() not in {"chrome.exe", "brave.exe", "msedge.exe", "firefox.exe", "opera.exe"}:
            return None

        clean = title.split(" - ")[0].strip()
        if not clean: return None
        known = ["YouTube", "Google", "GitHub", "ChatGPT", "Wikipedia", "Gmail", "Stack Overflow", "LinkedIn", "Netflix"]
        for name in known:
            if name.lower() in clean.lower(): return name
        return clean[:80]

    def add_web_usage(self, site, seconds):
        if not site or seconds <= 0: return
        with data_lock:
            ensure_day(self.today_key); day = bellek_db["days"][self.today_key]
            day["web"][site] = day["web"].get(site, 0) + seconds
            day["web_tracking_seconds"] += seconds

    def tick(self):
        now_mono = time.monotonic(); delta = now_mono - self.last_tick; self.last_tick = now_mono
        if delta > 15: delta = 5
        if delta < 0: delta = 0
        self.check_new_day()
        settings = self.settings
        idle_threshold = int(settings.get("idle_threshold_seconds", DEFAULT_IDLE_THRESHOLD_SECONDS))
        idle = get_idle_seconds(); app = get_active_process_name()

        if idle >= idle_threshold:
            if self.current_app: self.finish_app_session()
            if self.continuous_start: self.finish_continuous_session()
            if self.break_start is None:
                self.break_start = datetime.now() - timedelta(seconds=idle_threshold)
            self.web_session_key = None; self.web_session_seconds = 0
            set_live_status(is_afk=True, continuous_seconds=0, warning_active=False, current_app=None)
            return

        if self.break_start:
            self.register_break(self.break_start, datetime.now())
            self.break_start = None
        set_live_status(is_afk=False)
        if not app: return
        ignored = {"idle", "lockapp.exe", "screensaver.exe", "searchhost.exe", "startmenuexperiencehost.exe"}
        if app.lower() in ignored: return

        self.start_continuous_session()
        if app != self.current_app:
            self.finish_app_session(); self.start_app_session(app)
        self.add_usage(app, delta)
        self.app_session_seconds += delta
        self.continuous_seconds += delta
        set_live_status(continuous_seconds=int(self.continuous_seconds), current_app=app)

        if settings.get("web_tracking_enabled", False):
            site = self.extract_web_site(app, get_foreground_title())
            if site:
                self.add_web_usage(site, delta)

        # Mola hatırlatıcısı: ayarlanabilir süre.
        reminder_seconds = max(60, int(settings.get("reminder_minutes", DEFAULT_REMINDER_MINUTES)) * 60)
        if settings.get("reminder_enabled", True):
            self.reminder_counter += delta
            if self.reminder_counter >= reminder_seconds:
                self.reminder_counter = 0
                # 2 saatlik zorunlu banner ile çakışmayı önlemek için basit bildirim.
                reminder_message = f"{settings.get('reminder_minutes', DEFAULT_REMINDER_MINUTES)} {translate('dakikadır aktif kullanımdasın. Kısa bir mola iyi olabilir.', self.language)}"


                self._native_warning_message = reminder_message
                native_shown = False
                try:
                    self.show_fullscreen_warning()
                    native_shown = True
                except Exception as e:
                    log_error("Mola native bildirimi", e)


                shown = self.show_windows_toast(APP_NAME, reminder_message)

                if settings.get("sound_enabled", True):
                    try: play_warning_sound()
                    except Exception: pass

                if not native_shown and not shown:
                    icon = tray_icon_ref.get("icon")
                    if icon:
                        try: icon.notify(reminder_message, APP_NAME)
                        except Exception as e: log_error("Mola hatırlatıcısı", e)

        if self.continuous_seconds >= CONTINUOUS_WARNING_SECONDS:
            if not self.warning_fired:
                self.warning_fired = True; self.warning_repeat_counter = 0; self.trigger_warning()
            else:
                self.warning_repeat_counter += delta
                if self.warning_repeat_counter >= CONTINUOUS_REMINDER_REPEAT_SECONDS:
                    self.warning_repeat_counter = 0; self.trigger_warning()

        self.disk_counter += delta
        if self.disk_counter >= 60:
            veri_diske_yaz(); self.disk_counter = 0


def arka_plan_dongusu():
    global calisiyor
    tracker = ActivityTracker()
    while calisiyor:
        try: tracker.tick()
        except Exception as e: log_error("Tracker", e)

        if shutdown_event.wait(5):
            break
    tracker.finish_app_session(); tracker.finish_continuous_session(); veri_diske_yaz()


# ============================================================
# DİL DESTEĞİ
# ============================================================
LANGUAGE_OPTIONS = ["Türkçe", "English", "Deutsch", "Français", "Español", "Italiano", "Português", "Nederlands", "العربية", "Čeština", "Magyar", "日本語", "한국어", "Polski", "Русский", "简体中文"]
CURRENT_LANGUAGE = "Türkçe"
LANGUAGE_CODES = {"Türkçe":"tr", "English":"en", "Deutsch":"de", "Français":"fr", "Español":"es", "Italiano":"it", "Português":"pt", "Nederlands":"nl", "العربية":"ar", "Čeština":"cs", "Magyar":"hu", "日本語":"ja", "한국어":"ko", "Polski":"pl", "Русский":"ru", "简体中文":"zh-CN"}


def _normalize_lang_keys(lang_dict):

    return {LANGUAGE_CODES.get(k, k): v for k, v in lang_dict.items()}
TRANSLATIONS = {
    "العربية": {},
    "Čeština": {},
    "Magyar": {},
    "日本語": {},
    "한국어": {},
    "Polski": {},
    "Русский": {},
    "简体中文": {},

    "Genel Bakış": {"en":"Overview","de":"Übersicht","fr":"Vue d’ensemble","es":"Resumen","it":"Panoramica","pt":"Visão geral","nl":"Overzicht"},
    "Uygulamalar": {"en":"Applications","de":"Anwendungen","fr":"Applications","es":"Aplicaciones","it":"Applicazioni","pt":"Aplicações","nl":"Applicaties"},
    "Oturumlar": {"en":"Sessions","de":"Sitzungen","fr":"Sessions","es":"Sesiones","it":"Sessioni","pt":"Sessões","nl":"Sessies"},
    "İstatistikler": {"en":"Statistics","de":"Statistiken","fr":"Statistiques","es":"Estadísticas","it":"Statistiche","pt":"Estatísticas","nl":"Statistieken"},
    "Ayarlar": {"en":"Settings","de":"Einstellungen","fr":"Einstellungen","es":"Ajustes","it":"Impostazioni","pt":"Configurações","nl":"Instellingen"},
    "GÖRÜNÜM": {"en":"APPEARANCE","de":"DARSTELLUNG","fr":"APPARENCE","es":"APARIENCIA","it":"ASPETTO","pt":"APARÊNCIA","nl":"WEERGAVE"},
    "Karanlık": {"en":"Dark","de":"Dunkel","fr":"Sombre","es":"Oscuro","it":"Scuro","pt":"Escuro","nl":"Donker"},
    "Aydınlık": {"en":"Light","de":"Hell","fr":"Clair","es":"Claro","it":"Chiaro","pt":"Claro","nl":"Licht"},
    "Sistem": {"en":"System","de":"System","fr":"Système","es":"Sistema","it":"Sistema","pt":"Sistema","nl":"Systeem"},
    "Dil": {"en":"Language","de":"Sprache","fr":"Langue","es":"Idioma","it":"Lingua","pt":"Idioma","nl":"Taal"},
    "Uygulama dili": {"en":"Application language","de":"Anwendungssprache","fr":"Langue de l’application","es":"Idioma de la aplicación","it":"Lingua dell’applicazione","pt":"Idioma da aplicação","nl":"Taal van de applicatie"},
    "Aylık Günlük Kullanım": {"en":"Monthly Daily Usage","de":"Tägliche Monatsnutzung","fr":"Utilisation quotidienne mensuelle","es":"Uso diario mensual","it":"Utilizzo giornaliero mensile","pt":"Uso diário mensal","nl":"Maandelijks dagelijks gebruik"},
    "Günlük Saatlik Kullanım": {"en":"Daily Hourly Usage","de":"Stündliche Tagesnutzung","fr":"Utilisation horaire quotidienne","es":"Uso por hora diario","it":"Utilizzo orario giornaliero","pt":"Uso horário diário","nl":"Dagelijks gebruik per uur"},
    "Uygulama Kullanımı": {"en":"Application Usage","de":"Anwendungsnutzung","fr":"Utilisation des applications","es":"Uso de aplicaciones","it":"Utilizzo delle applicazioni","pt":"Uso de aplicações","nl":"Applicatiegebruik"},
    "KATEGORİLER": {"en":"CATEGORIES","de":"KATEGORIEN","fr":"CATÉGORIES","es":"CATEGORÍAS","it":"CATEGORIE","pt":"CATEGORIAS","nl":"CATEGORIEËN"},
    "VERİ": {"en":"DATA","de":"DATEN","fr":"DONNÉES","es":"DATOS","it":"DATI","pt":"DADOS","nl":"GEGEVENS"},
    "MOLA": {"en":"BREAKS","de":"PAUSEN","fr":"PAUSES","es":"DESCANSOS","it":"PAUSE","pt":"PAUSAS","nl":"PAUZES"},
    "GİZLİLİK": {"en":"PRIVACY","de":"DATENSCHUTZ","fr":"CONFIDENTIALITÉ","es":"PRIVACIDAD","it":"PRIVACY","pt":"PRIVACIDADE","nl":"PRIVACY"},
    "HEDEFLER": {"en":"GOALS","de":"ZIELE","fr":"OBJECTIFS","es":"OBJETIVOS","it":"OBIETTIVI","pt":"METAS","nl":"DOELEN"},
}


TRANS_8 = {}


# UI metinleri: tüm arayüzün seçilen dile çevrilmesi için ortak sözlük.
TRANSLATIONS.update({
    "◉  DİJİTAL DENGE": {"en":"◉  DIGITAL BALANCE","de":"◉  DIGITAL BALANCE","fr":"◉  ÉQUILIBRE NUMÉRIQUE","es":"◉  EQUILIBRIO DIGITAL","it":"◉  EQUILIBRIO DIGITALE","pt":"◉  EQUILÍBRIO DIGITAL","nl":"◉  DIGITAAL EVENWICHT"},
    "V3.0\nYerel veri • Gizlilik öncelikli": {"en":"V3.0\nLocal data • Privacy first","de":"V3.0\nLokale Daten • Datenschutz zuerst","fr":"V3.0\nDonnées locales • Confidentialité d’abord","es":"V3.0\nDatos locales • Privacidad primero","it":"V3.0\nDati locali • Privacy prima di tutto","pt":"V3.0\nDados locais • Privacidade em primeiro lugar","nl":"V3.0\nLokale gegevens • Privacy eerst"},
    "Bugünkü Aktif Süre":{"en":"Today's Active Time","de":"Aktive Zeit heute","fr":"Temps actif aujourd’hui","es":"Tiempo activo de hoy","it":"Tempo attivo di oggi","pt":"Tempo ativo de hoje","nl":"Actieve tijd vandaag"},
    "AFK hariç":{"en":"Excluding AFK","de":"Ohne AFK","fr":"Hors AFK","es":"Sin AFK","it":"Escluso AFK","pt":"Excluindo AFK","nl":"Exclusief AFK"},
    "Şu Anki Uygulama":{"en":"Current Application","de":"Aktuelle Anwendung","fr":"Application actuelle","es":"Aplicación actual","it":"Applicazione attuale","pt":"Aplicação atual","nl":"Huidige applicatie"},
    "AFK / Yok":{"en":"AFK / None","de":"AFK / Keine","fr":"AFK / Aucune","es":"AFK / Ninguna","it":"AFK / Nessuna","pt":"AFK / Nenhuma","nl":"AFK / Geen"},
    "canlı durum":{"en":"Live status","de":"Live-Status","fr":"État en direct","es":"Estado en vivo","it":"Stato live","pt":"Estado ao vivo","nl":"Live-status"},
    "Kesintisiz Kullanım":{"en":"Continuous Use","de":"Kontinuierliche Nutzung","fr":"Utilisation continue","es":"Uso continuo","it":"Uso continuo","pt":"Uso contínuo","nl":"Continu gebruik"},
    "2 saatte uyarı":{"en":"Warning after 2 hours","de":"Warnung nach 2 Stunden","fr":"Alerte après 2 heures","es":"Aviso después de 2 horas","it":"Avviso dopo 2 ore","pt":"Aviso após 2 horas","nl":"Waarschuwing na 2 uur"},
    "En Uzun Kullanım":{"en":"Longest Use","de":"Längste Nutzung","fr":"Plus longue utilisation","es":"Uso más largo","it":"Uso più lungo","pt":"Uso mais longo","nl":"Langste gebruik"},
    "bugünün rekoru":{"en":"Today's record","de":"Tagesrekord","fr":"Record du jour","es":"Récord de hoy","it":"Record di oggi","pt":"Recorde de hoje","nl":"Record van vandaag"},
    "Denge Skoru":{"en":"Balance Score","de":"Balance-Score","fr":"Score d’équilibre","es":"Puntuación de equilibrio","it":"Punteggio equilibrio","pt":"Pontuação de equilíbrio","nl":"Balansscore"},
    "davranış göstergesi":{"en":"Behavior indicator","de":"Verhaltensindikator","fr":"Indicateur comportemental","es":"Indicador de comportamiento","it":"Indicatore comportamentale","pt":"Indicador comportamental","nl":"Gedragsindicator"},
    "⚠  2 saattir mola vermeden kullanıyorsun. Kısa bir mola ver.":{"en":"⚠  You have been using the computer for 2 hours without a break. Take a short break.","de":"⚠  Du nutzt den Computer seit 2 Stunden ohne Pause. Mach eine kurze Pause.","fr":"⚠  Vous utilisez l’ordinateur depuis 2 heures sans pause. Faites une courte pause.","es":"⚠  Llevas 2 horas usando el ordenador sin descanso. Tómate una pausa breve.","it":"⚠  Usi il computer da 2 ore senza pausa. Fai una breve pausa.","pt":"⚠  Você usa o computador há 2 horas sem pausa. Faça uma pausa curta.","nl":"⚠  Je gebruikt de computer al 2 uur zonder pauze. Neem een korte pauze."},
    "BUGÜNÜN KULLANIMI":{"en":"TODAY'S USAGE","de":"NUTZUNG HEUTE","fr":"UTILISATION DU JOUR","es":"USO DE HOY","it":"UTILIZZO DI OGGI","pt":"USO DE HOJE","nl":"GEBRUIK VANDAAG"},
    "Henüz yeterli veri yok.":{"en":"Not enough data yet.","de":"Noch nicht genügend Daten.","fr":"Pas encore assez de données.","es":"Aún no hay suficientes datos.","it":"Dati ancora insufficienti.","pt":"Ainda não há dados suficientes.","nl":"Nog niet genoeg gegevens."},
    "SON 7 GÜN":{"en":"LAST 7 DAYS","de":"LETZTE 7 TAGE","fr":"7 DERNIERS JOURS","es":"ÚLTIMOS 7 DÍAS","it":"ULTIMI 7 GIORNI","pt":"ÚLTIMOS 7 DIAS","nl":"LAATSTE 7 DAGEN"},
    "GÜNLÜK HEDEF":{"en":"DAILY GOAL","de":"TAGESZIEL","fr":"OBJECTIF QUOTIDIEN","es":"OBJETIVO DIARIO","it":"OBIETTIVO GIORNALIERO","pt":"META DIÁRIA","nl":"DAGELIJKS DOEL"},
    "Bugün henüz uygulama verisi yok.":{"en":"No application data for today yet.","de":"Heute noch keine Anwendungsdaten.","fr":"Aucune donnée d’application pour aujourd’hui.","es":"Aún no hay datos de aplicaciones de hoy.","it":"Nessun dato applicativo per oggi.","pt":"Ainda não há dados de aplicações de hoje.","nl":"Nog geen applicatiegegevens voor vandaag."},
    "Kategori değiştir":{"en":"Change category","de":"Kategorie ändern","fr":"Changer de catégorie","es":"Cambiar categoría","it":"Cambia categoria","pt":"Alterar categoria","nl":"Categorie wijzigen"},
    "Oturum":{"en":"Sessions","de":"Sitzungen","fr":"Sessions","es":"Sesiones","it":"Sessioni","pt":"Sessões","nl":"Sessies"},
    "En uzun":{"en":"Longest","de":"Längste","fr":"Plus longue","es":"Más larga","it":"Più lunga","pt":"Mais longa","nl":"Langste"},
    "Yeni kategori adı":{"en":"New category name","de":"Name der neuen Kategorie","fr":"Nom de la nouvelle catégorie","es":"Nombre de la nueva categoría","it":"Nome della nuova categoria","pt":"Nome da nova categoria","nl":"Naam nieuwe categorie"},
    "Örn. Eğitim, Sosyal Medya, Tasarım...":{"en":"E.g. Education, Social Media, Design...","de":"z. B. Bildung, soziale Medien, Design...","fr":"Ex. Éducation, réseaux sociaux, design...","es":"Ej. Educación, redes sociales, diseño...","it":"Es. Istruzione, social media, design...","pt":"Ex. Educação, redes sociais, design...","nl":"Bijv. Onderwijs, sociale media, design..."},
    "Kategori, uygulama sınıflandırmasında kullanılabilir.":{"en":"The category can be used to classify applications.","de":"Die Kategorie kann zur Klassifizierung von Anwendungen verwendet werden.","fr":"La catégorie peut servir à classer les applications.","es":"La categoría puede usarse para clasificar aplicaciones.","it":"La categoria può essere usata per classificare le applicazioni.","pt":"A categoria pode ser usada para classificar aplicações.","nl":"De categorie kan worden gebruikt om applicaties te classificeren."},
    "Kategori Oluştur":{"en":"Create Category","de":"Kategorie erstellen","fr":"Créer une catégorie","es":"Crear categoría","it":"Crea categoria","pt":"Criar categoria","nl":"Categorie maken"},
    "Henüz tamamlanmış oturum yok.":{"en":"No completed sessions yet.","de":"Noch keine abgeschlossenen Sitzungen.","fr":"Aucune session terminée pour le moment.","es":"Aún no hay sesiones completadas.","it":"Nessuna sessione completata.","pt":"Ainda não há sessões concluídas.","nl":"Nog geen voltooide sessies."},
    "‹ Önceki Ay":{"en":"‹ Previous Month","de":"‹ Vorheriger Monat","fr":"‹ Mois précédent","es":"‹ Mes anterior","it":"‹ Mese precedente","pt":"‹ Mês anterior","nl":"‹ Vorige maand"},
    "Sonraki Ay ›":{"en":"Next Month ›","de":"Nächster Monat ›","fr":"Mois suivant ›","es":"Mes siguiente ›","it":"Mese successivo ›","pt":"Próximo mês ›","nl":"Volgende maand ›"},
    "KULLANIM TAKVİMİ":{"en":"USAGE CALENDAR","de":"NUTZUNGSKALENDER","fr":"CALENDRIER D’UTILISATION","es":"CALENDARIO DE USO","it":"CALENDARIO DI UTILIZZO","pt":"CALENDÁRIO DE USO","nl":"GEBRUIKSKALENDER"},
    "Bir güne tıkla; o güne ait toplam süre, uygulamalar, kategoriler, oturumlar ve molalar aşağıda açılır.":{"en":"Click a day to view its total time, applications, categories, sessions and breaks below.","de":"Klicke auf einen Tag, um Gesamtzeit, Anwendungen, Kategorien, Sitzungen und Pausen anzuzeigen.","fr":"Cliquez sur un jour pour voir la durée totale, les applications, les catégories, les sessions et les pauses.","es":"Haz clic en un día para ver el tiempo total, aplicaciones, categorías, sesiones y pausas.","it":"Fai clic su un giorno per vedere tempo totale, applicazioni, categorie, sessioni e pause.","pt":"Clique em um dia para ver tempo total, aplicações, categorias, sessões e pausas.","nl":"Klik op een dag om de totale tijd, applicaties, categorieën, sessies en pauzes te bekijken."},
    "Bilgisayarın hangi saatlerde aktif kullanıldığını gösterir.":{"en":"Shows which hours the computer was actively used.","de":"Zeigt, zu welchen Uhrzeiten der Computer aktiv genutzt wurde.","fr":"Affiche les heures d’utilisation active de l’ordinateur.","es":"Muestra a qué horas se utilizó activamente el ordenador.","it":"Mostra le ore in cui il computer è stato utilizzato attivamente.","pt":"Mostra em quais horas o computador foi usado ativamente.","nl":"Toont op welke uren de computer actief is gebruikt."},
    "AYLIK GÜNLÜK KULLANIM GRAFİĞİ":{"en":"MONTHLY DAILY USAGE CHART","de":"MONATLICHES TAGESNUTZUNGSDIAGRAMM","fr":"GRAPHIQUE MENSUEL D’UTILISATION QUOTIDIENNE","es":"GRÁFICO MENSUAL DE USO DIARIO","it":"GRAFICO MENSILE DELL’UTILIZZO GIORNALIERO","pt":"GRÁFICO MENSAL DE USO DIÁRIO","nl":"MAANDELIJKSE DAGELIJKSE GEBRUIKSGRAFIEK"},
    "AYLIK KATEGORİ ANALİZİ":{"en":"MONTHLY CATEGORY ANALYSIS","de":"MONATLICHE KATEGORIEANALYSE","fr":"ANALYSE MENSUELLE DES CATÉGORIES","es":"ANÁLISIS MENSUAL POR CATEGORÍAS","it":"ANALISI MENSILE DELLE CATEGORIE","pt":"ANÁLISE MENSAL DE CATEGORIAS","nl":"MAANDELIJKSE CATEGORIEANALYSE"},
    "Bu ay için kategori verisi bulunmuyor.":{"en":"No category data for this month.","de":"Keine Kategoriedaten für diesen Monat.","fr":"Aucune donnée de catégorie pour ce mois.","es":"No hay datos de categorías para este mes.","it":"Nessun dato di categoria per questo mese.","pt":"Não há dados de categorias para este mês.","nl":"Geen categoriedata voor deze maand."},
    "Bu tarihte uygulama kullanımı bulunmuyor.":{"en":"No application usage for this date.","de":"Keine Anwendungsnutzung an diesem Datum.","fr":"Aucune utilisation d’application à cette date.","es":"No hay uso de aplicaciones en esta fecha.","it":"Nessun utilizzo di applicazioni in questa data.","pt":"Não há uso de aplicações nesta data.","nl":"Geen applicatiegebruik op deze datum."},
    "Toplam":{"en":"Total","de":"Gesamt","fr":"Total","es":"Total","it":"Totale","pt":"Total","nl":"Totaal"},
    "UYGULAMA KULLANIMI • DAİRE GRAFİĞİ":{"en":"APPLICATION USAGE • DONUT CHART","de":"ANWENDUNGSNUTZUNG • RINGDIAGRAMM","fr":"UTILISATION DES APPLICATIONS • DIAGRAMME EN ANNEAU","es":"USO DE APLICACIONES • GRÁFICO DE DONUT","it":"UTILIZZO APPLICAZIONI • GRAFICO A CIAMBELLA","pt":"USO DE APLICAÇÕES • GRÁFICO DE ROSCA","nl":"APPLICATIEGEBRUIK • DONUTGRAFIEK"},
    "UYGULAMA KULLANIMI":{"en":"APPLICATION USAGE","de":"ANWENDUNGSNUTZUNG","fr":"UTILISATION DES APPLICATIONS","es":"USO DE APLICACIONES","it":"UTILIZZO DELLE APPLICAZIONI","pt":"USO DE APLICAÇÕES","nl":"APPLICATIEGEBRUIK"},
    "Bu tarihte kayıtlı uygulama verisi yok.":{"en":"No application data recorded for this date.","de":"Keine Anwendungsdaten für dieses Datum aufgezeichnet.","fr":"Aucune donnée d’application enregistrée pour cette date.","es":"No hay datos de aplicaciones registrados para esta fecha.","it":"Nessun dato applicativo registrato per questa data.","pt":"Nenhum dado de aplicação registado para esta data.","nl":"Geen applicatiegegevens geregistreerd voor deze datum."},
    "OTURUMLAR":{"en":"SESSIONS","de":"SITZUNGEN","fr":"SESSIONS","es":"SESIONES","it":"SESSIONI","pt":"SESSÕES","nl":"SESSIES"},
    "Bu tarihte oturum kaydı yok.":{"en":"No session record for this date.","de":"Keine Sitzung für dieses Datum.","fr":"Aucune session enregistrée pour cette date.","es":"No hay registro de sesiones para esta fecha.","it":"Nessun registro di sessione per questa data.","pt":"Não há registo de sessão para esta data.","nl":"Geen sessieregistratie voor deze datum."},
    "Saat":{"en":"Time","de":"Uhrzeit","fr":"Heure","es":"Hora","it":"Ora","pt":"Hora","nl":"Tijd"},
    "Uygulama / Oturum":{"en":"Application / Session","de":"Anwendung / Sitzung","fr":"Application / Session","es":"Aplicación / Sesión","it":"Applicazione / Sessione","pt":"Aplicação / Sessão","nl":"Applicatie / Sessie"},
    "Kategori":{"en":"Category","de":"Kategorie","fr":"Catégorie","es":"Categoría","it":"Categoria","pt":"Categoria","nl":"Categorie"},
    "Süre":{"en":"Duration","de":"Dauer","fr":"Durée","es":"Duración","it":"Durata","pt":"Duração","nl":"Duur"},
    "Sistem":{"en":"System","de":"System","fr":"Système","es":"Sistema","it":"Sistema","pt":"Sistema","nl":"Systeem"},
    "GÜNLÜK SAATLİK KULLANIM":{"en":"DAILY HOURLY USAGE","de":"TÄGLICHE STÜNDLICHE NUTZUNG","fr":"UTILISATION HORAIRE QUOTIDIENNE","es":"USO HORARIO DIARIO","it":"UTILIZZO ORARIO GIORNALIERO","pt":"USO HORÁRIO DIÁRIO","nl":"DAGELIJKS GEBRUIK PER UUR"},
    "SEÇİLEN TARİH":{"en":"SELECTED DATE","de":"AUSGEWÄHLTES DATUM","fr":"DATE SÉLECTIONNÉE","es":"FECHA SELECCIONADA","it":"DATA SELEZIONATA","pt":"DATA SELECIONADA","nl":"GESELECTEERDE DATUM"},
    "Toplam aktif kullanım":{"en":"Total active usage","de":"Gesamte aktive Nutzung","fr":"Utilisation active totale","es":"Uso activo total","it":"Utilizzo attivo totale","pt":"Uso ativo total","nl":"Totale actieve gebruik"},
    "En uzun kesintisiz kullanım":{"en":"Longest continuous use","de":"Längste kontinuierliche Nutzung","fr":"Plus longue utilisation continue","es":"Uso continuo más largo","it":"Utilizzo continuo più lungo","pt":"Uso contínuo mais longo","nl":"Langste continue gebruik"},
    "Tüm kullanım verileri silinsin mi?":{"en":"Delete all usage data?","de":"Alle Nutzungsdaten löschen?","fr":"Supprimer toutes les données d’utilisation ?","es":"¿Eliminar todos los datos de uso?","it":"Eliminare tutti i dati di utilizzo?","pt":"Eliminar todos os dados de utilização?","nl":"Alle gebruiksgegevens verwijderen?"},
    "Mola":{"en":"Breaks","de":"Pausen","fr":"Pauses","es":"Pausas","it":"Pause","pt":"Pausas","nl":"Pauzes"},
    "Uygulamanın aydınlık, karanlık veya Windows sistem temasını kullanmasını seç.":{"en":"Choose light, dark, or Windows system theme for the application.","de":"Wähle ein helles, dunkles oder Windows-Systemdesign.","fr":"Choisissez le thème clair, sombre ou système Windows.","es":"Elige el tema claro, oscuro o del sistema de Windows.","it":"Scegli il tema chiaro, scuro o di sistema Windows.","pt":"Escolha o tema claro, escuro ou do sistema Windows.","nl":"Kies een licht, donker of Windows-systeemthema."},
    "Otomatik kategorilere ek olarak kendi kategorilerini oluşturabilir ve Uygulamalar sayfasında uygulamalara atayabilirsin.":{"en":"You can create custom categories and assign them to applications on the Applications page.","de":"Du kannst eigene Kategorien erstellen und sie auf der Anwendungsseite zuweisen.","fr":"Vous pouvez créer des catégories personnalisées et les attribuer aux applications.","es":"Puedes crear categorías personalizadas y asignarlas a aplicaciones.","it":"Puoi creare categorie personalizzate e assegnarle alle applicazioni.","pt":"Pode criar categorias personalizadas e atribuí-las às aplicações.","nl":"Je kunt aangepaste categorieën maken en ze op de applicatiepagina toewijzen."},
    "＋ Yeni Kategori":{"en":"＋ New Category","de":"＋ Neue Kategorie","fr":"＋ Nouvelle catégorie","es":"＋ Nueva categoría","it":"＋ Nuova categoria","pt":"＋ Nova categoria","nl":"＋ Nieuwe categorie"},
    "Verileri dışa aktar (JSON)":{"en":"Export Data (JSON)","de":"Daten exportieren (JSON)","fr":"Exporter les données (JSON)","es":"Exportar datos (JSON)","it":"Esporta dati (JSON)","pt":"Exportar dados (JSON)","nl":"Gegevens exporteren (JSON)"},
    "Verileri sıfırla":{"en":"Reset Data","de":"Daten zurücksetzen","fr":"Réinitialiser les données","es":"Restablecer datos","it":"Reimposta dati","pt":"Redefinir dados","nl":"Gegevens resetten"},
    "Yerel dosya:":{"en":"Local file:","de":"Lokale Datei:","fr":"Fichier local :","es":"Archivo local:","it":"File locale:","pt":"Ficheiro local:","nl":"Lokaal bestand:"},
    "Log:":{"en":"Log:","de":"Protokoll:","fr":"Journal :","es":"Registro:","it":"Log:","pt":"Registo:","nl":"Logboek:"},
    "Kaydet":{"en":"Save","de":"Speichern","fr":"Enregistrer","es":"Guardar","it":"Salva","pt":"Guardar","nl":"Opslaan"},
    "Boşta kalma süresi (dakika)":{"en":"Idle threshold (minutes)","de":"Leerlaufzeit (Minuten)","fr":"Seuil d’inactivité (minutes)","es":"Umbral de inactividad (minutos)","it":"Soglia di inattività (minuti)","pt":"Limite de inatividade (minutos)","nl":"Inactiviteitsdrempel (minuten)"},
    "Mola hatırlatıcısı (dakika)":{"en":"Break reminder (minutes)","de":"Pausenerinnerung (Minuten)","fr":"Rappel de pause (minutes)","es":"Recordatorio de pausa (minutos)","it":"Promemoria pausa (minuti)","pt":"Lembrete de pausa (minutos)","nl":"Pauzeherinnering (minuten)"},
    "Mola hatırlatıcısını etkinleştir":{"en":"Enable break reminder","de":"Pausenerinnerung aktivieren","fr":"Activer le rappel de pause","es":"Activar recordatorio de pausa","it":"Attiva promemoria pausa","pt":"Ativar lembrete de pausa","nl":"Pauzeherinnering inschakelen"},
    "Sesli uyarı":{"en":"Sound alert","de":"Tonwarnung","fr":"Alerte sonore","es":"Alerta sonora","it":"Avviso sonoro","pt":"Alerta sonora","nl":"Geluidswaarschuwing"},
    "Web takibi (yalnızca pencere başlığı; URL kaydı yok)":{"en":"Web tracking (window title only; no URL recording)","de":"Web-Tracking (nur Fenstertitel; keine URLs)","fr":"Suivi web (titre de fenêtre uniquement ; aucune URL)","es":"Seguimiento web (solo título de ventana; sin URL)","it":"Monitoraggio web (solo titolo finestra; nessun URL)","pt":"Monitorização web (apenas título da janela; sem URLs)","nl":"Webtracking (alleen venstertitel; geen URL's)"},
    "Günlük aktif kullanım hedefi (saat)":{"en":"Daily active usage goal (hours)","de":"Tägliches aktives Nutzungsziel (Stunden)","fr":"Objectif quotidien d’utilisation active (heures)","es":"Objetivo diario de uso activo (horas)","it":"Obiettivo giornaliero di utilizzo attivo (ore)","pt":"Meta diária de uso ativo (horas)","nl":"Dagelijks doel voor actief gebruik (uren)"},
})

TRANSLATIONS.update({
    # Kategoriler
    "Mühendislik":{"en":"Engineering","de":"Ingenieurwesen","fr":"Ingénierie","es":"Ingeniería","it":"Ingegneria","pt":"Engenharia","nl":"Techniek"},
    "Geliştirme":{"en":"Development","de":"Entwicklung","fr":"Développement","es":"Desarrollo","it":"Sviluppo","pt":"Desenvolvimento","nl":"Ontwikkeling"},
    "Çalışma":{"en":"Work","de":"Arbeit","fr":"Travail","es":"Trabajo","it":"Lavoro","pt":"Trabalho","nl":"Werk"},
    "Belge":{"en":"Documents","de":"Dokumente","fr":"Documents","es":"Documentos","it":"Documenti","pt":"Documentos","nl":"Documenten"},
    "Tasarım":{"en":"Design","de":"Design","fr":"Design","es":"Diseño","it":"Design","pt":"Design","nl":"Ontwerp"},
    "Tarayıcı":{"en":"Browser","de":"Browser","fr":"Navigateur","es":"Navegador","it":"Browser","pt":"Navegador","nl":"Browser"},
    "İletişim":{"en":"Communication","de":"Kommunikation","fr":"Communication","es":"Comunicación","it":"Comunicazione","pt":"Comunicação","nl":"Communicatie"},
    "Oyun":{"en":"Games","de":"Spiele","fr":"Jeux","es":"Juegos","it":"Giochi","pt":"Jogos","nl":"Games"},
    "Medya":{"en":"Media","de":"Medien","fr":"Médias","es":"Medios","it":"Media","pt":"Mídia","nl":"Media"},
    "Diğer":{"en":"Other","de":"Andere","fr":"Autre","es":"Otros","it":"Altro","pt":"Outro","nl":"Overig"},
    "Bilinmeyen":{"en":"Unknown","de":"Unbekannt","fr":"Inconnu","es":"Desconocido","it":"Sconosciuto","pt":"Desconhecido","nl":"Onbekend"},
    # Günler
    "Pzt":{"en":"Mon","de":"Mo","fr":"Lun","es":"Lun","it":"Lun","pt":"Seg","nl":"Ma"},
    "Sal":{"en":"Tue","de":"Di","fr":"Mar","es":"Mar","it":"Mar","pt":"Ter","nl":"Di"},
    "Çar":{"en":"Wed","de":"Mi","fr":"Mer","es":"Mié","it":"Mer","pt":"Qua","nl":"Wo"},
    "Per":{"en":"Thu","de":"Do","fr":"Jeu","es":"Jue","it":"Gio","pt":"Qui","nl":"Do"},
    "Cum":{"en":"Fri","de":"Fr","fr":"Ven","es":"Vie","it":"Ven","pt":"Sex","nl":"Vr"},
    "Cmt":{"en":"Sat","de":"Sa","fr":"Sam","es":"Sáb","it":"Sab","pt":"Sáb","nl":"Za"},
    "Paz":{"en":"Sun","de":"So","fr":"Dim","es":"Dom","it":"Dom","pt":"Dom","nl":"Zo"},
    # Dashboard / özet kartları
    "Ay toplamı":{"en":"Monthly total","de":"Monatssumme","fr":"Total mensuel","es":"Total mensual","it":"Totale mensile","pt":"Total mensal","nl":"Maandtotaal"},
    "Günlük ortalama":{"en":"Daily average","de":"Tagesdurchschnitt","fr":"Moyenne quotidienne","es":"Promedio diario","it":"Media giornaliera","pt":"Média diária","nl":"Dagelijks gemiddelde"},
    "En uzun kesintisiz":{"en":"Longest continuous","de":"Längste ununterbrochene Nutzung","fr":"Plus longue utilisation continue","es":"Uso continuo más largo","it":"Utilizzo continuo più lungo","pt":"Uso contínuo mais longo","nl":"Langste ononderbroken gebruik"},
    "Toplam mola":{"en":"Total breaks","de":"Pausen insgesamt","fr":"Total des pauses","es":"Total de pausas","it":"Pause totali","pt":"Total de pausas","nl":"Totale pauzes"},
    "aktif gün":{"en":"active days","de":"aktive Tage","fr":"jours actifs","es":"días activos","it":"giorni attivi","pt":"dias ativos","nl":"actieve dagen"},
    "aktif gün ortalaması":{"en":"average of active days","de":"Durchschnitt der aktiven Tage","fr":"moyenne des jours actifs","es":"promedio de los días activos","it":"media dei giorni attivi","pt":"média dos dias ativos","nl":"gemiddelde van actieve dagen"},
    "ay içindeki rekor":{"en":"monthly record","de":"Monatsrekord","fr":"record du mois","es":"récord mensual","it":"record mensile","pt":"recorde mensal","nl":"maandrecord"},
    "kayıtlı mola":{"en":"recorded breaks","de":"erfasste Pausen","fr":"pauses enregistrées","es":"pausas registradas","it":"pause registrate","pt":"pausas registadas","nl":"geregistreerde pauzes"},
    "Seçili tarih yok":{"en":"No date selected","de":"Kein Datum ausgewählt","fr":"Aucune date sélectionnée","es":"Ninguna fecha seleccionada","it":"Nessuna data selezionata","pt":"Nenhuma data selecionada","nl":"Geen datum geselecteerd"},
    "Günlük veriye tıklayarak ayrıntıları görebilirsin":{"en":"Click a day to see details","de":"Klicke auf einen Tag für Details","fr":"Cliquez sur un jour pour voir les détails","es":"Haz clic en un día para ver los detalles","it":"Fai clic su un giorno per i dettagli","pt":"Clique num dia para ver os detalhes","nl":"Klik op een dag voor details"},
    "GÜNLÜK SAATLİK KULLANIM":{"en":"DAILY HOURLY USAGE","de":"TÄGLICHE STÜNDLICHE NUTZUNG","fr":"UTILISATION HORAIRE QUOTIDIENNE","es":"USO HORARIO DIARIO","it":"UTILIZZO ORARIO GIORNALIERO","pt":"USO HORÁRIO DIÁRIO","nl":"DAGELIJKS GEBRUIK PER UUR"},
    "Bugünkü molalar: ":{"en":"Today's breaks: ","de":"Pausen heute: ","fr":"Pauses aujourd’hui : ","es":"Pausas de hoy: ","it":"Pause di oggi: ","pt":"Pausas de hoje: ","nl":"Pauzes vandaag: "},
    "Yeni kategori":{"en":"New category","de":"Neue Kategorie","fr":"Nouvelle catégorie","es":"Nueva categoría","it":"Nuova categoria","pt":"Nova categoria","nl":"Nieuwe categorie"},
    "0 dk":{"en":"0 min","de":"0 Min.","fr":"0 min","es":"0 min","it":"0 min","pt":"0 min","nl":"0 min"},
    # Genel UI
    "Uygulama":{"en":"Application","de":"Anwendung","fr":"Application","es":"Aplicación","it":"Applicazione","pt":"Aplicação","nl":"Applicatie"},
    "Oturum: ":{"en":"Session: ","de":"Sitzung: ","fr":"Session : ","es":"Sesión: ","it":"Sessione: ","pt":"Sessão: ","nl":"Sessie: "},
    "En uzun: ":{"en":"Longest: ","de":"Längste: ","fr":"Plus longue : ","es":"Más larga: ","it":"Più lunga: ","pt":"Mais longa: ","nl":"Langste: "},
})

TRANSLATIONS.update({
    "VERİ SIFIRLAMA":{"en":"DATA RESET","de":"DATEN ZURÜCKSETZEN","fr":"RÉINITIALISATION DES DONNÉES","es":"RESTABLECER DATOS","it":"RESET DATI","pt":"REDEFINIR DADOS","nl":"GEGEVENS RESETTEN"},
    "Aylık Veri Sıfırlama":{"en":"Monthly Data Reset","de":"Monatliche Daten zurücksetzen","fr":"Réinitialisation mensuelle des données","es":"Restablecer datos mensuales","it":"Reimposta dati mensili","pt":"Redefinir dados mensais","nl":"Maandelijkse gegevens resetten"},
    "Aşağıdaki aydaki tüm kullanım kayıtlarını sil":{"en":"Delete all usage records for the selected month","de":"Alle Nutzungsdaten des ausgewählten Monats löschen","fr":"Supprimer toutes les données d’utilisation du mois sélectionné","es":"Eliminar todos los registros de uso del mes seleccionado","it":"Elimina tutti i dati di utilizzo del mese selezionato","pt":"Excluir todos os registros de uso do mês selecionado","nl":"Alle gebruiksgegevens van de geselecteerde maand verwijderen"},
    "Ay seçin":{"en":"Select month","de":"Monat auswählen","fr":"Sélectionner le mois","es":"Seleccionar mes","it":"Seleziona mese","pt":"Selecionar mês","nl":"Maand selecteren"},
    "Seçilen Ayın Verilerini Sıfırla":{"en":"Reset Selected Month","de":"Ausgewählten Monat zurücksetzen","fr":"Réinitialiser le mois sélectionné","es":"Restablecer mes seleccionado","it":"Reimposta mese selezionato","pt":"Redefinir mês selecionado","nl":"Geselecteerde maand resetten"},
    "Tüm Verileri Sıfırla":{"en":"Reset All Data","de":"Alle Daten zurücksetzen","fr":"Réinitialiser toutes les données","es":"Restablecer todos los datos","it":"Reimposta tutti i dati","pt":"Redefinir todos os dados","nl":"Alle gegevens resetten"},
    "Seçilen ayın tüm kullanım verileri silinecek. Bu işlem geri alınamaz.":{"en":"All usage data for the selected month will be deleted. This cannot be undone.","de":"Alle Nutzungsdaten des ausgewählten Monats werden gelöscht. Dies kann nicht rückgängig gemacht werden.","fr":"Toutes les données d’utilisation du mois sélectionné seront supprimées. Cette action est irréversible.","es":"Se eliminarán todos los datos de uso del mes seleccionado. Esta acción no se puede deshacer.","it":"Tutti i dati di utilizzo del mese selezionato verranno eliminati. L’operazione non può essere annullata.","pt":"Todos os dados de uso do mês selecionado serão excluídos. Esta ação não pode ser desfeita.","nl":"Alle gebruiksgegevens van de geselecteerde maand worden verwijderd. Dit kan niet ongedaan worden gemaakt."},
    "Sistemde tutulan tüm kullanım verileri silinecek. Ayarlar korunur ve bu işlem geri alınamaz.":{"en":"All stored usage data will be deleted. Settings will be preserved and this cannot be undone.","de":"Alle gespeicherten Nutzungsdaten werden gelöscht. Einstellungen bleiben erhalten und dies kann nicht rückgängig gemacht werden.","fr":"Toutes les données d’utilisation stockées seront supprimées. Les paramètres seront conservés et cette action est irréversible.","es":"Se eliminarán todos los datos de uso almacenados. La configuración se conservará y esta acción no se puede deshacer.","it":"Tutti i dati di utilizzo memorizzati verranno eliminati. Le impostazioni saranno conservate e l’operazione non può essere annullata.","pt":"Todos os dados de uso armazenados serão excluídos. As configurações serão mantidas e esta ação não pode ser desfeita.","nl":"Alle opgeslagen gebruiksgegevens worden verwijderd. Instellingen blijven behouden en dit kan niet ongedaan worden gemaakt."},
})

# ============================================================
# GENİŞLETİLMİŞ ÇOK DİLLİ UI ÇEVİRİLERİ
# ============================================================
TRANS_8['MENÜ'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['القائمة', 'NABÍDKA', 'MENÜ', 'メニュー', '메뉴', 'MENU', 'МЕНЮ', '菜单']))
TRANS_8['Genel Bakış'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['نظرة عامة', 'Přehled', 'Áttekintés', '概要', '개요', 'Przegląd', 'Обзор', '概览']))
TRANS_8['Uygulamalar'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التطبيقات', 'Aplikace', 'Alkalmazások', 'アプリ', '앱', 'Aplikacje', 'Приложения', '应用程序']))
TRANS_8['Oturumlar'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الجلسات', 'Relace', 'Munkamenetek', 'セッション', '세션', 'Sesje', 'Сеансы', '会话']))
TRANS_8['İstatistikler'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الإحصائيات', 'Statistiky', 'Statisztikák', '統計', '통계', 'Statystyki', 'Статистика', '统计']))
TRANS_8['Ayarlar'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الإعدادات', 'Nastavení', 'Beállítások', '設定', '설정', 'Ustawienia', 'Настройки', '设置']))
TRANS_8['Merhaba!'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['مرحبًا!', 'Ahoj!', 'Szia!', 'こんにちは！', '안녕하세요!', 'Cześć!', 'Здравствуйте!', '你好！']))
TRANS_8['Bugünkü dijital dengen nasıl?'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['كيف هو توازنك الرقمي اليوم؟', 'Jaká je dnes vaše digitální rovnováha?', 'Milyen a digitális egyensúlyod ma?', '今日のデジタルバランスはいかがですか？', '오늘의 디지털 균형은 어떤가요?', 'Jak wygląda dziś Twoja równowaga cyfrowa?', 'Как ваш цифровой баланс сегодня?', '今天的数字平衡如何？']))
TRANS_8['Daha iyi bir sen mümkün.'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['نسخة أفضل منك ممكنة.', 'Lepší verze vás je možná.', 'Egy jobb ön lehetséges.', 'もっと良い自分へ。', '더 나은 나를 만들 수 있어요.', 'Możesz być lepszą wersją siebie.', 'Возможна лучшая версия вас.', '更好的自己是可能的。']))
TRANS_8['Bugünkü Aktif Süre'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['وقت النشاط اليوم', 'Dnešní aktivní čas', 'Mai aktív idő', '今日のアクティブ時間', '오늘의 활성 시간', 'Dzisiejszy czas aktywności', 'Активное время сегодня', '今日活跃时间']))
TRANS_8['Şu Anki Uygulama'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التطبيق الحالي', 'Aktuální aplikace', 'Jelenlegi alkalmazás', '現在のアプリ', '현재 앱', 'Bieżąca aplikacja', 'Текущее приложение', '当前应用']))
TRANS_8['Günlük hedef'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الهدف اليومي', 'Denní cíl', 'Napi cél', '1日の目標', '일일 목표', 'Cel dzienny', 'Дневная цель', '每日目标']))
TRANS_8['Tümünü Gör'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['عرض الكل', 'Zobrazit vše', 'Összes megtekintése', 'すべて表示', '모두 보기', 'Zobacz wszystko', 'Показать все', '查看全部']))
TRANS_8['Bugün henüz uygulama verisi yok.'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد بيانات تطبيقات لليوم بعد.', 'Dnes nejsou k dispozici žádná data aplikací.', 'Ma még nincs alkalmazásadat.', '今日はまだアプリデータがありません。', '오늘은 아직 앱 데이터가 없습니다.', 'Brak jeszcze danych aplikacji na dziś.', 'За сегодня данных приложений пока нет.', '今天还没有应用数据。']))
TRANS_8['uygulama'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تطبيق', 'aplikace', 'alkalmazás', 'アプリ', '앱', 'aplikacja', 'приложение', '应用']))
TRANS_8['Oturum:'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الجلسة:', 'Relace:', 'Munkamenet:', 'セッション:', '세션:', 'Sesja:', 'Сеанс:', '会话：']))
TRANS_8['En uzun:'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الأطول:', 'Nejdelší:', 'Leghosszabb:', '最長:', '가장 긴 시간:', 'Najdłuższa:', 'Самая длинная:', '最长：']))
TRANS_8['Kategori değiştir'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تغيير الفئة', 'Změnit kategorii', 'Kategória módosítása', 'カテゴリを変更', '카테고리 변경', 'Zmień kategorię', 'Изменить категорию', '更改类别']))
TRANS_8['kategorisi'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الفئة', 'kategorie', 'kategória', 'カテゴリ', '카테고리', 'kategoria', 'категория', '类别']))
TRANS_8['Kaydet'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['حفظ', 'Uložit', 'Mentés', '保存', '저장', 'Zapisz', 'Сохранить', '保存']))
TRANS_8['Yeni kategori'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['فئة جديدة', 'Nová kategorie', 'Új kategória', '新しいカテゴリ', '새 카테고리', 'Nowa kategoria', 'Новая категория', '新类别']))
TRANS_8['Yeni kategori adı'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اسم الفئة الجديدة', 'Název nové kategorie', 'Új kategória neve', '新しいカテゴリ名', '새 카테고리 이름', 'Nazwa nowej kategorii', 'Название новой категории', '新类别名称']))
TRANS_8['Kategori Oluştur'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إنشاء فئة', 'Vytvořit kategorii', 'Kategória létrehozása', 'カテゴリを作成', '카테고리 만들기', 'Utwórz kategorię', 'Создать категорию', '创建类别']))
TRANS_8['Bugünkü Oturumlar'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['جلسات اليوم', 'Dnešní relace', 'Mai munkamenetek', '今日のセッション', '오늘의 세션', 'Dzisiejsze sesje', 'Сеансы сегодня', '今日会话']))
TRANS_8['Zaman Çizelgesi'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الخط الزمني', 'Časová osa', 'Idővonal', 'タイムライン', '타임라인', 'Oś czasu', 'Временная шкала', '时间线']))
TRANS_8['Mola'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['استراحة', 'Pauza', 'Szünet', '休憩', '휴식', 'Przerwa', 'Перерыв', '休息']))
TRANS_8['Dinlenme'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['راحة', 'Odpočinek', 'Pihenés', '休息', '휴식', 'Odpoczynek', 'Отдых', '休息']))
TRANS_8['Detaylı kullanım analizi'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تحليل مفصل للاستخدام', 'Podrobná analýza používání', 'Részletes használati elemzés', '詳細な使用状況分析', '상세 사용 분석', 'Szczegółowa analiza użytkowania', 'Подробный анализ использования', '详细使用分析']))
TRANS_8['Analiz dönemi'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['فترة التحليل', 'Období analýzy', 'Elemzési időszak', '分析期間', '분석 기간', 'Okres analizy', 'Период анализа', '分析周期']))
TRANS_8['aktif gün'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['يوم نشط', 'aktivní den', 'aktív nap', 'アクティブ日', '활성 일수', 'aktywny dzień', 'активный день', '活跃天数']))
TRANS_8['Günlük Ortalama'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['المتوسط اليومي', 'Denní průměr', 'Napi átlag', '1日の平均', '일일 평균', 'Średnia dzienna', 'Среднее за день', '每日平均']))
TRANS_8['En Verimli Gün'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اليوم الأكثر نشاطًا', 'Nejaktivnější den', 'Legaktívabb nap', '最もアクティブな日', '가장 활동적인 날', 'Najaktywniejszy dzień', 'Самый активный день', '最活跃的一天']))
TRANS_8['En Yoğun Kategori'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الفئة الأكثر استخدامًا', 'Nejčastější kategorie', 'Leggyakoribb kategória', '最多使用的类别', '가장 많이 사용한 카테고리', 'Najczęściej używana kategoria', 'Самая используемая категория', '最常用类别']))
TRANS_8['Günlük Kullanım Süresi'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['مدة الاستخدام اليومية', 'Denní doba používání', 'Napi használati idő', '1日の使用時間', '일일 사용 시간', 'Dzienny czas użytkowania', 'Ежедневное время использования', '每日使用时间']))
TRANS_8['Kategori Dağılımı'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['توزيع الفئات', 'Rozložení kategorií', 'Kategóriaeloszlás', 'カテゴリ分布', '카테고리 분포', 'Rozkład kategorii', 'Распределение категорий', '类别分布']))
TRANS_8['Kategori Bazında Günlük Dağılım'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التوزيع اليومي حسب الفئة', 'Denní rozložení podle kategorií', 'Napi kategória szerinti megoszlás', 'カテゴリ別の1日の分布', '카테고리별 일일 분포', 'Dzienny podział według kategorii', 'Ежедневное распределение по категориям', '按类别划分的每日分布']))
TRANS_8['En Çok Kullanılan Uygulamalar'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التطبيقات الأكثر استخدامًا', 'Nejpoužívanější aplikace', 'Leggyakrabban használt alkalmazások', '最も使用されたアプリ', '가장 많이 사용한 앱', 'Najczęściej używane aplikacje', 'Самые используемые приложения', '最常用应用']))
TRANS_8['Kullanım Trendleri'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اتجاهات الاستخدام', 'Trendy používání', 'Használati trendek', '使用傾向', '사용 추세', 'Trendy użytkowania', 'Тенденции использования', '使用趋势']))
TRANS_8['Karşılaştırma'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['مقارنة', 'Porovnání', 'Összehasonlítás', '比較', '비교', 'Porównanie', 'Сравнение', '比较']))
TRANS_8['Seçilen Gün'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اليوم المحدد', 'Vybraný den', 'Kiválasztott nap', '選択した日', '선택한 날짜', 'Wybrany dzień', 'Выбранный день', '所选日期']))
TRANS_8['Kullanım Takvimi'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تقويم الاستخدام', 'Kalendář používání', 'Használati naptár', '使用カレンダー', '사용 캘린더', 'Kalendarz użytkowania', 'Календарь использования', '使用日历']))
TRANS_8['Veri yok'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد بيانات', 'Žádná data', 'Nincs adat', 'データなし', '데이터 없음', 'Brak danych', 'Нет данных', '无数据']))
TRANS_8['Henüz veri yok.'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد بيانات بعد.', 'Zatím žádná data.', 'Még nincs adat.', 'まだデータがありません。', '아직 데이터가 없습니다.', 'Brak danych.', 'Данных пока нет.', '暂无数据。']))
TRANS_8['Toplam Kullanım'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إجمالي الاستخدام', 'Celkové použití', 'Teljes használat', '総使用時間', '총 사용량', 'Łączne użycie', 'Общее использование', '总使用量']))
TRANS_8['7 Gün'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['7 أيام', '7 dní', '7 nap', '7日間', '7일', '7 dni', '7 дней', '7天']))
TRANS_8['Geçen hafta'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الأسبوع الماضي', 'Minulý týden', 'Múlt hét', '先週', '지난주', 'Zeszły tydzień', 'Прошлая неделя', '上周']))
TRANS_8['Toplam aktif kullanım'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إجمالي الاستخدام النشط', 'Celkové aktivní používání', 'Teljes aktív használat', '総アクティブ使用時間', '총 활성 사용량', 'Łączny aktywny czas', 'Общее активное использование', '总活跃使用时间']))
TRANS_8['En uzun kesintisiz kullanım'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['أطول استخدام متواصل', 'Nejdelší nepřetržité používání', 'Leghosszabb folyamatos használat', '最長連続使用', '가장 긴 연속 사용', 'Najdłuższe nieprzerwane użycie', 'Самое длительное непрерывное использование', '最长连续使用']))
TRANS_8['Uygulama'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التطبيق', 'Aplikace', 'Alkalmazás', 'アプリ', '앱', 'Aplikacja', 'Приложение', '应用']))
TRANS_8['Kategori'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الفئة', 'Kategorie', 'Kategória', 'カテゴリ', '카테고리', 'Kategoria', 'Категория', '类别']))
TRANS_8['Süre'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['المدة', 'Doba', 'Időtartam', '時間', '시간', 'Czas', 'Продолжительность', '时长']))
TRANS_8['Pay'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['النسبة', 'Podíl', 'Arány', '割合', '비율', 'Udział', 'Доля', '占比']))
TRANS_8['GÖRÜNÜM'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['المظهر', 'VZHLED', 'MEGJELENÉS', '外観', '화면', 'WYGLĄD', 'ВИД', '外观']))
TRANS_8['Dil'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اللغة', 'Jazyk', 'Nyelv', '言語', '언어', 'Język', 'Язык', '语言']))
TRANS_8['Windows ile başlat'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التشغيل مع Windows', 'Spustit se systémem Windows', 'Indítás a Windowszal', 'Windowsで起動', 'Windows와 함께 시작', 'Uruchamiaj z Windows', 'Запускать вместе с Windows', '随 Windows 启动']))
TRANS_8['KATEGORİLER'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الفئات', 'KATEGORIE', 'KATEGÓRIÁK', 'カテゴリ', '카테고리', 'KATEGORIE', 'КАТЕГОРИИ', '类别']))
TRANS_8['VERİ'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['البيانات', 'DATA', 'ADATOK', 'データ', '데이터', 'DANE', 'ДАННЫЕ', '数据']))
TRANS_8['Verileri dışa aktar (JSON)'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تصدير البيانات (JSON)', 'Exportovat data (JSON)', 'Adatok exportálása (JSON)', 'データをエクスポート (JSON)', '데이터 내보내기 (JSON)', 'Eksportuj dane (JSON)', 'Экспортировать данные (JSON)', '导出数据 (JSON)']))
TRANS_8['VERİ SIFIRLAMA'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إعادة تعيين البيانات', 'RESET DAT', 'ADATOK VISSZAÁLLÍTÁSA', 'データリセット', '데이터 초기화', 'RESET DANYCH', 'СБРОС ДАННЫХ', '数据重置']))
TRANS_8['Aylık Veri Sıfırlama'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إعادة تعيين البيانات الشهرية', 'Měsíční reset dat', 'Havi adatok visszaállítása', '月間データをリセット', '월간 데이터 초기화', 'Reset danych miesięcznych', 'Сброс данных за месяц', '重置月度数据']))
TRANS_8['Ay seçin'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اختر الشهر', 'Vyberte měsíc', 'Válasszon hónapot', '月を選択', '월 선택', 'Wybierz miesiąc', 'Выберите месяц', '选择月份']))
TRANS_8['Seçilen Ayın Verilerini Sıfırla'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إعادة تعيين بيانات الشهر المحدد', 'Resetovat data vybraného měsíce', 'Kiválasztott hónap adatainak visszaállítása', '選択した月のデータをリセット', '선택한 달 데이터 초기화', 'Resetuj dane wybranego miesiąca', 'Сбросить данные выбранного месяца', '重置所选月份数据']))
TRANS_8['Tüm Verileri Sıfırla'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إعادة تعيين جميع البيانات', 'Resetovat všechna data', 'Összes adat visszaállítása', 'すべてのデータをリセット', '모든 데이터 초기화', 'Resetuj wszystkie dane', 'Сбросить все данные', '重置所有数据']))
TRANS_8['Paneli Aç'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['فتح اللوحة', 'Otevřít panel', 'Panel megnyitása', 'パネルを開く', '패널 열기', 'Otwórz panel', 'Открыть панель', '打开面板']))
TRANS_8['Tamamen Kapat'] = dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إغلاق بالكامل', 'Úplně zavřít', 'Teljes bezárás', '完全に閉じる', '완전히 닫기', 'Zamknij całkowicie', 'Полностью закрыть', '完全关闭']))

# Eksik sekme/ekran metinleri: 8 yeni dil için tam çeviri tablosu.
TRANS_8.update({
    "V3.0\nYerel veri • Gizlilik öncelikli": {"ar":"V3.0\nبيانات محلية • الخصوصية أولاً","cs":"V3.0\nLokální data • Soukromí na prvním místě","hu":"V3.0\nHelyi adatok • Adatvédelem az első helyen","ja":"V3.0\nローカルデータ • プライバシー優先","ko":"V3.0\n로컬 데이터 • 개인정보 보호 우선","pl":"V3.0\nDane lokalne • Prywatność przede wszystkim","ru":"V3.0\nЛокальные данные • Конфиденциальность прежде всего","zh-CN":"V3.0\n本地数据 • 隐私优先"},
    "⚠  2 saattir mola vermeden kullanıyorsun. Kısa bir mola ver.": {"ar":"⚠ لقد كنت تستخدم الكمبيوتر لمدة ساعتين دون استراحة. خذ استراحة قصيرة.","cs":"⚠ Používáš počítač 2 hodiny bez přestávky. Dej si krátkou pauzu.","hu":"⚠ 2 órája használod a számítógépet szünet nélkül. Tarts egy rövid szünetet.","ja":"⚠ 2時間休憩なしでパソコンを使用しています。少し休憩しましょう。","ko":"⚠ 2시간 동안 쉬지 않고 컴퓨터를 사용했습니다. 잠시 쉬어 주세요.","pl":"⚠ Korzystasz z komputera od 2 godzin bez przerwy. Zrób krótką przerwę.","ru":"⚠ Вы работаете за компьютером 2 часа без перерыва. Сделайте небольшой перерыв.","zh-CN":"⚠ 你已经连续使用电脑 2 小时了。休息一下吧。"},
    "Harika gidiyorsun!": {"ar":"أنت تقوم بعمل رائع!","cs":"Jde ti to skvěle!","hu":"Nagyszerűen haladsz!","ja":"順調です！","ko":"아주 잘하고 있어요!","pl":"Świetnie Ci idzie!","ru":"Отлично идёт!","zh-CN":"做得很好！"},
    "Devam et, iyi gidiyorsun.": {"ar":"استمر، أنت تسير بشكل جيد.","cs":"Pokračuj, jde ti to dobře.","hu":"Folytasd, jól haladsz.","ja":"その調子で続けましょう。","ko":"계속하세요. 잘하고 있어요.","pl":"Tak trzymaj, idzie Ci dobrze.","ru":"Продолжай, всё идёт хорошо.","zh-CN":"继续保持，你做得很好。"},
    "Henüz yeterli veri yok.": {"ar":"لا توجد بيانات كافية بعد.","cs":"Zatím není dostatek dat.","hu":"Még nincs elegendő adat.","ja":"まだ十分なデータがありません。","ko":"아직 충분한 데이터가 없습니다.","pl":"Brak jeszcze wystarczających danych.","ru":"Пока недостаточно данных.","zh-CN":"目前还没有足够的数据。"},
    "Örn. Eğitim, Sosyal Medya, Tasarım...": {"ar":"مثال: التعليم، وسائل التواصل الاجتماعي، التصميم...","cs":"Např. Vzdělávání, Sociální sítě, Design...","hu":"Pl. Oktatás, Közösségi média, Tervezés...","ja":"例：教育、SNS、デザイン...","ko":"예: 교육, 소셜 미디어, 디자인...","pl":"Np. Edukacja, Media społecznościowe, Design...","ru":"Напр. Обучение, Социальные сети, Дизайн...","zh-CN":"例如：学习、社交媒体、设计……"},
    "Kategori, uygulama sınıflandırmasında kullanılabilir.": {"ar":"يمكن استخدام الفئة لتصنيف التطبيقات.","cs":"Kategorie lze použít ke klasifikaci aplikací.","hu":"A kategória az alkalmazások besorolására használható.","ja":"カテゴリはアプリの分類に使用できます。","ko":"카테고리를 앱 분류에 사용할 수 있습니다.","pl":"Kategoria może służyć do klasyfikowania aplikacji.","ru":"Категория используется для классификации приложений.","zh-CN":"该分类可用于应用程序分类。"},
    "Uygulamalar arasında geçen aktif kullanım sürelerini ve kesintisiz oturumları zaman çizelgesinde inceleyebilirsin.": {"ar":"يمكنك عرض أوقات الاستخدام النشط والجلسات المتواصلة بين التطبيقات في المخطط الزمني.","cs":"Na časové ose můžeš zobrazit aktivní používání mezi aplikacemi a nepřerušované relace.","hu":"Az idővonalon megtekintheted az alkalmazások közötti aktív használatot és a megszakítás nélküli munkameneteket.","ja":"タイムラインでアプリ間のアクティブ使用時間と連続セッションを確認できます。","ko":"타임라인에서 앱 간 활성 사용 시간과 연속 세션을 확인할 수 있습니다.","pl":"Na osi czasu możesz sprawdzić aktywne korzystanie z aplikacji i nieprzerwane sesje.","ru":"На временной шкале можно посмотреть активное использование между приложениями и непрерывные сеансы.","zh-CN":"你可以在时间轴中查看应用之间的活跃使用时间和连续会话。"},
    "kayıt": {"ar":"سجل","cs":"záznam","hu":"bejegyzés","ja":"件","ko":"개","pl":"wpis","ru":"записей","zh-CN":"条记录"},
    "Bugün henüz tamamlanmış bir oturum bulunmuyor.": {"ar":"لا توجد جلسات مكتملة اليوم بعد.","cs":"Dnes zatím nejsou dokončené žádné relace.","hu":"Ma még nincs befejezett munkamenet.","ja":"今日はまだ完了したセッションがありません。","ko":"오늘 완료된 세션이 아직 없습니다.","pl":"Dzisiaj nie ma jeszcze zakończonych sesji.","ru":"Сегодня ещё нет завершённых сеансов.","zh-CN":"今天还没有已完成的会话。"},
    "Kesintisiz Bilgisayar Kullanımı": {"ar":"استخدام الكمبيوتر المتواصل","cs":"Nepřerušované používání počítače","hu":"Megszakítás nélküli számítógép-használat","ja":"連続パソコン使用","ko":"연속 컴퓨터 사용","pl":"Nieprzerwane korzystanie z komputera","ru":"Непрерывное использование компьютера","zh-CN":"连续电脑使用"},
    "En yeni kayıtlar üstte gösterilir.": {"ar":"تظهر أحدث السجلات في الأعلى.","cs":"Nejnovější záznamy jsou nahoře.","hu":"A legújabb bejegyzések felül jelennek meg.","ja":"最新の記録が上に表示されます。","ko":"최신 기록이 위에 표시됩니다.","pl":"Najnowsze wpisy są wyświetlane u góry.","ru":"Самые новые записи отображаются сверху.","zh-CN":"最新记录显示在顶部。"},
    "‹ Önceki Ay": {"ar":"‹ الشهر السابق","cs":"‹ Předchozí měsíc","hu":"‹ Előző hónap","ja":"‹ 前月","ko":"‹ 이전 달","pl":"‹ Poprzedni miesiąc","ru":"‹ Предыдущий месяц","zh-CN":"‹ 上个月"},
    "Sonraki Ay ›": {"ar":"الشهر التالي ›","cs":"Další měsíc ›","hu":"Következő hónap ›","ja":"次月 ›","ko":"다음 달 ›","pl":"Następny miesiąc ›","ru":"Следующий месяц ›","zh-CN":"下个月 ›"},
    "aktif gün ortalaması": {"ar":"متوسط الأيام النشطة","cs":"průměr aktivních dnů","hu":"aktív napok átlaga","ja":"アクティブ日の平均","ko":"활성 일 평균","pl":"średnia aktywnych dni","ru":"среднее за активные дни","zh-CN":"活跃日平均值"},
    "Bu ayın günlük aktif kullanım süresi": {"ar":"وقت الاستخدام النشط اليومي لهذا الشهر","cs":"Denní aktivní používání za tento měsíc","hu":"Az e havi napi aktív használati idő","ja":"今月の1日あたりのアクティブ使用時間","ko":"이번 달 일일 활성 사용 시간","pl":"Dzienny aktywny czas użytkowania w tym miesiącu","ru":"Ежедневное активное использование за этот месяц","zh-CN":"本月每日活跃使用时长"},
    "Toplam kullanımın kategori bazında dağılımı": {"ar":"توزيع إجمالي الاستخدام حسب الفئة","cs":"Rozložení celkového používání podle kategorií","hu":"A teljes használat kategóriánkénti megoszlása","ja":"総使用時間のカテゴリ別分布","ko":"총 사용량의 카테고리별 분포","pl":"Rozkład całkowitego użycia według kategorii","ru":"Распределение общего использования по категориям","zh-CN":"总使用量按类别分布"},
    "Günlük kullanımın kategorilere göre dağılımı": {"ar":"توزيع الاستخدام اليومي حسب الفئات","cs":"Denní používání podle kategorií","hu":"Napi használat kategóriánként","ja":"1日の使用時間のカテゴリ別分布","ko":"일일 사용량의 카테고리별 분포","pl":"Dzienny rozkład użycia według kategorii","ru":"Ежедневное использование по категориям","zh-CN":"每日使用量按类别分布"},
    "Bu ay en fazla kullanılan uygulamalar": {"ar":"الأكثر استخدامًا هذا الشهر","cs":"Nejpoužívanější aplikace tento měsíc","hu":"Ebben a hónapban legtöbbet használt alkalmazások","ja":"今月最も使用されたアプリ","ko":"이번 달 가장 많이 사용한 앱","pl":"Najczęściej używane aplikacje w tym miesiącu","ru":"Самые используемые приложения в этом месяце","zh-CN":"本月使用最多的应用"},
    "Son 7 günün kullanım eğilimi": {"ar":"اتجاه الاستخدام خلال آخر 7 أيام","cs":"Trend používání za posledních 7 dní","hu":"Az elmúlt 7 nap használati trendje","ja":"過去7日間の使用傾向","ko":"최근 7일 사용 추세","pl":"Trend użycia z ostatnich 7 dni","ru":"Тенденция использования за последние 7 дней","zh-CN":"最近7天使用趋势"},
    "Bu hafta ve önceki haftanın özeti": {"ar":"ملخص هذا الأسبوع والأسبوع السابق","cs":"Shrnutí tohoto a předchozího týdne","hu":"E hét és az előző hét összefoglalója","ja":"今週と先週の概要","ko":"이번 주와 지난주 요약","pl":"Podsumowanie tego i poprzedniego tygodnia","ru":"Сводка этой и предыдущей недели","zh-CN":"本周与上周摘要"},
    "Bir güne tıklayarak o günün ayrıntılarını açabilirsin.": {"ar":"انقر على يوم لفتح تفاصيله.","cs":"Kliknutím na den otevřeš jeho podrobnosti.","hu":"Kattints egy napra a részletek megnyitásához.","ja":"日付をクリックすると、その日の詳細を開けます。","ko":"날짜를 클릭하면 해당 날짜의 세부 정보를 볼 수 있습니다.","pl":"Kliknij dzień, aby otworzyć jego szczegóły.","ru":"Нажмите на день, чтобы открыть его подробности.","zh-CN":"点击某一天即可打开当天的详细信息。"},
    "Grafik yüklenemedi": {"ar":"تعذر تحميل الرسم البياني","cs":"Graf se nepodařilo načíst","hu":"A diagram nem tölthető be","ja":"グラフを読み込めませんでした","ko":"그래프를 불러오지 못했습니다","pl":"Nie udało się załadować wykresu","ru":"Не удалось загрузить график","zh-CN":"无法加载图表"},
    "Henüz veri yok": {"ar":"لا توجد بيانات بعد","cs":"Zatím žádná data","hu":"Még nincs adat","ja":"まだデータがありません","ko":"아직 데이터가 없습니다","pl":"Brak danych","ru":"Данных пока нет","zh-CN":"暂无数据"},
    "Toplam": {"ar":"الإجمالي","cs":"Celkem","hu":"Összesen","ja":"合計","ko":"총합","pl":"Łącznie","ru":"Всего","zh-CN":"总计"},
    "Henüz uygulama verisi yok.": {"ar":"لا توجد بيانات تطبيقات بعد.","cs":"Zatím nejsou žádná data aplikací.","hu":"Még nincs alkalmazásadat.","ja":"まだアプリデータがありません。","ko":"아직 앱 데이터가 없습니다.","pl":"Brak jeszcze danych aplikacji.","ru":"Данных приложений пока нет.","zh-CN":"暂无应用数据。"},
    "Son 7 gün ile önceki 7 gün karşılaştırması.": {"ar":"مقارنة آخر 7 أيام بالـ7 أيام السابقة.","cs":"Porovnání posledních 7 dní s předchozími 7 dny.","hu":"Az elmúlt 7 nap összehasonlítása az előző 7 nappal.","ja":"過去7日間とその前の7日間の比較。","ko":"최근 7일과 이전 7일 비교.","pl":"Porównanie ostatnich 7 dni z poprzednimi 7 dniami.","ru":"Сравнение последних 7 дней с предыдущими 7 днями.","zh-CN":"最近7天与前7天的比较。"},
    "Bu tarihte uygulama kullanımı bulunmuyor.": {"ar":"لا يوجد استخدام للتطبيقات في هذا التاريخ.","cs":"V tento den nebylo zaznamenáno používání aplikací.","hu":"Ezen a napon nem volt alkalmazáshasználat.","ja":"この日のアプリ使用記録はありません。","ko":"이 날짜에는 앱 사용 기록이 없습니다.","pl":"Brak użycia aplikacji w tym dniu.","ru":"В этот день использования приложений не было.","zh-CN":"这一天没有应用使用记录。"},
    "SEÇİLEN TARİH": {"ar":"التاريخ المحدد","cs":"VYBRANÉ DATUM","hu":"KIVÁLASZTOTT DÁTUM","ja":"選択した日付","ko":"선택한 날짜","pl":"WYBRANA DATA","ru":"ВЫБРАННАЯ ДАТА","zh-CN":"所选日期"},
    "UYGULAMA KULLANIMI • DAİRE GRAFİĞİ": {"ar":"استخدام التطبيقات • مخطط دائري","cs":"POUŽÍVÁNÍ APLIKACÍ • KOLÁČOVÝ GRAF","hu":"ALKALMAZÁSHASZNÁLAT • KÖRDIAGRAM","ja":"アプリ使用 • 円グラフ","ko":"앱 사용량 • 원형 차트","pl":"UŻYCIE APLIKACJI • WYKRES KOŁOWY","ru":"ИСПОЛЬЗОВАНИЕ ПРИЛОЖЕНИЙ • КРУГОВАЯ ДИАГРАММА","zh-CN":"应用使用 • 饼图"},
    "UYGULAMA KULLANIMI": {"ar":"استخدام التطبيقات","cs":"POUŽÍVÁNÍ APLIKACÍ","hu":"ALKALMAZÁSHASZNÁLAT","ja":"アプリ使用","ko":"앱 사용량","pl":"UŻYCIE APLIKACJI","ru":"ИСПОЛЬЗОВАНИЕ ПРИЛОЖЕНИЙ","zh-CN":"应用使用"},
    "Bu tarihte kayıtlı uygulama verisi yok.": {"ar":"لا توجد بيانات تطبيقات مسجلة في هذا التاريخ.","cs":"V tento den nejsou zaznamenána žádná data aplikací.","hu":"Ezen a napon nincs rögzített alkalmazásadat.","ja":"この日に記録されたアプリデータはありません。","ko":"이 날짜에는 기록된 앱 데이터가 없습니다.","pl":"Brak zarejestrowanych danych aplikacji dla tego dnia.","ru":"На эту дату нет записанных данных приложений.","zh-CN":"这一天没有记录的应用数据。"},
    "OTURUMLAR": {"ar":"الجلسات","cs":"RELACE","hu":"MUNKAMENETEK","ja":"セッション","ko":"세션","pl":"SESJE","ru":"СЕАНСЫ","zh-CN":"会话"},
    "Bu tarihte oturum kaydı yok.": {"ar":"لا توجد سجلات جلسات في هذا التاريخ.","cs":"V tento den nejsou žádné záznamy relací.","hu":"Ezen a napon nincs munkamenet-bejegyzés.","ja":"この日のセッション記録はありません。","ko":"이 날짜에는 세션 기록이 없습니다.","pl":"Brak zapisów sesji dla tego dnia.","ru":"На эту дату нет записей сеансов.","zh-CN":"这一天没有会话记录。"},
    "0 dk": {"ar":"0 د","cs":"0 min","hu":"0 p","ja":"0分","ko":"0분","pl":"0 min","ru":"0 мин","zh-CN":"0分钟"},
    "Uygulamanın aydınlık, karanlık veya Windows sistem temasını kullanmasını seç.": {"ar":"اختر استخدام المظهر الفاتح أو الداكن أو مظهر نظام Windows.","cs":"Zvol, zda aplikace použije světlý, tmavý nebo systémový motiv Windows.","hu":"Válaszd ki a világos, sötét vagy a Windows rendszertémát.","ja":"ライト、ダーク、またはWindowsのシステムテーマを選択します。","ko":"앱에서 밝은 테마, 어두운 테마 또는 Windows 시스템 테마를 사용할지 선택하세요.","pl":"Wybierz jasny, ciemny lub systemowy motyw Windows.","ru":"Выберите светлую, тёмную или системную тему Windows.","zh-CN":"选择应用使用浅色、深色或 Windows 系统主题。"},
    "Bilgisayar açıldığında Dijital Denge otomatik olarak çalışsın.": {"ar":"تشغيل Dijital Denge تلقائيًا عند بدء تشغيل الكمبيوتر.","cs":"Spouštět Dijital Denge automaticky při spuštění počítače.","hu":"A Dijital Denge automatikusan induljon a számítógép bekapcsolásakor.","ja":"パソコン起動時にDijital Dengeを自動的に起動します。","ko":"컴퓨터가 켜질 때 Dijital Denge를 자동으로 실행합니다.","pl":"Uruchamiaj Dijital Denge automatycznie po włączeniu komputera.","ru":"Автоматически запускать Dijital Denge при включении компьютера.","zh-CN":"电脑启动时自动运行 Dijital Denge。"},
    "Otomatik kategorilere ek olarak kendi kategorilerini oluşturabilir ve Uygulamalar sayfasında uygulamalara atayabilirsin.": {"ar":"بالإضافة إلى الفئات التلقائية، يمكنك إنشاء فئاتك الخاصة وتعيينها للتطبيقات في صفحة التطبيقات.","cs":"Kromě automatických kategorií můžeš vytvořit vlastní kategorie a přiřadit je aplikacím na stránce Aplikace.","hu":"Az automatikus kategóriák mellett saját kategóriákat is létrehozhatsz és hozzárendelheted őket az alkalmazásokhoz az Alkalmazások oldalon.","ja":"自動カテゴリに加えて独自のカテゴリを作成し、「アプリケーション」ページでアプリに割り当てられます。","ko":"자동 카테고리 외에도 사용자 지정 카테고리를 만들고 앱 페이지에서 앱에 지정할 수 있습니다.","pl":"Oprócz kategorii automatycznych możesz tworzyć własne kategorie i przypisywać je aplikacjom na stronie Aplikacje.","ru":"Помимо автоматических категорий, можно создавать собственные и назначать их приложениям на странице приложений.","zh-CN":"除了自动分类外，你还可以创建自定义分类，并在应用页面中将其分配给应用。"},
    "＋ Yeni Kategori": {"ar":"＋ فئة جديدة","cs":"＋ Nová kategorie","hu":"＋ Új kategória","ja":"＋ 新しいカテゴリ","ko":"＋ 새 카테고리","pl":"＋ Nowa kategoria","ru":"＋ Новая категория","zh-CN":"＋ 新建分类"},
    "Aşağıdaki aydaki tüm kullanım kayıtlarını sil": {"ar":"حذف جميع سجلات الاستخدام للشهر المحدد أدناه","cs":"Smazat všechny záznamy používání z níže uvedeného měsíce","hu":"Az alábbi hónap összes használati adatának törlése","ja":"以下の月のすべての使用記録を削除","ko":"아래 선택한 달의 모든 사용 기록 삭제","pl":"Usuń wszystkie dane użycia z poniższego miesiąca","ru":"Удалить все записи использования за указанный ниже месяц","zh-CN":"删除以下月份的所有使用记录"},
    "Sistemde tutulan tüm kullanım verileri silinecek. Ayarlar korunur ve bu işlem geri alınamaz.": {"ar":"سيتم حذف جميع بيانات الاستخدام المخزنة في النظام. ستبقى الإعدادات محفوظة ولا يمكن التراجع عن هذا الإجراء.","cs":"Všechna uložená data používání budou smazána. Nastavení zůstanou zachována a tuto akci nelze vrátit.","hu":"A rendszerben tárolt összes használati adat törlődik. A beállítások megmaradnak, ez a művelet nem vonható vissza.","ja":"システムに保存されているすべての使用データを削除します。設定は保持され、この操作は元に戻せません。","ko":"시스템에 저장된 모든 사용 데이터가 삭제됩니다. 설정은 유지되며 이 작업은 되돌릴 수 없습니다.","pl":"Wszystkie zapisane dane użycia zostaną usunięte. Ustawienia zostaną zachowane, a tej operacji nie można cofnąć.","ru":"Все сохранённые данные использования будут удалены. Настройки сохранятся, а отменить это действие невозможно.","zh-CN":"系统中保存的所有使用数据都将被删除。设置会保留，此操作无法撤销。"},
    "Seçilen ayın tüm kullanım verileri silinecek. Bu işlem geri alınamaz.": {"ar":"سيتم حذف جميع بيانات الاستخدام للشهر المحدد. لا يمكن التراجع عن هذا الإجراء.","cs":"Všechna data používání za vybraný měsíc budou smazána. Tuto akci nelze vrátit.","hu":"A kiválasztott hónap összes használati adata törlődik. Ez a művelet nem vonható vissza.","ja":"選択した月のすべての使用データを削除します。この操作は元に戻せません。","ko":"선택한 달의 모든 사용 데이터가 삭제됩니다. 이 작업은 되돌릴 수 없습니다.","pl":"Wszystkie dane użycia z wybranego miesiąca zostaną usunięte. Tej operacji nie można cofnąć.","ru":"Все данные использования за выбранный месяц будут удалены. Это действие нельзя отменить.","zh-CN":"所选月份的所有使用数据都将被删除。此操作无法撤销。"},
    "Bugünkü kullanım": {"ar":"استخدام اليوم","cs":"Dnešní používání","hu":"Mai használat","ja":"今日の使用状況","ko":"오늘 사용량","pl":"Dzisiejsze użycie","ru":"Использование сегодня","zh-CN":"今日使用"}
})

# Eksik sekme/ayar metinleri: 8 yeni dil için tamamlandı.
TRANS_8.update({
    'MOLA': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['BREAKS', 'PAUZY', 'SZÜNETEK', '休憩', '휴식', 'PRZERWY', 'ПЕРЕРЫВЫ', '休息'])),
    'GİZLİLİK': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['PRIVACY', 'SOUKROMÍ', 'ADATVÉDELEM', 'プライバシー', '개인정보 보호', 'PRYWATNOŚĆ', 'КОНФИДЕНЦИАЛЬНОСТЬ', '隐私'])),
    'HEDEFLER': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['GOALS', 'CÍLE', 'CÉLOK', '目標', '목표', 'CELE', 'ЦЕЛИ', '目标'])),
    'Karanlık': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Dark', 'Tmavý', 'Sötét', 'ダーク', '어둡게', 'Ciemny', 'Тёмная', '深色'])),
    'Aydınlık': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Light', 'Světlý', 'Világos', 'ライト', '밝게', 'Jasny', 'Светлая', '浅色'])),
    'Sistem': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['System', 'Systém', 'Rendszer', 'システム', '시스템', 'System', 'Система', '系统'])),
    'Uygulama dili': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Application language', 'Jazyk aplikace', 'Alkalmazás nyelve', 'アプリの言語', '앱 언어', 'Język aplikacji', 'Язык приложения', '应用语言'])),
    'Boşta kalma süresi (dakika)': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Idle threshold (minutes)', 'Limit nečinnosti (minuty)', 'Tétlenségi idő (perc)', 'アイドル時間（分）', '유휴 시간(분)', 'Próg bezczynności (minuty)', 'Порог бездействия (мин)', '空闲阈值（分钟）'])),
    'Mola hatırlatıcısı (dakika)': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Break reminder (minutes)', 'Připomínka přestávky (minuty)', 'Szünet emlékeztetője (perc)', '休憩リマインダー（分）', '휴식 알림(분)', 'Przypomnienie o przerwie (min)', 'Напоминание о перерыве (мин)', '休息提醒（分钟）'])),
    'Mola hatırlatıcısını etkinleştir': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Enable break reminder', 'Povolit připomínku přestávky', 'Szünetemlékeztető engedélyezése', '休憩リマインダーを有効にする', '휴식 알림 켜기', 'Włącz przypomnienie o przerwie', 'Включить напоминание о перерыве', '启用休息提醒'])),
    'Sesli uyarı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Sound alert', 'Zvukové upozornění', 'Hangjelzés', 'サウンド通知', '소리 알림', 'Alert dźwiękowy', 'Звуковое оповещение', '声音提醒'])),
    'Web takibi (yalnızca pencere başlığı; URL kaydı yok)': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Web tracking (window title only; no URL recording)', 'Sledování webu (pouze název okna; URL se neukládají)', 'Webkövetés (csak ablakcím; nincs URL-rögzítés)', 'Web追跡（ウィンドウタイトルのみ、URLは記録しません）', '웹 추적(창 제목만 기록, URL 저장 안 함)', 'Śledzenie stron (tylko tytuł okna; brak zapisu URL)', 'Отслеживание веба (только заголовок окна; URL не записываются)', '网页跟踪（仅窗口标题；不记录 URL）'])),
    'Günlük aktif kullanım hedefi (saat)': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Daily active usage goal (hours)', 'Denní cíl aktivního používání (hodiny)', 'Napi aktív használati cél (óra)', '1日のアクティブ使用目標（時間）', '일일 활성 사용 목표(시간)', 'Dzienny cel aktywnego użycia (godziny)', 'Дневная цель активного использования (часы)', '每日活跃使用目标（小时）'])),
    'Windows ile başlat': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Start with Windows', 'Spouštět se systémem Windows', 'Indítás a Windowszal', 'Windowsで起動', 'Windows와 함께 시작', 'Uruchamiaj wraz z Windows', 'Запускать вместе с Windows', '随 Windows 启动'])),
    'KATEGORİLER': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['CATEGORIES', 'KATEGORIE', 'KATEGÓRIÁK', 'カテゴリ', '카테고리', 'KATEGORIE', 'КАТЕГОРИИ', '类别'])),
    'VERİ': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['DATA', 'DATA', 'ADATOK', 'データ', '데이터', 'DANE', 'ДАННЫЕ', '数据'])),
    'VERİ SIFIRLAMA': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['DATA RESET', 'RESET DAT', 'ADATOK VISSZAÁLLÍTÁSA', 'データリセット', '데이터 초기화', 'RESET DANYCH', 'СБРОС ДАННЫХ', '数据重置'])),
    'Aylık Veri Sıfırlama': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Monthly Data Reset', 'Měsíční reset dat', 'Havi adatok visszaállítása', '月間データをリセット', '월간 데이터 초기화', 'Reset danych miesięcznych', 'Сброс данных за месяц', '重置月度数据'])),
    'Aşağıdaki aydaki tüm kullanım kayıtlarını sil': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Delete all usage records for the selected month', 'Smazat všechny záznamy používání vybraného měsíce', 'A kiválasztott hónap összes használati adatának törlése', '選択した月のすべての使用記録を削除', '선택한 달의 모든 사용 기록 삭제', 'Usuń wszystkie dane użycia z wybranego miesiąca', 'Удалить все записи использования за выбранный месяц', '删除所选月份的所有使用记录'])),
    'Ay seçin': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Select month', 'Vyberte měsíc', 'Válasszon hónapot', '月を選択', '월 선택', 'Wybierz miesiąc', 'Выберите месяц', '选择月份'])),
    'Seçilen Ayın Verilerini Sıfırla': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Reset Selected Month', 'Resetovat vybraný měsíc', 'Kiválasztott hónap adatainak visszaállítása', '選択した月のデータをリセット', '선택한 달 데이터 초기화', 'Resetuj wybrany miesiąc', 'Сбросить данные выбранного месяца', '重置所选月份数据'])),
    'Tüm Verileri Sıfırla': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Reset All Data', 'Resetovat všechna data', 'Összes adat visszaállítása', 'すべてのデータをリセット', '모든 데이터 초기화', 'Resetuj wszystkie dane', 'Сбросить все данные', '重置所有数据'])),
    'Seçilen ayın tüm kullanım verileri silinecek. Bu işlem geri alınamaz.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['All usage data for the selected month will be deleted. This cannot be undone.', 'Všechna data používání za vybraný měsíc budou smazána. Tuto akci nelze vrátit.', 'A kiválasztott hónap összes használati adata törlődik. Ez a művelet nem vonható vissza.', '選択した月のすべての使用データを削除します。この操作は元に戻せません。', '선택한 달의 모든 사용 데이터가 삭제됩니다. 이 작업은 되돌릴 수 없습니다.', 'Wszystkie dane użycia z wybranego miesiąca zostaną usunięte. Tej operacji nie można cofnąć.', 'Все данные использования за выбранный месяц будут удалены. Это действие нельзя отменить.', '所选月份的所有使用数据都将被删除。此操作无法撤销。'])),
    'Sistemde tutulan tüm kullanım verileri silinecek. Ayarlar korunur ve bu işlem geri alınamaz.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['All stored usage data will be deleted. Settings will be preserved and this cannot be undone.', 'Všechna uložená data používání budou smazána. Nastavení zůstanou zachována a tuto akci nelze vrátit.', 'A rendszerben tárolt összes használati adat törlődik. A beállítások megmaradnak, ez a művelet nem vonható vissza.', 'システムに保存されているすべての使用データを削除します。設定は保持され、この操作は元に戻せません。', '시스템에 저장된 모든 사용 데이터가 삭제됩니다. 설정은 유지되며 이 작업은 되돌릴 수 없습니다.', 'Wszystkie zapisane dane użycia zostaną usunięte. Ustawienia zostaną zachowane, a tej operacji nie można cofnąć.', 'Все сохранённые данные использования будут удалены. Настройки сохранятся, а отменить это действие невозможно.', '系统中保存的所有使用数据都将被删除。设置会保留，此操作无法撤销。'])),
    'Bugünkü kullanım': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ["Today's usage", 'Dnešní používání', 'Mai használat', '今日の使用状況', '오늘 사용량', 'Dzisiejsze użycie', 'Использование сегодня', '今日使用'])),
    'Devam et, iyi gidiyorsun.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ["Keep going, you're doing great.", 'Pokračuj, daří se ti skvěle.', 'Csak így tovább, remekül haladsz.', 'その調子です。', '계속 잘하고 있어요.', 'Tak trzymaj, świetnie Ci idzie.', 'Продолжай, у тебя отлично получается.', '继续保持，你做得很好。'])),
    'Harika gidiyorsun!': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ["You're doing great!", 'Daří se ti skvěle!', 'Nagyszerűen haladsz!', 'とても順調です！', '아주 잘하고 있어요!', 'Świetnie Ci idzie!', 'Отлично получается!', '做得非常好！'])),
    'Henüz yeterli veri yok.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Not enough data yet.', 'Zatím není dostatek dat.', 'Még nincs elég adat.', 'まだ十分なデータがありません。', '아직 충분한 데이터가 없습니다.', 'Brak jeszcze wystarczającej ilości danych.', 'Пока недостаточно данных.', '暂时没有足够的数据。'])),
    'Henüz veri yok': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['No data yet', 'Zatím žádná data', 'Még nincs adat', 'まだデータがありません', '아직 데이터 없음', 'Brak danych', 'Данных пока нет', '暂无数据'])),
    'Henüz uygulama verisi yok.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['No application data yet.', 'Zatím nejsou žádná data aplikací.', 'Még nincs alkalmazásadat.', 'まだアプリデータがありません。', '아직 앱 데이터가 없습니다.', 'Brak jeszcze danych aplikacji.', 'Данных приложений пока нет.', '暂无应用数据。'])),
    'Bugün henüz tamamlanmış bir oturum bulunmuyor.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['There are no completed sessions today yet.', 'Dnes zatím nejsou žádné dokončené relace.', 'Ma még nincs befejezett munkamenet.', '今日はまだ完了したセッションがありません。', '오늘은 아직 완료된 세션이 없습니다.', 'Brak jeszcze ukończonych sesji na dziś.', 'Сегодня завершённых сеансов пока нет.', '今天还没有已完成的会话。'])),
    'Bu ay en fazla kullanılan uygulamalar': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Most used applications this month', 'Nejpoužívanější aplikace tohoto měsíce', 'A hónap legtöbbet használt alkalmazásai', '今月最も使用したアプリ', '이번 달 가장 많이 사용한 앱', 'Najczęściej używane aplikacje w tym miesiącu', 'Самые используемые приложения в этом месяце', '本月使用最多的应用'])),
    'Bu ayın günlük aktif kullanım süresi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Daily active usage this month', 'Denní aktivní používání tohoto měsíce', 'A hónap napi aktív használata', '今月の1日あたりのアクティブ使用時間', '이번 달 일일 활성 사용 시간', 'Dzienny aktywny czas użycia w tym miesiącu', 'Ежедневное активное использование в этом месяце', '本月每日活跃使用时间'])),
    'Günlük kullanımın kategorilere göre dağılımı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Daily usage by category', 'Denní používání podle kategorií', 'Napi használat kategóriánként', 'カテゴリ別の1日の使用状況', '카테고리별 일일 사용량', 'Dzienne użycie według kategorii', 'Ежедневное использование по категориям', '按类别划分的每日使用量'])),
    'Toplam kullanımın kategori bazında dağılımı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Total usage by category', 'Celkové používání podle kategorií', 'Teljes használat kategóriánként', 'カテゴリ別の総使用量', '카테고리별 총 사용량', 'Łączne użycie według kategorii', 'Общее использование по категориям', '按类别划分的总使用量'])),
    'Sonraki Ay ›': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Next Month ›', 'Další měsíc ›', 'Következő hónap ›', '次の月 ›', '다음 달 ›', 'Następny miesiąc ›', 'Следующий месяц ›', '下个月 ›'])),
    '‹ Önceki Ay': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['‹ Previous Month', '‹ Předchozí měsíc', '‹ Előző hónap', '‹ 前の月', '‹ 이전 달', '‹ Poprzedni miesiąc', '‹ Предыдущий месяц', '‹ 上个月'])),
    'OTURUMLAR': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['SESSIONS', 'RELACE', 'MUNKAMENETEK', 'セッション', '세션', 'SESJE', 'СЕАНСЫ', '会话'])),
    'SEÇİLEN TARİH': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['SELECTED DATE', 'VYBRANÉ DATUM', 'KIVÁLASZTOTT DÁTUM', '選択した日付', '선택한 날짜', 'WYBRANA DATA', 'ВЫБРАННАЯ ДАТА', '所选日期'])),
    'Bu tarihte kayıtlı uygulama verisi yok.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['No application data recorded for this date.', 'V tento den nejsou zaznamenána žádná data aplikací.', 'Ezen a napon nincs rögzített alkalmazásadat.', 'この日に記録されたアプリデータはありません。', '이 날짜에는 기록된 앱 데이터가 없습니다.', 'Brak zarejestrowanych danych aplikacji dla tego dnia.', 'На эту дату нет записанных данных приложений.', '这一天没有记录的应用数据。'])),
    'Bu tarihte oturum kaydı yok.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['No session record for this date.', 'V tento den nejsou žádné záznamy relací.', 'Ezen a napon nincs munkamenet-bejegyzés.', 'この日のセッション記録はありません。', '이 날짜에는 세션 기록이 없습니다.', 'Brak zapisów sesji dla tego dnia.', 'На эту дату нет записей сеансов.', '这一天没有会话记录。'])),
    'Bu tarihte uygulama kullanımı bulunmuyor.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['No application usage for this date.', 'V tento den nebylo zaznamenáno používání aplikací.', 'Ezen a napon nem volt alkalmazáshasználat.', 'この日のアプリ使用記録はありません。', '이 날짜에는 앱 사용 기록이 없습니다.', 'Brak użycia aplikacji w tym dniu.', 'В этот день использования приложений не было.', '这一天没有应用使用记录。'])),
    'Bir güne tıklayarak o günün ayrıntılarını açabilirsin.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Click a day to open its details.', 'Kliknutím na den otevřeš jeho podrobnosti.', 'Kattints egy napra a részletek megnyitásához.', '日付をクリックすると詳細を開けます。', '날짜를 클릭하면 세부 정보를 볼 수 있습니다.', 'Kliknij dzień, aby otworzyć jego szczegóły.', 'Нажмите на день, чтобы открыть подробности.', '点击某一天即可打开当天的详细信息。'])),
    'Son 7 gün ile önceki 7 gün karşılaştırması.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Comparison of the last 7 days with the previous 7 days.', 'Porovnání posledních 7 dní s předchozími 7 dny.', 'Az elmúlt 7 nap összehasonlítása az előző 7 nappal.', '過去7日間とその前の7日間の比較。', '최근 7일과 이전 7일 비교.', 'Porównanie ostatnich 7 dni z poprzednimi 7 dniami.', 'Сравнение последних 7 дней с предыдущими 7 днями.', '最近7天与前7天的比较。'])),
    'Son 7 günün kullanım eğilimi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Usage trend for the last 7 days', 'Trend používání za posledních 7 dní', 'Az elmúlt 7 nap használati trendje', '過去7日間の使用傾向', '최근 7일 사용 추세', 'Trend użycia z ostatnich 7 dni', 'Тенденция использования за последние 7 дней', '最近7天使用趋势'])),
    'Bu hafta ve önceki haftanın özeti': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Summary of this week and last week', 'Shrnutí tohoto a předchozího týdne', 'E hét és az előző hét összefoglalója', '今週と先週の概要', '이번 주와 지난주 요약', 'Podsumowanie tego i poprzedniego tygodnia', 'Сводка этой и предыдущей недели', '本周与上周摘要'])),
    'En yeni kayıtlar üstte gösterilir.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Newest records are shown first.', 'Nejnovější záznamy jsou zobrazeny nahoře.', 'A legújabb bejegyzések felül jelennek meg.', '新しい記録が上に表示されます。', '최신 기록이 위에 표시됩니다.', 'Najnowsze wpisy są wyświetlane na górze.', 'Новые записи отображаются сверху.', '最新记录显示在顶部。'])),
    'Grafik yüklenemedi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Chart could not be loaded', 'Graf se nepodařilo načíst', 'A diagram nem tölthető be', 'グラフを読み込めませんでした', '그래프를 불러오지 못했습니다', 'Nie udało się załadować wykresu', 'Не удалось загрузить график', '无法加载图表'])),
    'Toplam': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Total', 'Celkem', 'Összesen', '合計', '총합', 'Łącznie', 'Всего', '总计'])),
    'Toplam Kullanım': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Total Usage', 'Celkové používání', 'Teljes használat', '総使用時間', '총 사용량', 'Łączne użycie', 'Общее использование', '总使用量'])),
    'UYGULAMA KULLANIMI': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['APPLICATION USAGE', 'POUŽÍVÁNÍ APLIKACÍ', 'ALKALMAZÁSHASZNÁLAT', 'アプリ使用', '앱 사용량', 'UŻYCIE APLIKACJI', 'ИСПОЛЬЗОВАНИЕ ПРИЛОЖЕНИЙ', '应用使用'])),
    'UYGULAMA KULLANIMI • DAİRE GRAFİĞİ': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['APPLICATION USAGE • DONUT CHART', 'POUŽÍVÁNÍ APLIKACÍ • KOLÁČOVÝ GRAF', 'ALKALMAZÁSHASZNÁLAT • KÖRDIAGRAM', 'アプリ使用 • 円グラフ', '앱 사용량 • 원형 차트', 'UŻYCIE APLIKACJI • WYKRES KOŁOWY', 'ИСПОЛЬЗОВАНИЕ ПРИЛОЖЕНИЙ • КРУГОВАЯ ДИАГРАММА', '应用使用 • 饼图'])),
    'Uygulamalar arasında geçen aktif kullanım sürelerini ve kesintisiz oturumları zaman çizelgesinde inceleyebilirsin.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['You can review active usage between applications and continuous sessions on the timeline.', 'Na časové ose můžeš prohlížet aktivní používání mezi aplikacemi a nepřetržité relace.', 'Az idővonalon megtekintheted az alkalmazások közötti aktív használatot és a folyamatos munkameneteket.', 'タイムラインでアプリ間のアクティブ使用時間と連続セッションを確認できます。', '타임라인에서 앱 간 활성 사용 시간과 연속 세션을 확인할 수 있습니다.', 'Na osi czasu możesz przeglądać aktywne użycie między aplikacjami i nieprzerwane sesje.', 'На временной шкале можно просматривать активное использование между приложениями и непрерывные сеансы.', '你可以在时间线上查看应用之间的活跃使用时间和连续会话。'])),
    'Uygulamanın aydınlık, karanlık veya Windows sistem temasını kullanmasını seç.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Choose whether the app uses light, dark, or Windows system theme.', 'Zvol, zda aplikace použije světlý, tmavý nebo systémový motiv Windows.', 'Válaszd ki a világos, sötét vagy a Windows rendszertémát.', 'アプリでライト、ダーク、またはWindowsのシステムテーマを使用するか選択します。', '앱에서 밝은 테마, 어두운 테마 또는 Windows 시스템 테마를 선택하세요.', 'Wybierz jasny, ciemny lub systemowy motyw Windows.', 'Выберите светлую, тёмную или системную тему Windows.', '选择应用使用浅色、深色或 Windows 系统主题。'])),
    'Otomatik kategorilere ek olarak kendi kategorilerini oluşturabilir ve Uygulamalar sayfasında uygulamalara atayabilirsin.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['You can create custom categories and assign them to applications on the Applications page.', 'Kromě automatických kategorií můžeš vytvořit vlastní kategorie a přiřadit je aplikacím na stránce Aplikace.', 'Az automatikus kategóriák mellett saját kategóriákat is létrehozhatsz és hozzárendelheted őket az Alkalmazások oldalon.', '自動カテゴリに加えて独自のカテゴリを作成し、「アプリケーション」ページで割り当てられます。', '자동 카테고리 외에도 사용자 지정 카테고리를 만들고 앱 페이지에서 지정할 수 있습니다.', 'Oprócz kategorii automatycznych możesz tworzyć własne i przypisywać je aplikacjom na stronie Aplikacje.', 'Помимо автоматических категорий, можно создавать собственные и назначать их приложениям на странице приложений.', '除了自动分类外，还可以创建自定义分类，并在应用页面中分配给应用。'])),
    '＋ Yeni Kategori': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['＋ New Category', '＋ Nová kategorie', '＋ Új kategória', '＋ 新しいカテゴリ', '＋ 새 카테고리', '＋ Nowa kategoria', '＋ Новая категория', '＋ 新建分类'])),
    'Örn. Eğitim, Sosyal Medya, Tasarım...': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['E.g. Education, Social Media, Design...', 'Např. Vzdělávání, Sociální sítě, Design...', 'Pl. Oktatás, közösségi média, dizájn...', '例：教育、SNS、デザイン…', '예: 교육, 소셜 미디어, 디자인...', 'Np. Edukacja, Media społecznościowe, Projektowanie...', 'Напр. образование, соцсети, дизайн...', '例如：教育、社交媒体、设计……'])),
    'Kategori, uygulama sınıflandırmasında kullanılabilir.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['The category can be used for application classification.', 'Kategorie lze použít ke klasifikaci aplikací.', 'A kategória alkalmazások osztályozására használható.', 'カテゴリはアプリの分類に使用できます。', '카테고리는 앱 분류에 사용할 수 있습니다.', 'Kategoria może służyć do klasyfikowania aplikacji.', 'Категорию можно использовать для классификации приложений.', '该分类可用于应用分类。'])),
    '0 dk': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['0 min', '0 min', '0 p', '0分', '0분', '0 min', '0 мин', '0分钟'])),
    'kayıt': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['record', 'záznam', 'bejegyzés', '記録', '기록', 'rekord', 'запись', '记录'])),
    'aktif gün ortalaması': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['average of active days', 'průměr aktivních dnů', 'aktív napok átlaga', 'アクティブ日の平均', '활성 일수 평균', 'średnia aktywnych dni', 'среднее активных дней', '活跃日平均'])),
    'kategorisi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['category', 'kategorie', 'kategória', 'カテゴリ', '카테고리', 'kategoria', 'категория', '类别'])),
    'uygulama': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['application', 'aplikace', 'alkalmazás', 'アプリ', '앱', 'aplikacja', 'приложение', '应用'])),
    'Kesintisiz Bilgisayar Kullanımı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Continuous Computer Use', 'Nepřetržité používání počítače', 'Folyamatos számítógép-használat', '連続したパソコン使用', '연속 컴퓨터 사용', 'Nieprzerwane korzystanie z komputera', 'Непрерывное использование компьютера', '连续电脑使用'])),
    'Mola': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Break', 'Pauza', 'Szünet', '休憩', '휴식', 'Przerwa', 'Перерыв', '休息'])),
    'Dinlenme': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Rest', 'Odpočinek', 'Pihenés', '休憩', '휴식', 'Odpoczynek', 'Отдых', '休息'])),
    'Mühendislik': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Engineering', 'Inženýrství', 'Mérnöki tudomány', 'エンジニアリング', '엔지니어링', 'Inżynieria', 'Инженерия', '工程'])),
    'Geliştirme': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Development', 'Vývoj', 'Fejlesztés', '開発', '개발', 'Programowanie', 'Разработка', '开发'])),
    'Çalışma': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Work', 'Práce', 'Munka', '作業', '업무', 'Praca', 'Работа', '工作'])),
    'Belge': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Document', 'Dokument', 'Dokumentum', 'ドキュメント', '문서', 'Dokument', 'Документ', '文档'])),
    'Tarayıcı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Browser', 'Prohlížeč', 'Böngésző', 'ブラウザ', '브라우저', 'Przeglądarka', 'Браузер', '浏览器'])),
    'İletişim': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Communication', 'Komunikace', 'Kommunikáció', 'コミュニケーション', '커뮤니케이션', 'Komunikacja', 'Общение', '通信'])),
    'Oyun': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Game', 'Hra', 'Játék', 'ゲーム', '게임', 'Gra', 'Игра', '游戏'])),
    'Medya': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Media', 'Média', 'Média', 'メディア', '미디어', 'Media', 'Медиа', '媒体'])),
    'Tasarım': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Design', 'Design', 'Tervezés', 'デザイン', '디자인', 'Projektowanie', 'Дизайн', '设计'])),
    'Diğer': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['Other', 'Ostatní', 'Egyéb', 'その他', '기타', 'Inne', 'Другое', '其他'])),
    '⚠  2 saattir mola vermeden kullanıyorsun. Kısa bir mola ver.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['⚠  You have been using the computer for 2 hours without a break. Take a short break.', '⚠  Počítač používáš 2 hodiny bez přestávky. Udělej si krátkou pauzu.', '⚠  2 órája használod a számítógépet szünet nélkül. Tarts egy rövid szünetet.', '⚠  2時間休憩なしでパソコンを使用しています。短い休憩を取りましょう。', '⚠  2시간 동안 쉬지 않고 컴퓨터를 사용하고 있어요. 잠시 쉬어 주세요.', '⚠  Korzystasz z komputera od 2 godzin bez przerwy. Zrób krótką przerwę.', '⚠  Вы используете компьютер 2 часа без перерыва. Сделайте небольшой перерыв.', '⚠  你已经连续使用电脑 2 小时，请休息一下。'])),
})

# COMPLETE_8_LANGUAGE_UI_FIX
TRANS_8.update({
    'Bugünkü kullanım': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['استخدام اليوم', 'Dnešní používání', 'Mai használat', '今日の使用状況', '오늘 사용량', 'Dzisiejsze użycie', 'Использование сегодня', '今日使用'])),
    'En uzun oturum': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['أطول جلسة', 'Nejdelší relace', 'Leghosszabb munkamenet', '最長セッション', '가장 긴 세션', 'Najdłuższa sesja', 'Самый длинный сеанс', '最长会话'])),
    'Verilen mola sayısı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['عدد فترات الراحة', 'Počet přestávek', 'Szünetek száma', '休憩回数', '휴식 횟수', 'Liczba przerw', 'Количество перерывов', '休息次数'])),
    'Denge Skoru': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['درجة التوازن', 'Skóre rovnováhy', 'Egyensúly pontszám', 'バランススコア', '균형 점수', 'Wynik równowagi', 'Баланс', '平衡分数'])),
    'AFK hariç': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['باستثناء عدم النشاط', 'Bez nečinnosti', 'Tétlenség nélkül', 'AFKを除く', 'AFK 제외', 'Bez AFK', 'Без AFK', '不含 AFK'])),
    'rekor': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الرقم القياسي', 'rekord', 'rekord', '記録', '기록', 'rekord', 'рекорд', '记录'])),
    'kayıtlı mola': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['فترات الراحة المسجلة', 'zaznamenané přestávky', 'rögzített szünet', '記録された休憩', '기록된 휴식', 'zarejestrowane przerwy', 'зафиксированных перерывов', '已记录休息'])),
    'düne göre': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['مقارنةً بالأمس', 'oproti včerejšku', 'tegnaphoz képest', '昨日比', '어제 대비', 'w porównaniu z wczoraj', 'по сравнению со вчера', '较昨日'])),
    'Kullanım Dağılımı (Bugün)': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['توزيع الاستخدام (اليوم)', 'Rozložení používání (dnes)', 'Használati megoszlás (ma)', '使用状況の分布（今日）', '사용량 분포(오늘)', 'Rozkład użycia (dzisiaj)', 'Распределение использования (сегодня)', '使用分布（今天）'])),
    'Son 7 Gün': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['آخر 7 أيام', 'Posledních 7 dní', 'Elmúlt 7 nap', '過去7日間', '최근 7일', 'Ostatnie 7 dni', 'Последние 7 дней', '最近7天'])),
    'Günlük hedef': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الهدف اليومي', 'Denní cíl', 'Napi cél', '1日の目標', '일일 목표', 'Cel dzienny', 'Дневная цель', '每日目标'])),
    'Bu ayın günlük aktif kullanım süresi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['مدة الاستخدام النشط اليومية هذا الشهر', 'Denní aktivní používání v tomto měsíci', 'A hónap napi aktív használati ideje', '今月の1日のアクティブ使用時間', '이번 달 일일 활성 사용 시간', 'Dzienny aktywny czas użycia w tym miesiącu', 'Ежедневное активное использование в этом месяце', '本月每日活跃使用时间'])),
    'Toplam kullanımın kategori bazında dağılımı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['توزيع إجمالي الاستخدام حسب الفئة', 'Celkové používání podle kategorií', 'Teljes használat kategóriánként', 'カテゴリ別の総使用状況', '카테고리별 총 사용량', 'Łączne użycie według kategorii', 'Распределение общего использования по категориям', '按类别划分的总使用量'])),
    'Günlük kullanımın kategorilere göre dağılımı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['توزيع الاستخدام اليومي حسب الفئة', 'Denní používání podle kategorií', 'Napi használat kategóriánként', 'カテゴリ別の1日の使用状況', '카테고리별 일일 사용량', 'Dzienne użycie według kategorii', 'Распределение ежедневного использования по категориям', '按类别划分的每日使用量'])),
    'Bu ay en fazla kullanılan uygulamalar': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التطبيقات الأكثر استخدامًا هذا الشهر', 'Nejpoužívanější aplikace tohoto měsíce', 'A hónap legtöbbet használt alkalmazásai', '今月最も使用したアプリ', '이번 달 가장 많이 사용한 앱', 'Najczęściej używane aplikacje w tym miesiącu', 'Самые используемые приложения в этом месяце', '本月使用最多的应用'])),
    'Son 7 günün kullanım eğilimi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اتجاه الاستخدام لآخر 7 أيام', 'Trend používání za posledních 7 dní', 'Az elmúlt 7 nap használati trendje', '過去7日間の使用傾向', '최근 7일 사용 추세', 'Trend użycia z ostatnich 7 dni', 'Тенденция использования за последние 7 дней', '最近7天使用趋势'])),
    'Bu hafta ve önceki haftanın özeti': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['ملخص هذا الأسبوع والأسبوع السابق', 'Shrnutí tohoto a předchozího týdne', 'E hét és az előző hét összefoglalója', '今週と先週の概要', '이번 주와 지난주 요약', 'Podsumowanie tego i poprzedniego tygodnia', 'Сводка этой и предыдущей недели', '本周与上周摘要'])),
    'Bir güne tıklayarak o günün ayrıntılarını açabilirsin.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['انقر على يوم لفتح تفاصيله.', 'Kliknutím na den otevřeš jeho podrobnosti.', 'Kattints egy napra a részletek megnyitásához.', '日付をクリックすると詳細を開けます。', '날짜를 클릭하면 세부 정보를 볼 수 있습니다.', 'Kliknij dzień, aby otworzyć szczegóły.', 'Нажмите на день, чтобы открыть подробности.', '点击某一天即可打开当天的详细信息。'])),
    'Henüz veri yok': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد بيانات بعد', 'Zatím žádná data', 'Még nincs adat', 'まだデータがありません', '아직 데이터가 없습니다', 'Brak danych', 'Данных пока нет', '暂无数据'])),
    'V3.0\nYerel veri • Gizlilik öncelikli': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['V3.0\nبيانات محلية • الخصوصية أولاً', 'V3.0\nLokální data • Soukromí na prvním místě', 'V3.0\nHelyi adatok • Adatvédelem az első', 'V3.0\nローカルデータ • プライバシー優先', 'V3.0\n로컬 데이터 • 개인정보 보호 우선', 'V3.0\nDane lokalne • Prywatność przede wszystkim', 'V3.0\nЛокальные данные • Конфиденциальность прежде всего', 'V3.0\n本地数据 • 隐私优先'])),
    'Örn. Eğitim, Sosyal Medya, Tasarım...': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['مثال: التعليم، وسائل التواصل، التصميم...', 'Např. vzdělávání, sociální sítě, design...', 'Pl. oktatás, közösségi média, tervezés...', '例：教育、SNS、デザイン…', '예: 교육, 소셜 미디어, 디자인...', 'Np. edukacja, media społecznościowe, projektowanie...', 'Напр.: образование, соцсети, дизайн...', '例如：教育、社交媒体、设计……'])),
    'aktif gün ortalaması': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['متوسط الأيام النشطة', 'průměr aktivních dnů', 'aktív napok átlaga', 'アクティブ日の平均', '활성 일수 평균', 'średnia aktywnych dni', 'среднее активных дней', '活跃日平均'])),
    'Toplam': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الإجمالي', 'Celkem', 'Összesen', '合計', '총합', 'Łącznie', 'Всего', '总计'])),
    'Seçilen ayın tüm kullanım verileri silinecek. Bu işlem geri alınamaz.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['سيتم حذف جميع بيانات الاستخدام للشهر المحدد. لا يمكن التراجع عن هذا الإجراء.', 'Všechna data používání za vybraný měsíc budou smazána. Tuto akci nelze vrátit.', 'A kiválasztott hónap összes használati adata törlődik. Ez a művelet nem vonható vissza.', '選択した月のすべての使用データを削除します。この操作は元に戻せません。', '선택한 달의 모든 사용 데이터가 삭제됩니다. 이 작업은 되돌릴 수 없습니다.', 'Wszystkie dane użycia z wybranego miesiąca zostaną usunięte. Tej operacji nie można cofnąć.', 'Все данные использования за выбранный месяц будут удалены. Это действие нельзя отменить.', '所选月份的所有使用数据都将被删除。此操作无法撤销。'])),
    'Kesintisiz Bilgisayar Kullanımı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الاستخدام المتواصل للكمبيوتر', 'Nepřetržité používání počítače', 'Folyamatos számítógép-használat', '連続したパソコン使用', '연속 컴퓨터 사용', 'Nieprzerwane korzystanie z komputera', 'Непрерывное использование компьютера', '连续电脑使用'])),
    'Henüz yeterli veri yok.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد بيانات كافية بعد.', 'Zatím není dostatek dat.', 'Még nincs elég adat.', 'まだ十分なデータがありません。', '아직 충분한 데이터가 없습니다.', 'Brak jeszcze wystarczającej ilości danych.', 'Пока недостаточно данных.', '暂时没有足够的数据。'])),
    'Bu tarihte uygulama kullanımı bulunmuyor.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا يوجد استخدام للتطبيقات في هذا التاريخ.', 'V tento den nebylo zaznamenáno používání aplikací.', 'Ezen a napon nem volt alkalmazáshasználat.', 'この日のアプリ使用記録はありません。', '이 날짜에는 앱 사용 기록이 없습니다.', 'Brak użycia aplikacji w tym dniu.', 'В этот день использования приложений не было.', '这一天没有应用使用记录。'])),
    'Harika gidiyorsun!': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['أنت تقوم بعمل رائع!', 'Daří se ti skvěle!', 'Nagyszerűen haladsz!', 'とても順調です！', '아주 잘하고 있어요!', 'Świetnie Ci idzie!', 'Отлично получается!', '做得非常好！'])),
    'Devam et, iyi gidiyorsun.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['استمر، أنت تقوم بعمل جيد.', 'Pokračuj, daří se ti dobře.', 'Csak így tovább, jól haladsz.', 'その調子で続けましょう。', '계속 잘하고 있어요.', 'Tak trzymaj, świetnie Ci idzie.', 'Продолжай, всё идёт хорошо.', '继续保持，你做得很好。'])),
    'Kategori, uygulama sınıflandırmasında kullanılabilir.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['يمكن استخدام الفئة لتصنيف التطبيقات.', 'Kategorii lze použít ke klasifikaci aplikací.', 'A kategória alkalmazások osztályozására használható.', 'カテゴリはアプリの分類に使用できます。', '카테고리는 앱 분류에 사용할 수 있습니다.', 'Kategoria może służyć do klasyfikowania aplikacji.', 'Категорию можно использовать для классификации приложений.', '该分类可用于应用分类。'])),
    'Uygulamalar arasında geçen aktif kullanım sürelerini ve kesintisiz oturumları zaman çizelgesinde inceleyebilirsin.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['يمكنك مراجعة أوقات الاستخدام النشط بين التطبيقات والجلسات المتواصلة في المخطط الزمني.', 'Na časové ose můžeš prohlížet aktivní používání mezi aplikacemi a nepřetržité relace.', 'Az idővonalon megtekintheted az alkalmazások közötti aktív használatot és a folyamatos munkameneteket.', 'タイムラインでアプリ間のアクティブ使用時間と連続セッションを確認できます。', '타임라인에서 앱 간 활성 사용 시간과 연속 세션을 확인할 수 있습니다.', 'Na osi czasu możesz przeglądać aktywne użycie między aplikacjami i nieprzerwane sesje.', 'На временной шкале можно просматривать активное использование между приложениями и непрерывные сеансы.', '你可以在时间线上查看应用之间的活跃使用时间和连续会话。'])),
    'En yeni kayıtlar üstte gösterilir.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تظهر أحدث السجلات في الأعلى.', 'Nejnovější záznamy jsou zobrazeny nahoře.', 'A legújabb bejegyzések felül jelennek meg.', '新しい記録が上に表示されます。', '최신 기록이 위에 표시됩니다.', 'Najnowsze wpisy są wyświetlane na górze.', 'Новые записи отображаются сверху.', '最新记录显示在顶部。'])),
    '‹ Önceki Ay': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['‹ الشهر السابق', '‹ Předchozí měsíc', '‹ Előző hónap', '‹ 前の月', '‹ 이전 달', '‹ Poprzedni miesiąc', '‹ Предыдущий месяц', '‹ 上个月'])),
    'Sonraki Ay ›': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الشهر التالي ›', 'Další měsíc ›', 'Következő hónap ›', '次の月 ›', '다음 달 ›', 'Następny miesiąc ›', 'Следующий месяц ›', '下个月 ›'])),
    'Son 7 gün ile önceki 7 gün karşılaştırması.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['مقارنة آخر 7 أيام بالـ7 أيام السابقة.', 'Porovnání posledních 7 dní s předchozími 7 dny.', 'Az elmúlt 7 nap összehasonlítása az előző 7 nappal.', '過去7日間とその前の7日間の比較。', '최근 7일과 이전 7일 비교.', 'Porównanie ostatnich 7 dni z poprzednimi 7 dniami.', 'Сравнение последних 7 дней с предыдущими 7 днями.', '最近7天与前7天的比较。'])),
    'UYGULAMA KULLANIMI • DAİRE GRAFİĞİ': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['استخدام التطبيقات • مخطط دائري', 'POUŽÍVÁNÍ APLIKACÍ • KOLÁČOVÝ GRAF', 'ALKALMAZÁSHASZNÁLAT • KÖRDIAGRAM', 'アプリ使用 • 円グラフ', '앱 사용량 • 원형 차트', 'UŻYCIE APLIKACJI • WYKRES KOŁOWY', 'ИСПОЛЬЗОВАНИЕ ПРИЛОЖЕНИЙ • КРУГОВАЯ ДИАГРАММА', '应用使用 • 饼图'])),
    'UYGULAMA KULLANIMI': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['استخدام التطبيقات', 'POUŽÍVÁNÍ APLIKACÍ', 'ALKALMAZÁSHASZNÁLAT', 'アプリ使用', '앱 사용량', 'UŻYCIE APLIKACJI', 'ИСПОЛЬЗОВАНИЕ ПРИЛОЖЕНИЙ', '应用使用'])),
    'OTURUMLAR': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الجلسات', 'RELACE', 'MUNKAMENETEK', 'セッション', '세션', 'SESJE', 'СЕАНСЫ', '会话'])),
    'Uygulamanın aydınlık, karanlık veya Windows sistem temasını kullanmasını seç.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اختر المظهر الفاتح أو الداكن أو مظهر نظام Windows للتطبيق.', 'Zvol, zda aplikace použije světlý, tmavý nebo systémový motiv Windows.', 'Válaszd ki a világos, sötét vagy a Windows rendszertémát.', 'アプリでライト、ダーク、またはWindowsのシステムテーマを使用するか選択します。', '앱에서 밝은 테마, 어두운 테마 또는 Windows 시스템 테마를 선택하세요.', 'Wybierz jasny, ciemny lub systemowy motyw Windows.', 'Выберите светлую, тёмную или системную тему Windows.', '选择应用使用浅色、深色或 Windows 系统主题。'])),
    'Bilgisayar açıldığında Dijital Denge otomatik olarak çalışsın.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تشغيل Dijital Denge تلقائيًا عند بدء تشغيل الكمبيوتر.', 'Spouštět Dijital Denge automaticky při spuštění počítače.', 'A Dijital Denge automatikusan induljon a számítógép bekapcsolásakor.', 'パソコン起動時にDijital Dengeを自動的に起動します。', '컴퓨터가 켜질 때 Dijital Denge를 자동으로 실행합니다.', 'Uruchamiaj Dijital Denge automatycznie po włączeniu komputera.', 'Автоматически запускать Dijital Denge при включении компьютера.', '电脑启动时自动运行 Dijital Denge。'])),
    'Otomatik kategorilere ek olarak kendi kategorilerini oluşturabilir ve Uygulamalar sayfasında uygulamalara atayabilirsin.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['بالإضافة إلى الفئات التلقائية، يمكنك إنشاء فئاتك الخاصة وتعيينها للتطبيقات في صفحة التطبيقات.', 'Kromě automatických kategorií můžeš vytvořit vlastní kategorie a přiřadit je aplikacím na stránce Aplikace.', 'Az automatikus kategóriák mellett saját kategóriákat is létrehozhatsz és hozzárendelheted őket az Alkalmazások oldalon.', '自動カテゴリに加えて独自のカテゴリを作成し、「アプリケーション」ページでアプリに割り当てられます。', '자동 카테고리 외에도 사용자 지정 카테고리를 만들고 앱 페이지에서 앱에 지정할 수 있습니다.', 'Oprócz kategorii automatycznych możesz tworzyć własne i przypisywać je aplikacjom na stronie Aplikacje.', 'Помимо автоматических категорий, можно создавать собственные и назначать их приложениям на странице приложений.', '除了自动分类外，你还可以创建自定义分类，并在应用页面中将其分配给应用。'])),
    '＋ Yeni Kategori': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['＋ فئة جديدة', '＋ Nová kategorie', '＋ Új kategória', '＋ 新しいカテゴリ', '＋ 새 카테고리', '＋ Nowa kategoria', '＋ Новая категория', '＋ 新建分类'])),
    'Aşağıdaki aydaki tüm kullanım kayıtlarını sil': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['حذف جميع سجلات الاستخدام للشهر المحدد أدناه', 'Smazat všechny záznamy používání z níže uvedeného měsíce', 'Az alábbi hónap összes használati adatának törlése', '以下の月のすべての使用記録を削除', '아래 선택한 달의 모든 사용 기록 삭제', 'Usuń wszystkie dane użycia z poniższego miesiąca', 'Удалить все записи использования за указанный ниже месяц', '删除以下月份的所有使用记录'])),
    'Sistemde tutulan tüm kullanım verileri silinecek. Ayarlar korunur ve bu işlem geri alınamaz.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['سيتم حذف جميع بيانات الاستخدام المخزنة في النظام. ستبقى الإعدادات محفوظة ولا يمكن التراجع عن هذا الإجراء.', 'Všechna uložená data používání budou smazána. Nastavení zůstanou zachována a tuto akci nelze vrátit.', 'A rendszerben tárolt összes használati adat törlődik. A beállítások megmaradnak, ez a művelet nem vonható vissza.', 'システムに保存されているすべての使用データを削除します。設定は保持され、この操作は元に戻せません。', '시스템에 저장된 모든 사용 데이터가 삭제됩니다. 설정은 유지되며 이 작업은 되돌릴 수 없습니다.', 'Wszystkie zapisane dane użycia zostaną usunięte. Ustawienia zostaną zachowane, a tej operacji nie można cofnąć.', 'Все сохранённые данные использования будут удалены. Настройки сохранятся, а отменить это действие невозможно.', '系统中保存的所有使用数据都将被删除。设置会保留，此操作无法撤销。'])),
    '⚠  2 saattir mola vermeden kullanıyorsun. Kısa bir mola ver.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['⚠  لقد استخدمت الكمبيوتر لمدة ساعتين دون استراحة. خذ استراحة قصيرة.', '⚠  Počítač používáš 2 hodiny bez přestávky. Udělej si krátkou pauzu.', '⚠  2 órája használod a számítógépet szünet nélkül. Tarts egy rövid szünetet.', '⚠  2時間休憩なしでパソコンを使用しています。短い休憩を取りましょう。', '⚠  2시간 동안 쉬지 않고 컴퓨터를 사용하고 있어요. 잠시 쉬어 주세요.', '⚠  Korzystasz z komputera od 2 godzin bez przerwy. Zrób krótką przerwę.', '⚠  Вы используете компьютер 2 часа без перерыва. Сделайте небольшой перерыв.', '⚠  你已经连续使用电脑 2 小时，请休息一下。'])),
    'Bugün henüz tamamlanmış bir oturum bulunmuyor.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد جلسات مكتملة اليوم بعد.', 'Dnes zatím nejsou žádné dokončené relace.', 'Ma még nincs befejezett munkamenet.', '今日はまだ完了したセッションがありません。', '오늘은 아직 완료된 세션이 없습니다.', 'Brak jeszcze ukończonych sesji na dziś.', 'Сегодня завершённых сеансов пока нет.', '今天还没有已完成的会话。'])),
    'Henüz uygulama verisi yok.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد بيانات تطبيقات بعد.', 'Zatím nejsou žádná data aplikací.', 'Még nincs alkalmazásadat.', 'まだアプリデータがありません。', '아직 앱 데이터가 없습니다.', 'Brak jeszcze danych aplikacji.', 'Данных приложений пока нет.', '暂无应用数据。'])),
    'Bu tarihte kayıtlı uygulama verisi yok.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد بيانات تطبيقات مسجلة في هذا التاريخ.', 'V tento den nejsou zaznamenána žádná data aplikací.', 'Ezen a napon nincs rögzített alkalmazásadat.', 'この日に記録されたアプリデータはありません。', '이 날짜에는 기록된 앱 데이터가 없습니다.', 'Brak zarejestrowanych danych aplikacji dla tego dnia.', 'На эту дату нет записанных данных приложений.', '这一天没有记录的应用数据。'])),
    'Bu tarihte oturum kaydı yok.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد سجلات جلسات في هذا التاريخ.', 'V tento den nejsou žádné záznamy relací.', 'Ezen a napon nincs munkamenet-bejegyzés.', 'この日のセッション記録はありません。', '이 날짜에는 세션 기록이 없습니다.', 'Brak zapisów sesji dla tego dnia.', 'На эту дату нет записей сеансов.', '这一天没有会话记录。'])),
    '0 dk': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['0 د', '0 min', '0 p', '0分', '0분', '0 min', '0 мин', '0分钟'])),
    'kayıt': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['سجل', 'záznam', 'bejegyzés', '記録', '기록', 'rekord', 'запись', '记录'])),
    'SEÇİLEN TARİH': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التاريخ المحدد', 'VYBRANÉ DATUM', 'KIVÁLASZTOTT DÁTUM', '選択した日付', '선택한 날짜', 'WYBRANA DATA', 'ВЫБРАННАЯ ДАТА', '所选日期'])),
    'Grafik yüklenemedi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تعذر تحميل الرسم البياني', 'Graf se nepodařilo načíst', 'A diagram nem tölthető be', 'グラフを読み込めませんでした', '그래프를 불러오지 못했습니다', 'Nie udało się załadować wykresu', 'Не удалось загрузить график', '无法加载图表'])),
})

# COMPLETE_ALL_DIRECT_TRANSLATE_KEYS
TRANS_8.update({
    'MENÜ': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['القائمة', 'NABÍDKA', 'MENÜ', 'メニュー', '메뉴', 'MENU', 'МЕНЮ', '菜单'])),
    'V3.0\nYerel veri • Gizlilik öncelikli': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['V3.0\nبيانات محلية • الخصوصية أولاً', 'V3.0\nLokální data • Soukromí na prvním místě', 'V3.0\nHelyi adatok • Adatvédelem az első', 'V3.0\nローカルデータ • プライバシー優先', 'V3.0\n로컬 데이터 • 개인정보 보호 우선', 'V3.0\nDane lokalne • Prywatność przede wszystkim', 'V3.0\nЛокальные данные • Конфиденциальность прежде всего', 'V3.0\n本地数据 • 隐私优先'])),
    'Genel Bakış': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['نظرة عامة', 'Přehled', 'Áttekintés', '概要', '개요', 'Przegląd', 'Обзор', '概览'])),
    'Bugünkü dijital dengen nasıl?': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['كيف هو توازنك الرقمي اليوم؟', 'Jaká je dnes tvoje digitální rovnováha?', 'Milyen ma a digitális egyensúlyod?', '今日のデジタルバランスはどうですか？', '오늘의 디지털 균형은 어떤가요?', 'Jak wygląda dziś Twoja równowaga cyfrowa?', 'Каков твой цифровой баланс сегодня?', '今天你的数字平衡如何？'])),
    'Merhaba!': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['مرحبًا!', 'Ahoj!', 'Szia!', 'こんにちは！', '안녕하세요!', 'Cześć!', 'Привет!', '你好！'])),
    'Daha iyi bir sen mümkün.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['يمكنك أن تصبح نسخة أفضل من نفسك.', 'Je možné být lepší verzí sebe sama.', 'Lehetsz még jobb önmagad.', 'もっと良い自分になれます。', '더 나은 나를 만들 수 있어요.', 'Możesz stać się lepszą wersją siebie.', 'Ты можешь стать лучше.', '你可以成为更好的自己。'])),
    'Tümünü Gör': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['عرض الكل', 'Zobrazit vše', 'Összes megtekintése', 'すべて表示', '모두 보기', 'Pokaż wszystko', 'Показать всё', '查看全部'])),
    'Uygulamalar': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التطبيقات', 'Aplikace', 'Alkalmazások', 'アプリ', '앱', 'Aplikacje', 'Приложения', '应用'])),
    'Bugün henüz uygulama verisi yok.': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد بيانات تطبيقات اليوم بعد.', 'Dnes zatím nejsou žádná data aplikací.', 'Ma még nincs alkalmazásadat.', '今日はまだアプリデータがありません。', '오늘은 아직 앱 데이터가 없습니다.', 'Brak jeszcze danych aplikacji na dziś.', 'Сегодня данных приложений пока нет.', '今天还没有应用数据。'])),
    'uygulama': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تطبيق', 'aplikace', 'alkalmazás', 'アプリ', '앱', 'aplikacja', 'приложение', '应用'])),
    'Oturum:': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الجلسة:', 'Relace:', 'Munkamenet:', 'セッション:', '세션:', 'Sesja:', 'Сеанс:', '会话：'])),
    'En uzun:': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الأطول:', 'Nejdelší:', 'Leghosszabb:', '最長:', '가장 긴 항목:', 'Najdłuższa:', 'Самый длинный:', '最长：'])),
    'Kategori değiştir': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تغيير الفئة', 'Změnit kategorii', 'Kategória módosítása', 'カテゴリを変更', '카테고리 변경', 'Zmień kategorię', 'Изменить категорию', '更改类别'])),
    'kategorisi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الفئة', 'kategorie', 'kategória', 'カテゴリ', '카테고리', 'kategoria', 'категория', '类别'])),
    'Kaydet': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['حفظ', 'Uložit', 'Mentés', '保存', '저장', 'Zapisz', 'Сохранить', '保存'])),
    'Yeni kategori': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['فئة جديدة', 'Nová kategorie', 'Új kategória', '新しいカテゴリ', '새 카테고리', 'Nowa kategoria', 'Новая категория', '新建分类'])),
    'Yeni kategori adı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اسم الفئة الجديدة', 'Název nové kategorie', 'Új kategória neve', '新しいカテゴリ名', '새 카테고리 이름', 'Nazwa nowej kategorii', 'Название новой категории', '新分类名称'])),
    'Kategori Oluştur': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إنشاء الفئة', 'Vytvořit kategorii', 'Kategória létrehozása', 'カテゴリを作成', '카테고리 만들기', 'Utwórz kategorię', 'Создать категорию', '创建分类'])),
    'Oturumlar': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الجلسات', 'Relace', 'Munkamenetek', 'セッション', '세션', 'Sesje', 'Сеансы', '会话'])),
    'Bugünkü Oturumlar': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['جلسات اليوم', 'Dnešní relace', 'Mai munkamenetek', '今日のセッション', '오늘의 세션', 'Dzisiejsze sesje', 'Сеансы сегодня', '今天的会话'])),
    'Zaman Çizelgesi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الخط الزمني', 'Časová osa', 'Idővonal', 'タイムライン', '타임라인', 'Oś czasu', 'Временная шкала', '时间线'])),
    'Dinlenme': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['استراحة', 'Odpočinek', 'Pihenés', '休憩', '휴식', 'Odpoczynek', 'Отдых', '休息'])),
    'İstatistikler': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الإحصائيات', 'Statistiky', 'Statisztikák', '統計', '통계', 'Statystyki', 'Статистика', '统计'])),
    'Detaylı kullanım analizi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تحليل مفصل للاستخدام', 'Podrobná analýza používání', 'Részletes használati elemzés', '詳細な使用状況分析', '상세 사용량 분석', 'Szczegółowa analiza użycia', 'Подробный анализ использования', '详细使用分析'])),
    'Analiz dönemi': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['فترة التحليل', 'Období analýzy', 'Elemzési időszak', '分析期間', '분석 기간', 'Okres analizy', 'Период анализа', '分析期间'])),
    'aktif gün': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['يوم نشط', 'aktivní den', 'aktív nap', 'アクティブ日', '활성 일', 'aktywny dzień', 'активный день', '活跃日'])),
    '7 Gün': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['7 أيام', '7 dní', '7 nap', '7日間', '7일', '7 dni', '7 дней', '7天'])),
    'Seçilen Gün': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اليوم المحدد', 'Vybraný den', 'Kiválasztott nap', '選択した日', '선택한 날짜', 'Wybrany dzień', 'Выбранный день', '所选日期'])),
    'Veri yok': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['لا توجد بيانات', 'Žádná data', 'Nincs adat', 'データなし', '데이터 없음', 'Brak danych', 'Нет данных', '无数据'])),
    'Geçen hafta': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الأسبوع الماضي', 'Minulý týden', 'Múlt hét', '先週', '지난주', 'Zeszły tydzień', 'Прошлая неделя', '上周'])),
    'Toplam aktif kullanım': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إجمالي الاستخدام النشط', 'Celkové aktivní používání', 'Teljes aktív használat', '総アクティブ使用時間', '총 활성 사용량', 'Łączne aktywne użycie', 'Общее активное использование', '总活跃使用量'])),
    'En uzun kesintisiz kullanım': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['أطول استخدام متواصل', 'Nejdelší nepřetržité používání', 'Leghosszabb folyamatos használat', '最長連続使用時間', '가장 긴 연속 사용', 'Najdłuższe nieprzerwane użycie', 'Самое длительное непрерывное использование', '最长连续使用'])),
    'Uygulama': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['التطبيق', 'Aplikace', 'Alkalmazás', 'アプリ', '앱', 'Aplikacja', 'Приложение', '应用'])),
    'Kategori': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الفئة', 'Kategorie', 'Kategória', 'カテゴリ', '카테고리', 'Kategoria', 'Категория', '类别'])),
    'Süre': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['المدة', 'Doba', 'Időtartam', '時間', '시간', 'Czas', 'Продолжительность', '时长'])),
    'Pay': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['النسبة', 'Podíl', 'Arány', '割合', '비율', 'Udział', 'Доля', '占比'])),
    'Ayarlar': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['الإعدادات', 'Nastavení', 'Beállítások', '設定', '설정', 'Ustawienia', 'Настройки', '设置'])),
    'GÖRÜNÜM': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['المظهر', 'VZHLED', 'MEGJELENÉS', '外観', '화면', 'WYGLĄD', 'ВИД', '外观'])),
    'Dil': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اللغة', 'Jazyk', 'Nyelv', '言語', '언어', 'Język', 'Язык', '语言'])),
    'VERİ': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['البيانات', 'DATA', 'ADATOK', 'データ', '데이터', 'DANE', 'ДАННЫЕ', '数据'])),
    'Verileri dışa aktar (JSON)': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['تصدير البيانات (JSON)', 'Exportovat data (JSON)', 'Adatok exportálása (JSON)', 'データをエクスポート（JSON）', '데이터 내보내기(JSON)', 'Eksportuj dane (JSON)', 'Экспортировать данные (JSON)', '导出数据（JSON）'])),
    'Aylık Veri Sıfırlama': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إعادة تعيين البيانات الشهرية', 'Měsíční reset dat', 'Havi adatok visszaállítása', '月間データをリセット', '월간 데이터 초기화', 'Reset danych miesięcznych', 'Сброс данных за месяц', '重置月度数据'])),
    'Aşağıdaki aydaki tüm kullanım kayıtlarını sil': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['حذف جميع سجلات الاستخدام للشهر المحدد أدناه', 'Smazat všechny záznamy používání z níže uvedeného měsíce', 'Az alábbi hónap összes használati adatának törlése', '以下の月のすべての使用記録を削除', '아래 선택한 달의 모든 사용 기록 삭제', 'Usuń wszystkie dane użycia z poniższego miesiąca', 'Удалить все записи использования за указанный ниже месяц', '删除以下月份的所有使用记录'])),
    'Ay seçin': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['اختر الشهر', 'Vyberte měsíc', 'Válasszon hónapot', '月を選択', '월 선택', 'Wybierz miesiąc', 'Выберите месяц', '选择月份'])),
    'Seçilen Ayın Verilerini Sıfırla': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إعادة تعيين بيانات الشهر المحدد', 'Resetovat data vybraného měsíce', 'A kiválasztott hónap adatainak visszaállítása', '選択した月のデータをリセット', '선택한 달 데이터 초기화', 'Resetuj dane wybranego miesiąca', 'Сбросить данные выбранного месяца', '重置所选月份数据'])),
    'Tüm Verileri Sıfırla': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إعادة تعيين جميع البيانات', 'Resetovat všechna data', 'Összes adat visszaállítása', 'すべてのデータをリセット', '모든 데이터 초기화', 'Resetuj wszystkie dane', 'Сбросить все данные', '重置所有数据'])),
    'Paneli Aç': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['فتح اللوحة', 'Otevřít panel', 'Vezérlőpult megnyitása', 'パネルを開く', '패널 열기', 'Otwórz panel', 'Открыть панель', '打开面板'])),
    'Tamamen Kapat': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إغلاق بالكامل', 'Úplně zavřít', 'Teljes bezárás', '完全に終了', '완전히 닫기', 'Zamknij całkowicie', 'Полностью закрыть', '完全关闭'])),
    '0 dk': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['0 د', '0 min', '0 p', '0分', '0분', '0 min', '0 мин', '0分钟'])),
})

# FINAL_SESSION_CARD_TRANSLATIONS
TRANS_8.update({
    'Oturum Sayısı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['عدد الجلسات', 'Počet relací', 'Munkamenetek száma', 'セッション数', '세션 수', 'Liczba sesji', 'Количество сеансов', '会话数量'])),
    'En Uzun Oturum': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['أطول جلسة', 'Nejdelší relace', 'Leghosszabb munkamenet', '最長セッション', '가장 긴 세션', 'Najdłuższa sesja', 'Самый длинный сеанс', '最长会话'])),
    'Mola Sayısı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['عدد فترات الراحة', 'Počet přestávek', 'Szünetek száma', '休憩回数', '휴식 횟수', 'Liczba przerw', 'Количество перерывов', '休息次数'])),
    'Toplam Oturum': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['إجمالي الجلسات', 'Celkem relací', 'Összes munkamenet', '総セッション数', '총 세션 수', 'Łączna liczba sesji', 'Всего сеансов', '会话总数'])),
    'Ortalama Oturum': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['متوسط الجلسة', 'Průměrná relace', 'Átlagos munkamenet', '平均セッション', '평균 세션', 'Średnia sesja', 'Средний сеанс', '平均会话'])),
    'En Uzun Kesintisiz': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['أطول استخدام متواصل', 'Nejdelší nepřetržité používání', 'Leghosszabb folyamatos használat', '最長連続使用', '가장 긴 연속 사용', 'Najdłuższe nieprzerwane użycie', 'Самое длительное непрерывное использование', '最长连续使用'])),
    'Mola': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['استراحة', 'Pauza', 'Szünet', '休憩', '휴식', 'Przerwa', 'Перерыв', '休息'])),
    'Kısa Mola': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['استراحة قصيرة', 'Krátká pauza', 'Rövid szünet', '短い休憩', '짧은 휴식', 'Krótka przerwa', 'Короткий перерыв', '短暂休息'])),
    'Uzun Mola': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['استراحة طويلة', 'Dlouhá pauza', 'Hosszú szünet', '長い休憩', '긴 휴식', 'Długa przerwa', 'Длинный перерыв', '长时间休息'])),
    'Bilgisayar Dışı': dict(zip(['ar', 'cs', 'hu', 'ja', 'ko', 'pl', 'ru', 'zh-CN'], ['خارج الكمبيوتر', 'Mimo počítač', 'Számítógépen kívül', 'パソコン外', '컴퓨터를 사용하지 않음', 'Poza komputerem', 'Без компьютера', '离开电脑'])),
})



_COMPLETE_UI_TRANSLATIONS = {
    "MENÜ": ["MENU","MENÜ","MENU","MENÚ","MENU","MENU","MENU","القائمة","NABÍDKA","MENÜ","メニュー","메뉴","MENU","МЕНЮ","菜单"],
    "7 Gün": ["7 Days","7 Tage","7 jours","7 días","7 giorni","7 dias","7 dagen","7 أيام","7 dní","7 nap","7日間","7일","7 dni","7 дней","7天"],
    "Bir güne tıklayarak o günün ayrıntılarını açabilirsin.": [
        "Click a day to open its details.","Klicken Sie auf einen Tag, um seine Details zu öffnen.",
        "Cliquez sur un jour pour afficher ses détails.","Haz clic en un día para ver sus detalles.",
        "Fai clic su un giorno per aprirne i dettagli.","Clique em um dia para abrir os detalhes.",
        "Klik op een dag om de details te openen.","انقر على يوم لفتح تفاصيله.",
        "Kliknutím na den otevřete jeho podrobnosti.","Kattints egy napra a részletek megnyitásához.",
        "日付をクリックして詳細を開けます。","날짜를 클릭하여 세부 정보를 엽니다.",
        "Kliknij dzień, aby otworzyć jego szczegóły.","Нажмите на день, чтобы открыть его подробности.","点击某一天以打开其详细信息。"
    ],
    "En uzun:": ["Longest:","Längste:","Plus longue :","Más larga:","Più lunga:","Mais longa:","Langste:","الأطول:","Nejdelší:","Leghosszabb:","最長:","가장 긴 시간:","Najdłuższa:","Самая длинная:","最长："],
    "Grafik yüklenemedi": ["Chart could not be loaded","Diagramm konnte nicht geladen werden","Impossible de charger le graphique","No se pudo cargar el gráfico","Impossibile caricare il grafico","Não foi possível carregar o gráfico","Grafiek kon niet worden geladen","تعذر تحميل الرسم البياني","Graf se nepodařilo načíst","A diagram nem tölthető be","グラフを読み込めませんでした","그래프를 불러올 수 없습니다","Nie można załadować wykresu","Не удалось загрузить график","无法加载图表"],
    "Henüz uygulama verisi yok.": ["No application data yet.","Noch keine Anwendungsdaten.","Aucune donnée d’application pour le moment.","Aún no hay datos de aplicaciones.","Nessun dato applicativo ancora.","Ainda não há dados de aplicativos.","Nog geen applicatiegegevens.","لا توجد بيانات تطبيقات بعد.","Zatím žádná data aplikací.","Még nincs alkalmazásadat.","まだアプリデータがありません。","아직 앱 데이터가 없습니다.","Brak jeszcze danych aplikacji.","Данных приложений пока нет.","暂无应用数据。"],
    "Henüz veri yok": ["No data yet","Noch keine Daten","Aucune donnée","Aún no hay datos","Nessun dato","Ainda não há dados","Nog geen gegevens","لا توجد بيانات بعد","Zatím žádná data","Még nincs adat","まだデータがありません","아직 데이터가 없습니다","Brak danych","Данных пока нет","暂无数据"],
    "Oturum:": ["Session:","Sitzung:","Session :","Sesión:","Sessione:","Sessão:","Sessie:","الجلسة:","Relace:","Munkamenet:","セッション:","세션:","Sesja:","Сеанс:","会话："],
    "Paneli Aç": ["Open Dashboard","Dashboard öffnen","Ouvrir le tableau de bord","Abrir panel","Apri pannello","Abrir painel","Dashboard openen","فتح اللوحة","Otevřít panel","Panel megnyitása","パネルを開く","패널 열기","Otwórz panel","Открыть панель","打开面板"],
    "Tamamen Kapat": ["Exit Completely","Vollständig schließen","Fermer complètement","Cerrar completamente","Chiudi completamente","Fechar completamente","Volledig sluiten","إغلاق بالكامل","Úplně zavřít","Teljes bezárás","完全に閉じる","완전히 닫기","Zamknij całkowicie","Полностью закрыть","完全关闭"],

    "DİJİTAL DENGE": ["DIGITAL BALANCE","DIGITAL BALANCE","ÉQUILIBRE NUMÉRIQUE","EQUILIBRIO DIGITAL","EQUILIBRIO DIGITALE","EQUILÍBRIO DIGITAL","DIGITAAL EVENWICHT","التوازن الرقمي","DIGITÁLNÍ ROVNOVÁHA","DIGITÁLIS EGYENSÚLY","デジタルバランス","디지털 균형","RÓWNOWAGA CYFROWA","ЦИФРОВОЙ БАЛАНС","数字平衡"],
    "Daha bilinçli, daha sen": ["More mindful, more you","Bewusster, mehr du","Plus conscient, plus vous","Más consciente, más tú","Più consapevole, più te","Mais consciente, mais você","Bewuster, meer jezelf","أكثر وعيًا، أكثر أنت","Vědoměji, více ty","Tudatosabban, önmagadként","もっと意識的に、もっと自分らしく","더 의식적으로, 더 나답게","Bardziej świadomie, bardziej sobą","Осознаннее, больше себя","更自觉，更做自己"],
    "Bugünkü kullanım": ["Today's usage","Nutzung heute","Utilisation aujourd’hui","Uso de hoy","Utilizzo di oggi","Uso de hoje","Gebruik vandaag","استخدام اليوم","Dnešní používání","Mai használat","今日の使用時間","오늘의 사용량","Dzisiejsze użycie","Использование сегодня","今日使用量"],
    "AFK hariç": ["Excluding AFK","Ohne AFK","Hors AFK","Sin AFK","Escluso AFK","Excluindo AFK","Exclusief AFK","باستثناء AFK","Bez AFK","AFK nélkül","AFKを除く","AFK 제외","Z wyłączeniem AFK","Без AFK","不含 AFK"],
    "Verilen mola sayısı": ["Breaks taken","Genommene Pausen","Pauses prises","Pausas realizadas","Pause effettuate","Pausas feitas","Genomen pauzes","فترات الراحة","Počet přestávek","Vett szünetek","取得した休憩","취한 휴식 횟수","Liczba przerw","Количество перерывов","休息次数"],
    "kayıtlı mola": ["recorded breaks","aufgezeichnete Pausen","pauses enregistrées","pausas registradas","pause registrate","pausas registradas","geregistreerde pauzes","فترات راحة مسجلة","zaznamenané přestávky","rögzített szünetek","記録された休憩","기록된 휴식","zarejestrowane przerwy","записанных перерывов","已记录休息"],
    "Denge Skoru": ["Balance Score","Balance-Score","Score d’équilibre","Puntuación de equilibrio","Punteggio di equilibrio","Pontuação de equilíbrio","Balansscore","درجة التوازن","Skóre rovnováhy","Egyensúlypontszám","バランススコア","균형 점수","Wynik równowagi","Баланс","平衡分数"],
    "düne göre": ["vs. yesterday","gegenüber gestern","par rapport à hier","frente a ayer","rispetto a ieri","em relação a ontem","vergeleken met gisteren","مقارنةً بالأمس","oproti včerejšku","tegnaphoz képest","昨日との比較","어제 대비","w porównaniu z wczoraj","по сравнению со вчера","与昨天相比"],
    "2 saattir mola vermeden kullanıyorsun. Kısa bir mola ver.": ["You have been using the computer for 2 hours without a break. Take a short break.","Du nutzt den Computer seit 2 Stunden ohne Pause. Mach eine kurze Pause.","Vous utilisez l’ordinateur depuis 2 heures sans pause. Faites une courte pause.","Llevas 2 horas usando el ordenador sin descanso. Tómate un breve descanso.","Usi il computer da 2 ore senza pausa. Fai una breve pausa.","Você está usando o computador há 2 horas sem pausa. Faça uma pausa curta.","Je gebruikt de computer al 2 uur zonder pauze. Neem een korte pauze.","تستخدم الكمبيوتر منذ ساعتين دون استراحة. خذ استراحة قصيرة.","Používáte počítač 2 hodiny bez přestávky. Dejte si krátkou pauzu.","2 órája használod a számítógépet szünet nélkül. Tarts egy rövid szünetet.","2時間休憩なしでパソコンを使っています。少し休憩しましょう。","2시간 동안 쉬지 않고 컴퓨터를 사용했습니다. 잠시 쉬어 주세요.","Korzystasz z komputera od 2 godzin bez przerwy. Zrób krótką przerwę.","Вы используете компьютер 2 часа без перерыва. Сделайте небольшой перерыв.","你已经连续使用电脑2小时了。休息一下吧。"],
    "Kullanım Dağılımı (Bugün)": ["Usage Distribution (Today)","Nutzungsverteilung (heute)","Répartition de l’utilisation (aujourd’hui)","Distribución de uso (hoy)","Distribuzione utilizzo (oggi)","Distribuição de uso (hoje)","Gebruiksverdeling (vandaag)","توزيع الاستخدام (اليوم)","Rozložení používání (dnes)","Használati megoszlás (ma)","使用状況の分布（今日）","사용량 분포 (오늘)","Rozkład użycia (dzisiaj)","Распределение использования (сегодня)","使用分布（今天）"],
    "Son 7 Gün": ["Last 7 Days","Letzte 7 Tage","7 derniers jours","Últimos 7 días","Ultimi 7 giorni","Últimos 7 dias","Afgelopen 7 dagen","آخر 7 أيام","Posledních 7 dní","Elmúlt 7 nap","過去7日間","최근 7일","Ostatnie 7 dni","Последние 7 дней","最近7天"],
    "Toplam Kullanım": ["Total Usage","Gesamtnutzung","Utilisation totale","Uso total","Utilizzo totale","Uso total","Totaal gebruik","إجمالي الاستخدام","Celkové použití","Teljes használat","総使用時間","총 사용량","Łączne użycie","Общее использование","总使用量"],
    "Oturum Sayısı": ["Session Count","Sitzungsanzahl","Nombre de sessions","Número de sesiones","Numero di sessioni","Número de sessões","Aantal sessies","عدد الجلسات","Počet relací","Munkamenetek száma","セッション数","세션 수","Liczba sesji","Количество сеансов","会话数量"],
    "Mola Sayısı": ["Break Count","Pausenanzahl","Nombre de pauses","Número de pausas","Numero di pause","Número de pausas","Aantal pauzes","عدد فترات الراحة","Počet přestávek","Szünetek száma","休憩回数","휴식 횟수","Liczba przerw","Количество перерывов","休息次数"],
    "Günlük Ortalama": ["Daily Average","Tagesdurchschnitt","Moyenne quotidienne","Promedio diario","Media giornaliera","Média diária","Dagelijks gemiddelde","المتوسط اليومي","Denní průměr","Napi átlag","1日の平均","일일 평균","Średnia dzienna","Среднее за день","每日平均"],
    "En Verimli Gün": ["Most Productive Day","Produktivster Tag","Jour le plus productif","Día más productivo","Giorno più produttivo","Dia mais produtivo","Productiefste dag","أكثر يوم إنتاجية","Nejproduktivnější den","Legproduktívabb nap","最も生産的な日","가장 생산적인 날","Najbardziej produktywny dzień","Самый продуктивный день","效率最高的一天"],
    "En Yoğun Kategori": ["Most Used Category","Am häufigsten genutzte Kategorie","Catégorie la plus utilisée","Categoría más utilizada","Categoria più utilizzata","Categoria mais usada","Meest gebruikte categorie","الفئة الأكثر استخدامًا","Nejčastější kategorie","Leggyakrabban használt kategória","最も使用されたカテゴリ","가장 많이 사용한 카테고리","Najczęściej używana kategoria","Самая используемая категория","使用最多的类别"],
    "Günlük Kullanım Süresi": ["Daily Usage Time","Tägliche Nutzungszeit","Durée d’utilisation quotidienne","Tiempo de uso diario","Tempo di utilizzo giornaliero","Tempo de uso diário","Dagelijkse gebruikstijd","مدة الاستخدام اليومية","Denní doba používání","Napi használati idő","1日の使用時間","일일 사용 시간","Dzienny czas użytkowania","Ежедневное время использования","每日使用时长"],
    "Kategori Dağılımı": ["Category Distribution","Kategorienverteilung","Répartition des catégories","Distribución por categorías","Distribuzione per categoria","Distribuição por categoria","Categoriedistributie","توزيع الفئات","Rozložení kategorií","Kategóriaeloszlás","カテゴリ分布","카테고리 분포","Rozkład kategorii","Распределение категорий","类别分布"],
    "Kategori Bazında Günlük Dağılım": ["Daily Distribution by Category","Tägliche Verteilung nach Kategorie","Répartition quotidienne par catégorie","Distribución diaria por categoría","Distribuzione giornaliera per categoria","Distribuição diária por categoria","Dagelijkse verdeling per categorie","التوزيع اليومي حسب الفئة","Denní rozložení podle kategorií","Napi kategória szerinti megoszlás","カテゴリ別の1日の分布","카테고리별 일일 분포","Dzienny rozkład według kategorii","Ежедневное распределение по категориям","按类别的每日分布"],
    "En Çok Kullanılan Uygulamalar": ["Most Used Applications","Am häufigsten verwendete Anwendungen","Applications les plus utilisées","Aplicaciones más utilizadas","Applicazioni più utilizzate","Aplicativos mais usados","Meest gebruikte applicaties","التطبيقات الأكثر استخدامًا","Nejpoužívanější aplikace","Leggyakrabban használt alkalmazások","最も使用されたアプリ","가장 많이 사용한 앱","Najczęściej używane aplikacje","Самые используемые приложения","使用最多的应用"],
    "Kullanım Trendleri": ["Usage Trends","Nutzungstrends","Tendances d’utilisation","Tendencias de uso","Tendenze di utilizzo","Tendências de uso","Gebruikstrends","اتجاهات الاستخدام","Trendy používání","Használati trendek","使用傾向","사용 추세","Trendy użytkowania","Тенденции использования","使用趋势"],
    "Karşılaştırma": ["Comparison","Vergleich","Comparaison","Comparación","Confronto","Comparação","Vergelijking","مقارنة","Porovnání","Összehasonlítás","比較","비교","Porównanie","Сравнение","比较"],
    "Kullanım Takvimi": ["Usage Calendar","Nutzungskalender","Calendrier d’utilisation","Calendario de uso","Calendario di utilizzo","Calendário de uso","Gebruikskalender","تقويم الاستخدام","Kalendář používání","Használati naptár","使用カレンダー","사용 캘린더","Kalendarz użytkowania","Календарь использования","使用日历"],
    "Yerel dosya:": ["Local file:","Lokale Datei:","Fichier local :","Archivo local:","File locale:","Arquivo local:","Lokaal bestand:","الملف المحلي:","Lokální soubor:","Helyi fájl:","ローカルファイル：","로컬 파일:","Plik lokalny:","Локальный файл:","本地文件："],
    "Log:": ["Log:","Protokoll:","Journal :","Registro:","Log:","Log:","Logboek:","السجل:","Protokol:","Napló:","ログ：","로그:","Dziennik:","Журнал:","日志："],
    "MOLA": ["BREAKS","PAUSEN","PAUSES","PAUSAS","PAUSE","PAUSAS","PAUZES","الاستراحات","PAUZY","SZÜNETEK","休憩","휴식","PRZERWY","ПЕРЕРЫВЫ","休息"],
    "Boşta kalma süresi (dakika)": ["Idle time (minutes)","Leerlaufzeit (Minuten)","Temps d’inactivité (minutes)","Tiempo de inactividad (minutos)","Tempo di inattività (minuti)","Tempo de inatividade (minutos)","Inactieve tijd (minuten)","مدة الخمول (بالدقائق)","Doba nečinnosti (minuty)","Tétlenségi idő (perc)","アイドル時間（分）","유휴 시간 (분)","Czas bezczynności (minuty)","Время простоя (минуты)","空闲时间（分钟）"],
    "Mola hatırlatıcısı (dakika)": ["Break reminder (minutes)","Pausenerinnerung (Minuten)","Rappel de pause (minutes)","Recordatorio de pausa (minutos)","Promemoria pausa (minuti)","Lembrete de pausa (minutos)","Pauzeherinnering (minuten)","تذكير بالاستراحة (بالدقائق)","Připomínka pauzy (minuty)","Szünetemlékeztető (perc)","休憩リマインダー（分）","휴식 알림 (분)","Przypomnienie o przerwie (minuty)","Напоминание о перерыве (минуты)","休息提醒（分钟）"],
    "Mola hatırlatıcısını etkinleştir": ["Enable break reminder","Pausenerinnerung aktivieren","Activer le rappel de pause","Activar recordatorio de pausa","Attiva promemoria pausa","Ativar lembrete de pausa","Pauzeherinnering inschakelen","تفعيل تذكير الاستراحة","Povolit připomínku pauzy","Szünetemlékeztető engedélyezése","休憩リマインダーを有効にする","휴식 알림 활성화","Włącz przypomnienie o przerwie","Включить напоминание о перерыве","启用休息提醒"],
    "Sesli uyarı": ["Sound alert","Tonwarnung","Alerte sonore","Alerta sonora","Avviso sonoro","Alerta sonora","Geluidswaarschuwing","تنبيه صوتي","Zvukové upozornění","Hangjelzés","サウンド通知","소리 알림","Alert dźwiękowy","Звуковое уведомление","声音提醒"],
    "Web takibi (yalnızca pencere başlığı; URL kaydı yok)": ["Web tracking (window title only; no URL logging)","Web-Tracking (nur Fenstertitel; keine URL-Aufzeichnung)","Suivi web (titre de fenêtre uniquement ; aucune URL enregistrée)","Seguimiento web (solo título de ventana; sin registro de URL)","Monitoraggio web (solo titolo finestra; nessun URL registrato)","Rastreamento web (apenas título da janela; sem registro de URL)","Webtracking (alleen venstertitel; geen URL-registratie)","تتبع الويب (عنوان النافذة فقط؛ دون تسجيل عناوين URL)","Sledování webu (pouze název okna; bez záznamu URL)","Webkövetés (csak ablakcím; nincs URL-naplózás)","Web追跡（ウィンドウタイトルのみ、URL記録なし）","웹 추적 (창 제목만, URL 기록 없음)","Śledzenie sieci (tylko tytuł okna; brak zapisu URL)","Отслеживание веб-сайтов (только заголовок окна; URL не сохраняются)","网页跟踪（仅窗口标题；不记录URL）"],
    "Günlük aktif kullanım hedefi (saat)": ["Daily active usage goal (hours)","Tägliches aktives Nutzungsziel (Stunden)","Objectif d’utilisation active quotidienne (heures)","Objetivo diario de uso activo (horas)","Obiettivo di utilizzo attivo giornaliero (ore)","Meta diária de uso ativo (horas)","Dagelijks doel voor actief gebruik (uren)","هدف الاستخدام النشط اليومي (بالساعات)","Denní cíl aktivního používání (hodiny)","Napi aktív használati cél (óra)","1日のアクティブ使用目標（時間）","일일 활성 사용 목표 (시간)","Dzienny cel aktywnego użycia (godziny)","Дневная цель активного использования (часы)","每日主动使用目标（小时）"],
    "Örn. Eğitim, Sosyal Medya, Tasarım...": ["E.g. Education, Social Media, Design...","Z. B. Bildung, soziale Medien, Design...","Ex. Éducation, réseaux sociaux, design...","Ej. Educación, redes sociales, diseño...","Es. Istruzione, social media, design...","Ex. Educação, redes sociais, design...","Bijv. Onderwijs, sociale media, design...","مثال: التعليم، وسائل التواصل الاجتماعي، التصميم...","Např. Vzdělávání, sociální sítě, design...","Pl. Oktatás, közösségi média, dizájn...","例：教育、SNS、デザイン…","예: 교육, 소셜 미디어, 디자인...","Np. Edukacja, media społecznościowe, design...","Напр. Образование, соцсети, дизайн...","例如：教育、社交媒体、设计……"],
    "Kategori, uygulama sınıflandırmasında kullanılabilir.": ["The category can be used to classify applications.","Die Kategorie kann zur Klassifizierung von Anwendungen verwendet werden.","La catégorie peut servir à classer les applications.","La categoría puede usarse para clasificar aplicaciones.","La categoria può essere usata per classificare le applicazioni.","A categoria pode ser usada para classificar aplicativos.","De categorie kan worden gebruikt om applicaties te classificeren.","يمكن استخدام الفئة لتصنيف التطبيقات.","Kategorii lze použít ke klasifikaci aplikací.","A kategória alkalmazások osztályozására használható.","カテゴリはアプリの分類に使用できます。","카테고리를 앱 분류에 사용할 수 있습니다.","Kategoria może służyć do klasyfikowania aplikacji.","Категорию можно использовать для классификации приложений.","该类别可用于应用分类。"],
    "＋ Yeni Kategori": ["＋ New Category","＋ Neue Kategorie","＋ Nouvelle catégorie","＋ Nueva categoría","＋ Nuova categoria","＋ Nova categoria","＋ Nieuwe categorie","＋ فئة جديدة","＋ Nová kategorie","＋ Új kategória","＋ 新しいカテゴリ","＋ 새 카테고리","＋ Nowa kategoria","＋ Новая категория","＋ 新类别"],
}
_CODES = ["en","de","fr","es","it","pt","nl","ar","cs","hu","ja","ko","pl","ru","zh-CN"]
for _k, _vals in _COMPLETE_UI_TRANSLATIONS.items():
    _d = dict(zip(_CODES, _vals))
    TRANSLATIONS.setdefault(_k, {}).update(_d)
    TRANS_8.setdefault(_k, {}).update({c:v for c,v in _d.items() if c in {"ar","cs","hu","ja","ko","pl","ru","zh-CN"}})



_MORE_UI = {
    "Kısa bir mola vermen iyi olabilir.": ["A short break may be a good idea.","Eine kurze Pause könnte gut sein.","Une courte pause pourrait être une bonne idée.","Un breve descanso puede ser una buena idea.","Una breve pausa potrebbe essere una buona idea.","Uma breve pausa pode ser uma boa ideia.","Een korte pauze kan een goed idee zijn.","قد تكون استراحة قصيرة فكرة جيدة.","Krátká pauza může být dobrý nápad.","Egy rövid szünet jó ötlet lehet.","少し休憩するとよいでしょう。","잠시 쉬는 것이 좋습니다.","Krótka przerwa może być dobrym pomysłem.","Небольшой перерыв может быть хорошей идеей.","休息一下可能是个好主意。"],
    "10 dk ertele": ["Snooze 10 min","10 Min. verschieben","Reporter de 10 min","Posponer 10 min","Posticipa di 10 min","Adiar 10 min","10 min uitstellen","تأجيل 10 دقائق","Odložit o 10 min","10 perc szundi","10分延期","10분 미루기","Odłóż 10 min","Отложить на 10 мин","延后10分钟"],
    "Tamam": ["OK","OK","OK","Aceptar","OK","OK","OK","موافق","OK","OK","OK","확인","OK","ОК","确定"],
    "dakikadır aktif kullanımdasın. Kısa bir mola iyi olabilir.": ["minutes of active use. A short break may be a good idea.","Minuten aktiv. Eine kurze Pause könnte gut sein.","minutes d’utilisation active. Une courte pause pourrait être une bonne idée.","minutos de uso activo. Un breve descanso puede ser una buena idea.","minuti di utilizzo attivo. Una breve pausa potrebbe essere una buona idea.","minutos de uso ativo. Uma breve pausa pode ser uma boa ideia.","minuten actief gebruik. Een korte pauze kan een goed idee zijn.","دقائق من الاستخدام النشط. قد تكون استراحة قصيرة فكرة جيدة.","minut aktivního používání. Krátká pauza může být dobrý nápad.","percnyi aktív használat. Egy rövid szünet jó ötlet lehet.","分間アクティブに使用しています。少し休憩するとよいでしょう。","분 동안 사용했습니다. 잠시 쉬는 것이 좋습니다.","minut aktywnego użycia. Krótka przerwa może być dobrym pomysłem.","минут активного использования. Небольшой перерыв может быть хорошей идеей.","分钟的主动使用时间。休息一下可能是个好主意。"],
}
for _k,_vals in _MORE_UI.items():
    _d=dict(zip(_CODES,_vals)); TRANSLATIONS.setdefault(_k,{}).update(_d); TRANS_8.setdefault(_k,{}).update({c:v for c,v in _d.items() if c in {"ar","cs","hu","ja","ko","pl","ru","zh-CN"}})


_RESET_UI = {
    "Devam etmek istediğinizden emin misiniz?": ["Are you sure you want to continue?","Möchten Sie wirklich fortfahren?","Êtes-vous sûr de vouloir continuer ?","¿Está seguro de que desea continuar?","Sei sicuro di voler continuare?","Tem certeza de que deseja continuar?","Weet je zeker dat je wilt doorgaan?","هل أنت متأكد من أنك تريد المتابعة؟","Opravdu chcete pokračovat?","Biztosan folytatja?","続行してもよろしいですか？","계속하시겠습니까?","Czy na pewno chcesz kontynuować?","Вы уверены, что хотите продолжить?","确定要继续吗？"],
    "Son Onay": ["Final Confirmation","Letzte Bestätigung","Confirmation finale","Confirmación final","Conferma finale","Confirmação final","Laatste bevestiging","التأكيد النهائي","Konečné potvrzení","Végső megerősítés","最終確認","최종 확인","Ostateczne potwierdzenie","Окончательное подтверждение","最终确认"],
    "Bu işlem geri alınamaz.": ["This action cannot be undone.","Dieser Vorgang kann nicht rückgängig gemacht werden.","Cette action est irréversible.","Esta acción no se puede deshacer.","Questa operazione non può essere annullata.","Esta ação não pode ser desfeita.","Deze actie kan niet ongedaan worden gemaakt.","لا يمكن التراجع عن هذا الإجراء.","Tuto akci nelze vrátit zpět.","Ez a művelet nem vonható vissza.","この操作は元に戻せません。","이 작업은 취소할 수 없습니다.","Tej operacji nie można cofnąć.","Это действие нельзя отменить.","此操作无法撤销。"],
    "Gerçekten devam etmek istiyor musunuz?": ["Do you really want to continue?","Möchten Sie wirklich fortfahren?","Voulez-vous vraiment continuer ?","¿Realmente desea continuar?","Vuoi davvero continuare?","Tem certeza de que deseja continuar?","Weet je zeker dat je wilt doorgaan?","هل تريد حقًا المتابعة؟","Opravdu chcete pokračovat?","Biztosan folytatja?","本当に続行しますか？","정말 계속하시겠습니까?","Czy na pewno chcesz kontynuować?","Вы действительно хотите продолжить?","确定要继续吗？"],
    "Sistemde tutulan TÜM kullanım verileri silinecek.": ["ALL stored usage data will be deleted.","ALLE gespeicherten Nutzungsdaten werden gelöscht.","TOUTES les données d’utilisation seront supprimées.","Se eliminarán TODOS los datos de uso almacenados.","TUTTI i dati di utilizzo memorizzati verranno eliminati.","TODOS os dados de uso armazenados serão excluídos.","ALLE opgeslagen gebruiksgegevens worden verwijderd.","سيتم حذف جميع بيانات الاستخدام المخزنة.","VŠECHNA uložená data používání budou smazána.","MINDEN tárolt használati adat törlődik.","保存されているすべての使用データが削除されます。","저장된 모든 사용 데이터가 삭제됩니다.","WSZYSTKIE zapisane dane użycia zostaną usunięte.","ВСЕ сохраненные данные использования будут удалены.","所有已存储的使用数据都将被删除。"],
    "Tüm kullanım geçmişini kalıcı olarak silmek istediğinizden emin misiniz?": ["Are you sure you want to permanently delete all usage history?","Möchten Sie wirklich den gesamten Nutzungsverlauf dauerhaft löschen?","Voulez-vous supprimer définitivement tout l’historique d’utilisation ?","¿Está seguro de que desea eliminar permanentemente todo el historial de uso?","Sei sicuro di voler eliminare definitivamente tutta la cronologia di utilizzo?","Tem certeza de que deseja excluir permanentemente todo o histórico de uso?","Weet je zeker dat je de volledige gebruiksgeschiedenis permanent wilt verwijderen?","هل أنت متأكد من أنك تريد حذف سجل الاستخدام بالكامل نهائيًا؟","Opravdu chcete trvale odstranit celou historii používání?","Biztosan véglegesen törli a teljes használati előzményt?","すべての使用履歴を完全に削除してもよろしいですか？","모든 사용 기록을 영구적으로 삭제하시겠습니까?","Czy na pewno chcesz trwale usunąć całą historię użycia?","Вы уверены, что хотите навсегда удалить всю историю использования?","确定要永久删除所有使用记录吗？"],
    "Kesintisiz bilgisayar kullanımı": ["Continuous computer use","Ununterbrochene Computernutzung","Utilisation continue de l’ordinateur","Uso continuo del ordenador","Uso continuo del computer","Uso contínuo do computador","Ononderbroken computergebruik","استخدام الكمبيوتر بشكل متواصل","Nepřetržité používání počítače","Folyamatos számítógép-használat","パソコンの連続使用","연속 컴퓨터 사용","Ciągłe korzystanie z komputera","Непрерывное использование компьютера","连续使用电脑"],
}
for _k,_vals in _RESET_UI.items():
    _d=dict(zip(_CODES,_vals)); TRANSLATIONS.setdefault(_k,{}).update(_d); TRANS_8.setdefault(_k,{}).update({c:v for c,v in _d.items() if c in {"ar","cs","hu","ja","ko","pl","ru","zh-CN"}})

def translate(text, language):
    if language == "Türkçe":
        return text
    code = LANGUAGE_CODES.get(language, "en")
    if text in TRANS_8 and code in TRANS_8[text]:
        return TRANS_8[text][code]
    return TRANSLATIONS.get(text, {}).get(code, text)

TRANSLATIONS.update({
    "Uygulama":{"en":"Application","de":"Anwendung","fr":"Application","es":"Aplicación","it":"Applicazione","pt":"Aplicação","nl":"Applicatie"},
    "Oturum: ":{"en":"Session: ","de":"Sitzung: ","fr":"Session : ","es":"Sesión: ","it":"Sessione: ","pt":"Sessão: ","nl":"Sessie: "},
    "En uzun: ":{"en":"Longest: ","de":"Längste: ","fr":"Plus longue : ","es":"Más larga: ","it":"Più lunga: ","pt":"Mais longa: ","nl":"Langste: "},
    "Kategori değiştir":{"en":"Change category","de":"Kategorie ändern","fr":"Changer de catégorie","es":"Cambiar categoría","it":"Cambia categoria","pt":"Alterar categoria","nl":"Categorie wijzigen"},
    "kategorisi":{"en":"category","de":"Kategorie","fr":"catégorie","es":"categoría","it":"categoria","pt":"categoria","nl":"categorie"},
    "Bugünkü molalar: ":{"en":"Today's breaks: ","de":"Pausen heute: ","fr":"Pauses aujourd’hui : ","es":"Pausas de hoy: ","it":"Pause di oggi: ","pt":"Pausas de hoje: ","nl":"Pauzes vandaag: "},
    "Aylık Günlük Kullanım":{"en":"Monthly Daily Usage","de":"Tägliche Monatsnutzung","fr":"Utilisation quotidienne mensuelle","es":"Uso diario mensual","it":"Utilizzo giornaliero mensile","pt":"Uso diário mensal","nl":"Maandelijks dagelijks gebruik"},
    "Günlük Saatlik Kullanım":{"en":"Daily Hourly Usage","de":"Stündliche Tagesnutzung","fr":"Utilisation horaire quotidienne","es":"Uso por hora diario","it":"Utilizzo orario giornaliero","pt":"Uso horário diário","nl":"Dagelijks gebruik per uur"},
    "Aylık":{"en":"Monthly","de":"Monatlich","fr":"Mensuel","es":"Mensual","it":"Mensile","pt":"Mensal","nl":"Maandelijks"},
    "Uygulama":{"en":"Application","de":"Anwendung","fr":"Application","es":"Aplicación","it":"Applicazione","pt":"Aplicação","nl":"Applicatie"},
    "Günlük veriye tıklayarak ayrıntıları görebilirsin":{"en":"Click a day to see details","de":"Klicke auf einen Tag für Details","fr":"Cliquez sur un jour pour voir les détails","es":"Haz clic en un día para ver los detalles","it":"Fai clic su un giorno per i dettagli","pt":"Clique num dia para ver os detalhes","nl":"Klik op een dag voor details"},
    "Pay":{"en":"Share","de":"Anteil","fr":"Part","es":"Porcentaje","it":"Quota","pt":"Participação","nl":"Aandeel"},
})

# ============================================================
# DASHBOARD / UI
# ============================================================

# ============================================================
# MODERN DASHBOARD METİNLERİ
# ============================================================
TRANSLATIONS.update({
    "Merhaba!": {"en":"Hello!","de":"Hallo!","fr":"Bonjour !","es":"¡Hola!","it":"Ciao!","pt":"Olá!","nl":"Hallo!"},
    "Bugünkü dijital dengen nasıl?": {
        "en":"How is your digital balance today?","de":"Wie ist dein digitales Gleichgewicht heute?",
        "fr":"Comment va ton équilibre numérique aujourd’hui ?","es":"¿Cómo está tu equilibrio digital hoy?",
        "it":"Com’è il tuo equilibrio digitale oggi?","pt":"Como está o teu equilíbrio digital hoje?",
        "nl":"Hoe is je digitale balans vandaag?"
    },
    "Daha iyi bir sen mümkün.": {
        "en":"A better you is possible.","de":"Ein besseres Ich ist möglich.",
        "fr":"Une meilleure version de toi est possible.","es":"Una mejor versión de ti es posible.",
        "it":"Una versione migliore di te è possibile.","pt":"Uma versão melhor de ti é possível.",
        "nl":"Een betere versie van jezelf is mogelijk."
    },
    "Devam et, iyi gidiyorsun.": {
        "en":"Keep going, you're doing great.","de":"Mach weiter, du bist auf einem guten Weg.",
        "fr":"Continue, tu es sur la bonne voie.","es":"Sigue así, vas por buen camino.",
        "it":"Continua così, stai andando alla grande.","pt":"Continua, estás a ir muito bem.",
        "nl":"Ga zo door, je doet het goed."
    },
    "Bugünkü kullanım": {"en":"Today's usage","de":"Nutzung heute","fr":"Utilisation aujourd’hui","es":"Uso de hoy","it":"Utilizzo di oggi","pt":"Uso de hoje","nl":"Gebruik vandaag"},
    "En uzun oturum": {"en":"Longest session","de":"Längste Sitzung","fr":"Session la plus longue","es":"Sesión más larga","it":"Sessione più lunga","pt":"Sessão mais longa","nl":"Langste sessie"},
    "Verilen mola sayısı": {"en":"Breaks taken","de":"Pausen genommen","fr":"Pauses prises","es":"Descansos realizados","it":"Pause effettuate","pt":"Pausas feitas","nl":"Genomen pauzes"},
    "Denge Skoru": {"en":"Balance Score","de":"Balance-Score","fr":"Score d’équilibre","es":"Puntuación de equilibrio","it":"Punteggio equilibrio","pt":"Pontuação de equilíbrio","nl":"Balansscore"},
    "düne göre": {"en":"vs. yesterday","de":"vs. gestern","fr":"vs. hier","es":"vs. ayer","it":"rispetto a ieri","pt":"vs. ontem","nl":"vs. gisteren"},
    "rekor": {"en":"record","de":"Rekord","fr":"record","es":"récord","it":"record","pt":"recorde","nl":"record"},
    "kayıtlı mola": {"en":"breaks recorded","de":"Pausen erfasst","fr":"pauses enregistrées","es":"descansos registrados","it":"pause registrate","pt":"pausas registadas","nl":"pauzes geregistreerd"},
    "Kullanım Dağılımı (Bugün)": {"en":"Usage Distribution (Today)","de":"Nutzungsverteilung (Heute)","fr":"Répartition de l’utilisation (Aujourd’hui)","es":"Distribución de uso (Hoy)","it":"Distribuzione dell’uso (Oggi)","pt":"Distribuição de uso (Hoje)","nl":"Gebruiksverdeling (Vandaag)"},
    "Son 7 Gün": {"en":"Last 7 Days","de":"Letzte 7 Tage","fr":"7 derniers jours","es":"Últimos 7 días","it":"Ultimi 7 giorni","pt":"Últimos 7 dias","nl":"Laatste 7 dagen"},
    "Tümünü Gör": {"en":"View all","de":"Alle anzeigen","fr":"Tout voir","es":"Ver todo","it":"Vedi tutto","pt":"Ver tudo","nl":"Alles bekijken"},
    "Harika gidiyorsun!": {"en":"You're doing great!","de":"Du machst das großartig!","fr":"Tu fais du bon travail !","es":"¡Lo estás haciendo genial!","it":"Stai andando alla grande!","pt":"Estás a ir muito bem!","nl":"Je doet het geweldig!"},
    "Günlük hedef": {"en":"Daily goal","de":"Tagesziel","fr":"Objectif quotidien","es":"Objetivo diario","it":"Obiettivo giornaliero","pt":"Meta diária","nl":"Dagelijks doel"},
    "AFK": {"en":"AFK","de":"AFK","fr":"AFK","es":"AFK","it":"AFK","pt":"AFK","nl":"AFK"},
})


# Additional translations for the modern statistics dashboard.
STAT_TRANSLATIONS = {'Detaylı kullanım analizi': {'English': 'Detailed usage analysis', 'Deutsch': 'Detaillierte Nutzungsanalyse', 'Français': 'Analyse détaillée de l’utilisation', 'Español': 'Análisis detallado de uso', 'Italiano': 'Analisi dettagliata dell’utilizzo', 'Português': 'Análise detalhada de uso', 'Nederlands': 'Gedetailleerde gebruiksanalyse'}, 'Analiz dönemi': {'English': 'Analysis period', 'Deutsch': 'Analysezeitraum', 'Français': 'Période d’analyse', 'Español': 'Periodo de análisis', 'Italiano': 'Periodo di analisi', 'Português': 'Período de análise', 'Nederlands': 'Analyseperiode'}, 'Veri yok': {'English': 'No data', 'Deutsch': 'Keine Daten', 'Français': 'Aucune donnée', 'Español': 'Sin datos', 'Italiano': 'Nessun dato', 'Português': 'Sem dados', 'Nederlands': 'Geen gegevens'}, 'Aktif gün': {'English': 'active days', 'Deutsch': 'aktive Tage', 'Français': 'jours actifs', 'Español': 'días activos', 'Italiano': 'giorni attivi', 'Português': 'dias ativos', 'Nederlands': 'actieve dagen'}, 'Günlük Ortalama': {'English': 'Daily Average', 'Deutsch': 'Tagesdurchschnitt', 'Français': 'Moyenne quotidienne', 'Español': 'Promedio diario', 'Italiano': 'Media giornaliera', 'Português': 'Média diária', 'Nederlands': 'Dagelijks gemiddelde'}, 'En Verimli Gün': {'English': 'Most Active Day', 'Deutsch': 'Aktivster Tag', 'Français': 'Jour le plus actif', 'Español': 'Día más activo', 'Italiano': 'Giorno più attivo', 'Português': 'Dia mais ativo', 'Nederlands': 'Actiefste dag'}, 'En Yoğun Kategori': {'English': 'Top Category', 'Deutsch': 'Top-Kategorie', 'Français': 'Catégorie principale', 'Español': 'Categoría principal', 'Italiano': 'Categoria principale', 'Português': 'Categoria principal', 'Nederlands': 'Topcategorie'}, 'Günlük Kullanım Süresi': {'English': 'Daily Usage Time', 'Deutsch': 'Tägliche Nutzungszeit', 'Français': 'Temps d’utilisation quotidien', 'Español': 'Tiempo de uso diario', 'Italiano': 'Tempo di utilizzo giornaliero', 'Português': 'Tempo de uso diário', 'Nederlands': 'Dagelijkse gebruikstijd'}, 'Kategori Dağılımı': {'English': 'Category Distribution', 'Deutsch': 'Kategorienverteilung', 'Français': 'Répartition par catégorie', 'Español': 'Distribución por categoría', 'Italiano': 'Distribuzione per categoria', 'Português': 'Distribuição por categoria', 'Nederlands': 'Categorieverdeling'}, 'Kategori Bazında Günlük Dağılım': {'English': 'Daily Category Breakdown', 'Deutsch': 'Tägliche Kategorienverteilung', 'Français': 'Répartition quotidienne par catégorie', 'Español': 'Distribución diaria por categoría', 'Italiano': 'Distribuzione giornaliera per categoria', 'Português': 'Distribuição diária por categoria', 'Nederlands': 'Dagelijkse categorieverdeling'}, 'En Çok Kullanılan Uygulamalar': {'English': 'Most Used Applications', 'Deutsch': 'Meistgenutzte Anwendungen', 'Français': 'Applications les plus utilisées', 'Español': 'Aplicaciones más utilizadas', 'Italiano': 'Applicazioni più utilizzate', 'Português': 'Aplicações mais usadas', 'Nederlands': 'Meest gebruikte apps'}, 'Kullanım Trendleri': {'English': 'Usage Trends', 'Deutsch': 'Nutzungstrends', 'Français': 'Tendances d’utilisation', 'Español': 'Tendencias de uso', 'Italiano': 'Tendenze di utilizzo', 'Português': 'Tendências de uso', 'Nederlands': 'Gebruikstrends'}, 'Karşılaştırma': {'English': 'Comparison', 'Deutsch': 'Vergleich', 'Français': 'Comparaison', 'Español': 'Comparación', 'Italiano': 'Confronto', 'Português': 'Comparação', 'Nederlands': 'Vergelijking'}, 'Seçilen Gün': {'English': 'Selected Day', 'Deutsch': 'Ausgewählter Tag', 'Français': 'Jour sélectionné', 'Español': 'Día seleccionado', 'Italiano': 'Giorno selezionato', 'Português': 'Dia selecionado', 'Nederlands': 'Geselecteerde dag'}, 'Kullanım Takvimi': {'English': 'Usage Calendar', 'Deutsch': 'Nutzungskalender', 'Français': 'Calendrier d’utilisation', 'Español': 'Calendario de uso', 'Italiano': 'Calendario di utilizzo', 'Português': 'Calendário de uso', 'Nederlands': 'Gebruikskalender'}, 'Bu ayın günlük aktif kullanım süresi': {'English': 'Daily active usage for this month', 'Deutsch': 'Tägliche aktive Nutzung in diesem Monat', 'Français': 'Utilisation active quotidienne ce mois-ci', 'Español': 'Uso activo diario de este mes', 'Italiano': 'Utilizzo attivo giornaliero di questo mese', 'Português': 'Uso ativo diário deste mês', 'Nederlands': 'Dagelijks actief gebruik deze maand'}, 'Toplam kullanımın kategori bazında dağılımı': {'English': 'Category breakdown of total usage', 'Deutsch': 'Verteilung der Gesamtnutzung nach Kategorien', 'Français': 'Répartition de l’utilisation totale par catégorie', 'Español': 'Distribución del uso total por categoría', 'Italiano': 'Distribuzione dell’utilizzo totale per categoria', 'Português': 'Distribuição do uso total por categoria', 'Nederlands': 'Categorieverdeling van totaalgebruik'}, 'Günlük kullanımın kategorilere göre dağılımı': {'English': 'Daily usage split by category', 'Deutsch': 'Tägliche Nutzung nach Kategorien', 'Français': 'Utilisation quotidienne par catégorie', 'Español': 'Uso diario por categoría', 'Italiano': 'Utilizzo giornaliero per categoria', 'Português': 'Uso diário por categoria', 'Nederlands': 'Dagelijks gebruik per categorie'}, 'Bu ay en fazla kullanılan uygulamalar': {'English': 'Most used applications this month', 'Deutsch': 'Meistgenutzte Anwendungen in diesem Monat', 'Français': 'Applications les plus utilisées ce mois-ci', 'Español': 'Aplicaciones más utilizadas este mes', 'Italiano': 'Applicazioni più utilizzate questo mese', 'Português': 'Aplicações mais usadas este mês', 'Nederlands': 'Meest gebruikte apps deze maand'}, 'Son 7 günün kullanım eğilimi': {'English': 'Usage trend over the last 7 days', 'Deutsch': 'Nutzungstrend der letzten 7 Tage', 'Français': 'Tendance d’utilisation des 7 derniers jours', 'Español': 'Tendencia de uso de los últimos 7 días', 'Italiano': 'Tendenza d’uso degli ultimi 7 giorni', 'Português': 'Tendência de uso dos últimos 7 dias', 'Nederlands': 'Gebruikstrend van de afgelopen 7 dagen'}, 'Bu hafta ve önceki haftanın özeti': {'English': 'This week vs previous week', 'Deutsch': 'Diese Woche im Vergleich zur Vorwoche', 'Français': 'Cette semaine vs semaine précédente', 'Español': 'Esta semana vs semana anterior', 'Italiano': 'Questa settimana vs settimana precedente', 'Português': 'Esta semana vs semana anterior', 'Nederlands': 'Deze week vs vorige week'}, 'Son 7 gün ile önceki 7 gün karşılaştırması.': {'English': 'Comparison of the latest 7 days with the previous 7 days.', 'Deutsch': 'Vergleich der letzten 7 Tage mit den 7 Tagen davor.', 'Français': 'Comparaison des 7 derniers jours avec les 7 jours précédents.', 'Español': 'Comparación de los últimos 7 días con los 7 días anteriores.', 'Italiano': 'Confronto degli ultimi 7 giorni con i 7 giorni precedenti.', 'Português': 'Comparação dos últimos 7 dias com os 7 dias anteriores.', 'Nederlands': 'Vergelijking van de laatste 7 dagen met de 7 dagen ervoor.'}, 'Geçen hafta': {'English': 'Last week', 'Deutsch': 'Letzte Woche', 'Français': 'Semaine dernière', 'Español': 'Semana pasada', 'Italiano': 'Settimana scorsa', 'Português': 'Semana passada', 'Nederlands': 'Vorige week'}, 'Mola Sayısı': {'English': 'Break Count', 'Deutsch': 'Pausenanzahl', 'Français': 'Nombre de pauses', 'Español': 'Número de pausas', 'Italiano': 'Numero di pause', 'Português': 'Número de pausas', 'Nederlands': 'Aantal pauzes'}, 'En Uzun Gün': {'English': 'Longest Day', 'Deutsch': 'Längster Tag', 'Français': 'Journée la plus longue', 'Español': 'Día más largo', 'Italiano': 'Giorno più lungo', 'Português': 'Dia mais longo', 'Nederlands': 'Langste dag'}}
for _key, _langs in STAT_TRANSLATIONS.items():
    TRANSLATIONS.setdefault(_key, {}).update(_normalize_lang_keys(_langs))


# ============================================================
# WINDOWS UYGULAMA İKONLARI
# ============================================================
_APP_ICON_CACHE = {}


def _resolve_app_exe(app_name):
    name = (app_name or "").strip()
    if not name:
        return None
    key = name.lower()
    cached = _APP_ICON_CACHE.get(("path", key))
    if cached:
        return cached
    candidates = [name] if name.lower().endswith(".exe") else [name, name + ".exe"]
    for candidate in candidates:
        if os.path.isfile(candidate):
            _APP_ICON_CACHE[("path", key)] = candidate
            return candidate
        found = shutil.which(candidate)
        if found:
            _APP_ICON_CACHE[("path", key)] = found
            return found
    exe_name=candidates[-1]
    registry_paths=[
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"),
    ]
    for root, reg_path in registry_paths:
        for access in (winreg.KEY_READ, winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, reg_path, 0, access) as key:
                    value,_=winreg.QueryValueEx(key,None)
                    if value and os.path.isfile(value):
                        _APP_ICON_CACHE[("path",key)] = value
                        return value
            except OSError:
                pass
    local=os.environ.get("LOCALAPPDATA","")
    pf=os.environ.get("PROGRAMFILES","")
    common={
        "chrome.exe":[os.path.join(local,"Google","Chrome","Application","chrome.exe")],
        "msedge.exe":[os.path.join(pf,"Microsoft","Edge","Application","msedge.exe")],
        "code.exe":[os.path.join(local,"Programs","Microsoft VS Code","Code.exe"),os.path.join(pf,"Microsoft VS Code","Code.exe")],
        "spotify.exe":[os.path.join(local,"Spotify","Spotify.exe")],
        "discord.exe":[os.path.join(local,"Discord","Update.exe")],
        "python.exe":[os.path.join(local,"Programs","Python","Python311","python.exe")],
    }
    for candidate in common.get(key,[]):
        if candidate and os.path.isfile(candidate):
            _APP_ICON_CACHE[("path",key)]=candidate
            return candidate
    try:
        result=subprocess.run(["where",exe_name],capture_output=True,text=True,timeout=1,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        for line in result.stdout.splitlines():
            line=line.strip()
            if os.path.isfile(line):
                _APP_ICON_CACHE[("path",key)]=line
                return line
    except Exception:
        pass
    return None


def _extract_windows_icon(exe_path,size=44):
    if not exe_path or not os.path.isfile(exe_path): return None
    # GDI handles are a limited OS resource; every one obtained below must be
    # released on every code path (success, early-return, or exception) or
    # long-running sessions slowly leak them. Track what was actually
    # allocated and clean it all up in a single `finally` block.
    shell32=ctypes.windll.shell32; u32=ctypes.windll.user32; gdi32=ctypes.windll.gdi32
    hdc = dc = bmp = h_icon = None
    old = None
    try:
        class SHFILEINFOW(ctypes.Structure):
            _fields_=[("hIcon",ctypes.c_void_p),("iIcon",ctypes.c_int),("dwAttributes",ctypes.c_uint32),("szDisplayName",ctypes.c_wchar*260),("szTypeName",ctypes.c_wchar*80)]
        sfi=SHFILEINFOW()
        if not shell32.SHGetFileInfoW(exe_path,0,ctypes.byref(sfi),ctypes.sizeof(sfi),0x100) or not sfi.hIcon: return None
        h_icon = sfi.hIcon
        class BIH(ctypes.Structure):
            _fields_=[("biSize",ctypes.c_uint32),("biWidth",ctypes.c_int32),("biHeight",ctypes.c_int32),("biPlanes",ctypes.c_uint16),("biBitCount",ctypes.c_uint16),("biCompression",ctypes.c_uint32),("biSizeImage",ctypes.c_uint32),("biXPelsPerMeter",ctypes.c_int32),("biYPelsPerMeter",ctypes.c_int32),("biClrUsed",ctypes.c_uint32),("biClrImportant",ctypes.c_uint32)]
        class BI(ctypes.Structure): _fields_=[("bmiHeader",BIH),("bmiColors",ctypes.c_uint32*3)]
        hdc=u32.GetDC(0); dc=gdi32.CreateCompatibleDC(hdc); bmi=BI(); bmi.bmiHeader.biSize=ctypes.sizeof(BIH); bmi.bmiHeader.biWidth=size; bmi.bmiHeader.biHeight=-size; bmi.bmiHeader.biPlanes=1; bmi.bmiHeader.biBitCount=32
        bits=ctypes.c_void_p(); bmp=gdi32.CreateDIBSection(dc,ctypes.byref(bmi),0,ctypes.byref(bits),None,0)
        if not bmp or not bits.value:
            return None
        old=gdi32.SelectObject(dc,bmp); gdi32.PatBlt(dc,0,0,size,size,0x00FF0062); u32.DrawIconEx(dc,0,0,h_icon,size,size,0,0,0x0003)
        raw=ctypes.string_at(bits.value,size*size*4); img=Image.frombuffer("RGBA",(size,size),raw,"raw","BGRA",0,1).copy()
        return img
    except Exception as e:
        log_error("Uygulama ikonu",e); return None
    finally:
        if dc and old is not None:
            gdi32.SelectObject(dc, old)
        if bmp:
            gdi32.DeleteObject(bmp)
        if dc:
            gdi32.DeleteDC(dc)
        if hdc:
            u32.ReleaseDC(0, hdc)
        if h_icon:
            u32.DestroyIcon(h_icon)


def _make_app_fallback_icon(app_name,size=44):
    img=Image.new("RGBA",(size,size),(30,41,59,255)); draw=ImageDraw.Draw(img)
    initials="".join(p[0] for p in re.split(r"[^A-Za-z0-9]+",app_name.replace(".exe","")) if p)[:2].upper() or app_name[:2].upper() or "A"
    palette=["#38bdf8","#8b5cf6","#22c55e","#f59e0b","#ec4899"]; accent=palette[sum(map(ord,initials))%len(palette)]
    draw.rounded_rectangle((1,1,size-2,size-2),radius=9,fill=accent)
    try: font=ImageFont.truetype(os.path.join(os.environ.get("WINDIR",r"C:\Windows"),"Fonts","segoeui.ttf"),max(12,size//2-2))
    except Exception: font=None
    box=draw.textbbox((0,0),initials,font=font); draw.text(((size-box[2]+box[0])/2,(size-box[3]+box[1])/2-1),initials,fill="white",font=font)
    return img


def get_app_icon_image(app_name,size=44):
    key=((app_name or "").lower(),size)
    if key in _APP_ICON_CACHE: return _APP_ICON_CACHE[key].copy()
    img=_extract_windows_icon(_resolve_app_exe(app_name),size)
    if img is None: img=_make_app_fallback_icon(app_name,size)
    _APP_ICON_CACHE[key]=img.copy(); return img


TRANSLATIONS.setdefault("uygulama", {}).update(_normalize_lang_keys({"English":"apps", "Deutsch":"Apps", "Français":"applications", "Español":"aplicaciones", "Italiano":"app", "Português":"aplicativos", "Nederlands":"apps"}))


# Modern typography: a modest global increase for readability.
MODERN_FONT_FAMILY = "Segoe UI"



# Windows başlangıç seçeneği için eksik çeviriler.
STARTUP_TRANSLATIONS = {
    "Windows ile başlat": {
        "English": "Start with Windows", "Deutsch": "Mit Windows starten",
        "Français": "Démarrer avec Windows", "Español": "Iniciar con Windows",
        "Italiano": "Avvia con Windows", "Português": "Iniciar com o Windows",
        "Nederlands": "Starten met Windows"
    },
    "Bilgisayar açıldığında Dijital Denge otomatik olarak çalışsın.": {
        "English": "Start Digital Balance automatically when Windows starts.",
        "Deutsch": "Digital Balance automatisch beim Start von Windows ausführen.",
        "Français": "Lancer automatiquement Digital Balance au démarrage de Windows.",
        "Español": "Iniciar Digital Balance automáticamente al arrancar Windows.",
        "Italiano": "Avvia automaticamente Digital Balance all'avvio di Windows.",
        "Português": "Iniciar o Digital Balance automaticamente quando o Windows iniciar.",
        "Nederlands": "Digital Balance automatisch starten wanneer Windows wordt gestart."
    }
}
for _key, _langs in STARTUP_TRANSLATIONS.items():
    TRANSLATIONS.setdefault(_key, {}).update(_normalize_lang_keys(_langs))


# Session-page translations
SESSION_TRANSLATIONS = {
    "Bugünkü Oturumlar": {"en":"Today’s Sessions","de":"Heutige Sitzungen","fr":"Sessions d’aujourd’hui","es":"Sesiones de hoy","it":"Sessioni di oggi","pt":"Sessões de hoje","nl":"Sessies van vandaag"},
    "Uygulamalar arasında geçen aktif kullanım sürelerini ve kesintisiz oturumları zaman çizelgesinde inceleyebilirsin.": {"en":"Review active usage between applications and continuous sessions on the timeline.","de":"Überprüfe aktive Nutzungszeiten zwischen Anwendungen und ununterbrochene Sitzungen in der Zeitleiste.","fr":"Consulte les temps d’utilisation active entre les applications et les sessions continues dans la chronologie.","es":"Consulta los tiempos de uso activo entre aplicaciones y las sesiones continuas en la línea de tiempo.","it":"Esamina i tempi di utilizzo attivo tra le applicazioni e le sessioni continue nella cronologia.","pt":"Veja os tempos de uso ativo entre aplicações e as sessões contínuas na linha do tempo.","nl":"Bekijk actieve gebruikstijden tussen apps en ononderbroken sessies in de tijdlijn."},
    "Toplam Kullanım": {"en":"Total Usage","de":"Gesamtnutzung","fr":"Utilisation totale","es":"Uso total","it":"Utilizzo totale","pt":"Uso total","nl":"Totaal gebruik"},
    "Oturum Sayısı": {"en":"Session Count","de":"Sitzungsanzahl","fr":"Nombre de sessions","es":"Número de sesiones","it":"Numero di sessioni","pt":"Número de sessões","nl":"Aantal sessies"},
    "En Uzun Oturum": {"en":"Longest Session","de":"Längste Sitzung","fr":"Session la plus longue","es":"Sesión más larga","it":"Sessione più lunga","pt":"Sessão mais longa","nl":"Langste sessie"},
    "Mola Sayısı": {"en":"Break Count","de":"Pausenanzahl","fr":"Nombre de pauses","es":"Número de pausas","it":"Numero di pause","pt":"Número de pausas","nl":"Aantal pauzes"},
    "Zaman Çizelgesi": {"en":"Timeline","de":"Zeitleiste","fr":"Chronologie","es":"Línea de tiempo","it":"Cronologia","pt":"Linha do tempo","nl":"Tijdlijn"},
    "kayıt": {"en":"records","de":"Einträge","fr":"enregistrements","es":"registros","it":"registrazioni","pt":"registros","nl":"registraties"},
    "Mola": {"en":"Break","de":"Pause","fr":"Pause","es":"Pausa","it":"Pausa","pt":"Pausa","nl":"Pauze"},
    "Dinlenme": {"en":"Rest","de":"Erholung","fr":"Repos","es":"Descanso","it":"Riposo","pt":"Descanso","nl":"Rust"},
    "Kesintisiz Bilgisayar Kullanımı": {"en":"Continuous Computer Use","de":"Ununterbrochene Computernutzung","fr":"Utilisation continue de l’ordinateur","es":"Uso continuo del ordenador","it":"Uso continuo del computer","pt":"Uso contínuo do computador","nl":"Ononderbroken computergebruik"},
    "Bugün henüz tamamlanmış bir oturum bulunmuyor.": {"en":"There are no completed sessions today yet.","de":"Heute gibt es noch keine abgeschlossenen Sitzungen.","fr":"Aucune session terminée aujourd’hui pour le moment.","es":"Todavía no hay sesiones completadas hoy.","it":"Oggi non ci sono ancora sessioni completate.","pt":"Ainda não há sessões concluídas hoje.","nl":"Er zijn vandaag nog geen voltooide sessies."},
    "En yeni kayıtlar üstte gösterilir.": {"en":"Newest records are shown first.","de":"Die neuesten Einträge werden zuerst angezeigt.","fr":"Les enregistrements les plus récents sont affichés en premier.","es":"Los registros más recientes aparecen primero.","it":"I record più recenti sono mostrati per primi.","pt":"Os registros mais recentes aparecem primeiro.","nl":"De nieuwste registraties worden bovenaan weergegeven."}
}
for _k, _v in SESSION_TRANSLATIONS.items():
    TRANSLATIONS.setdefault(_k, {}).update(_v)

class DashboardApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Dijital Denge")
        self.geometry("1240x800")
        self.minsize(1080, 700)

        # Uygulama simgesi
        icon_path = os.path.join(
            BASE_DIR,
            "assets",
            "dijital_denge_icon2.ico"
        )

        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception as e:
                print(f"Uygulama ikonu yüklenemedi: {e}")

        ctk.set_appearance_mode(get_settings().get("theme", "dark"))
        ctk.set_default_color_theme("blue")

        self.language = get_settings().get("language", "Türkçe")

        global CURRENT_LANGUAGE
        CURRENT_LANGUAGE = self.language

        self.protocol(
            "WM_DELETE_WINDOW",
            self.kucult_tepsiye
        )

        # Modern renk sistemi
        self.colors = {
            "accent": "#22d3ee",
            "accent2": "#38bdf8",
            "dark_bg": "#07111f",
            "dark_panel": "#0b1728",
            "dark_card": "#102033",
            "light_bg": "#e5e7eb",
            "light_panel": "#f1f3f5",
            "light_card": "#ffffff",
        }

        # Marka görselleri
        self.logo_icon = None
        self.logo_wordmark = None
        self._load_brand_assets()

        self.grid_columnconfigure(
            0,
            weight=0,
            minsize=255
        )

        self.grid_columnconfigure(
            1,
            weight=1
        )

        self.grid_rowconfigure(
            0,
            weight=1
        )

        # ---------------- SIDEBAR ----------------
        self.sidebar = ctk.CTkFrame(
            self, width=255, corner_radius=0,
            fg_color=("#d9dde2", self.colors["dark_bg"])
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        self.brand_frame = ctk.CTkFrame(
            self.sidebar, fg_color=("#d9dde2", self.colors["dark_bg"]), corner_radius=0
        )
        self.brand_frame.pack(fill="x", padx=18, pady=(22, 18))
        if self.logo_icon:
            ctk.CTkLabel(self.brand_frame, image=self.logo_icon, text="").pack(pady=(2, 6))
        self.brand_title = ctk.CTkLabel(
            self.brand_frame, text=translate("DİJİTAL DENGE", self.language), font=("Segoe UI", 21, "bold"),
            text_color=("#0f172a", "#f8fafc")
        )
        self.brand_title.pack()
        self.brand_subtitle = ctk.CTkLabel(
            self.brand_frame,
            text=translate("Daha bilinçli, daha sen", self.language),
            font=("Segoe UI", 11),
            text_color=("#64748b", "#94a3b8")
        )
        self.brand_subtitle.pack(pady=(1, 0))

        self.nav_label = ctk.CTkLabel(
            self.sidebar, text=translate("MENÜ", self.language),
            font=("Segoe UI", 12, "bold"), text_color=("#64748b", "#64748b")
        )
        self.nav_label.pack(fill="x", padx=25, pady=(3, 7))

        self.nav_buttons = {}
        self.nav_rows = {}
        self.nav_indicators = {}
        self.nav_icon_labels = {}
        self.nav_text_labels = {}
        self.nav_icon_badges = {}
        # Modern, Windows uyumlu çizgi ikonları.
        nav_items = [
            ("dashboard", "\ue80f", "Genel Bakış"),
            ("apps", "\ue71d", "Uygulamalar"),
            ("sessions", "\ue823", "Oturumlar"),
            ("stats", "\ue9d2", "İstatistikler"),
            ("settings", "\ue713", "Ayarlar"),
        ]
        for key, icon, text in nav_items:
            self.create_nav(key, icon, text)

        self.sidebar_spacer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.sidebar_spacer.pack(expand=True, fill="both")
        self.sidebar_bottom = ctk.CTkLabel(
            self.sidebar,
            text=translate("V3.0\nYerel veri • Gizlilik öncelikli", self.language),
            font=("Segoe UI", 11),
            text_color=("#64748b", "#64748b"), justify="left"
        )
        self.sidebar_bottom.pack(padx=25, pady=(0, 24), anchor="w")

        # ---------------- MAIN AREA ----------------
        self.main = ctk.CTkFrame(
            self, fg_color=(self.colors["light_panel"], self.colors["dark_panel"]), corner_radius=0
        )
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        self.header = ctk.CTkFrame(
            self.main, fg_color="transparent", corner_radius=0
        )
        self.header.grid(row=0, column=0, sticky="ew", padx=34, pady=(27, 12))
        self.header.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(self.header, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="w")
        self.page_accent = ctk.CTkFrame(
            title_row, width=5, height=38, corner_radius=3,
            fg_color=self.colors["accent"]
        )
        self.page_accent.pack(side="left", padx=(0, 12))
        self.page_title = ctk.CTkLabel(
            title_row, text=translate("Genel Bakış", self.language),
            font=("Segoe UI", 31, "bold"), text_color=("#0f172a", "#f8fafc")
        )
        self.page_title.pack(side="left")

        self.page_date = ctk.CTkLabel(
            self.header, text="", font=("Segoe UI", 14),
            text_color=("#64748b", "#94a3b8")
        )
        self.page_date.grid(row=1, column=0, sticky="w", padx=(17, 0), pady=(2, 0))

        self.refresh_btn = ctk.CTkButton(
            self.header, text="↻", width=45, height=42, corner_radius=12,
            fg_color=("#ffffff", "#16263b"), hover_color=("#e2e8f0", "#1e3855"),
            text_color=("#0f172a", "#e2e8f0"), font=("Segoe UI", 23),
            command=self.guncelle
        )
        self.refresh_btn.grid(row=0, column=1, rowspan=2, padx=(15, 0))

        # İçeriği ayrı host içinde tutarak sekmeler arası yumuşak yatay geçiş sağlıyoruz.
        self.content_host = ctk.CTkFrame(self.main, fg_color="transparent", corner_radius=0)
        self.content_host.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        self.content_host.grid_rowconfigure(0, weight=1)
        self.content_host.grid_columnconfigure(0, weight=1)
        self.content = ctk.CTkScrollableFrame(
            self.content_host,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_fg_color=("#d1d5db", "#0f2035"),
            scrollbar_button_color=("#94a3b8", "#38bdf8"),
            scrollbar_button_hover_color=("#64748b", "#67e8f9"),
            orientation="vertical",
        )

        self.content.grid(row=0, column=0, sticky="nsew")
        self._setup_content_scrolling()

        # Sekme cache: ilk oluşturulan ekranlar tekrar destroy edilmez.
        self._page_frames = {"dashboard": self.content}
        self._page_built = {"dashboard": False}
        self._dashboard_widgets = {}

        self.dash_widgets = {}
        self._warning_overlay = None
        self._warning_overlay_after_id = None
        self.current_page = "dashboard"
        self.show_dashboard()
        self.after(5000, self.auto_refresh)

    def _setup_content_scrolling(self):

        try:
            canvas = self.content._parent_canvas
            canvas.configure(yscrollincrement=24)

            def scroll(event):
                # Windows / macOS touchpad ve mouse wheel
                delta = getattr(event, "delta", 0)
                if delta:
                    units = max(1, abs(int(delta / 120)))
                    canvas.yview_scroll(-units if delta > 0 else units, "units")
                return "break"

            def scroll_up(event):
                canvas.yview_scroll(-3, "units")
                return "break"

            def scroll_down(event):
                canvas.yview_scroll(3, "units")
                return "break"


            try:
                if hasattr(self, "_wheel_bind_id"):
                    self.unbind_all("<MouseWheel>", self._wheel_bind_id)
                if hasattr(self, "_wheel_up_bind_id"):
                    self.unbind_all("<Button-4>", self._wheel_up_bind_id)
                if hasattr(self, "_wheel_down_bind_id"):
                    self.unbind_all("<Button-5>", self._wheel_down_bind_id)
            except Exception:
                pass


            canvas.bind("<MouseWheel>", scroll, add="+")
            canvas.bind("<Button-4>", scroll_up, add="+")
            canvas.bind("<Button-5>", scroll_down, add="+")

            self._wheel_bind_id = self.bind_all("<MouseWheel>", scroll, add="+")
            self._wheel_up_bind_id = self.bind_all("<Button-4>", scroll_up, add="+")
            self._wheel_down_bind_id = self.bind_all("<Button-5>", scroll_down, add="+")


            canvas.bind("<Shift-MouseWheel>", scroll, add="+")


            self.content.update_idletasks()
            canvas.update_idletasks()
        except Exception as e:
            log_error("İçerik kaydırma kurulumu", e)

    def _load_brand_assets(self):
        try:
            icon_path = os.path.join(BASE_DIR, "assets", "dijital_denge_icon2.png")
            wordmark_path = os.path.join(BASE_DIR, "assets", "dijital_denge_icon2.png")
            if os.path.exists(icon_path):
                self.logo_icon = ctk.CTkImage(
                    light_image=Image.open(icon_path), dark_image=Image.open(icon_path),
                    size=(72, 72)
                )
            if os.path.exists(wordmark_path):
                self.logo_wordmark = ctk.CTkImage(
                    light_image=Image.open(wordmark_path), dark_image=Image.open(wordmark_path),
                    size=(190, 134)
                )

            if os.path.exists(icon_path):
                self.iconphoto(True, tk.PhotoImage(file=icon_path))
        except Exception as e:
            log_error("Logo yükleme", e)

    def create_nav(self, key, icon, base):
        row = ctk.CTkFrame(self.sidebar, fg_color="transparent", corner_radius=14, height=56)
        row.pack(fill="x", padx=12, pady=4)
        row.pack_propagate(False)

        indicator = ctk.CTkFrame(row, width=4, height=34, corner_radius=3, fg_color="transparent")
        indicator.pack(side="left", padx=(2, 9))

        button = ctk.CTkButton(
            row, text="", height=52, corner_radius=13,
            fg_color="transparent", hover_color=("#e4e8ec", "#142b45"),
            border_width=0, command=lambda k=key: self.navigate(k)
        )
        button.pack(side="left", fill="both", expand=True)

        inner = ctk.CTkFrame(button, fg_color="transparent", corner_radius=12)
        inner.place(relx=0, rely=0, relwidth=1, relheight=1)

        icon_badge = ctk.CTkFrame(inner, width=38, height=38, corner_radius=10, fg_color="transparent")
        icon_badge.pack(side="left", padx=(9, 11), pady=7)
        icon_badge.pack_propagate(False)

        icon_label = ctk.CTkLabel(
            icon_badge, text=icon, font=("Segoe MDL2 Assets", 21),
            text_color=("#64748b", "#aebed0")
        )
        icon_label.pack(expand=True)

        text_label = ctk.CTkLabel(
            inner, text=translate(base, self.language),
            font=("Segoe UI", 16, "bold"),
            text_color=("#334155", "#cbd5e1"), anchor="w"
        )
        text_label.pack(side="left", fill="x", expand=True)

        for widget in (inner, icon_badge, icon_label, text_label):
            widget.bind("<Button-1>", lambda event, k=key: self.navigate(k))

        self.nav_buttons[key] = button
        self.nav_rows[key] = row
        self.nav_indicators[key] = indicator
        self.nav_icon_labels[key] = icon_label
        self.nav_text_labels[key] = text_label
        self.nav_icon_badges[key] = icon_badge

    def _set_nav_active(self, key, active):
        button = self.nav_buttons[key]
        row = self.nav_rows[key]
        icon_label = self.nav_icon_labels[key]
        text_label = self.nav_text_labels[key]
        badge = self.nav_icon_badges[key]
        if active:
            row.configure(fg_color=("#e7edf2", "#102943"))
            button.configure(fg_color=("#ffffff", "#142f4b"), hover_color=("#f3f6f8", "#183957"))
            badge.configure(fg_color=("#dff6fb", "#123d59"))
            icon_label.configure(text_color=("#0284c7", "#67e8f9"))
            text_label.configure(text_color=("#0f6fa8", "#f1f5f9"))
            self.nav_indicators[key].configure(fg_color=self.colors["accent"])
        else:
            row.configure(fg_color="transparent")
            button.configure(fg_color="transparent", hover_color=("#e4e8ec", "#142b45"))
            badge.configure(fg_color="transparent")
            icon_label.configure(text_color=("#64748b", "#aebed0"))
            text_label.configure(text_color=("#334155", "#cbd5e1"))
            self.nav_indicators[key].configure(fg_color="transparent")

    def _page_scrollable(self, page):
        """Sekme için bir kez oluşturulan scrollable container."""
        frame = ctk.CTkScrollableFrame(
            self.content_host, fg_color="transparent", corner_radius=0,
            scrollbar_fg_color=("#d1d5db", "#0f2035"),
            scrollbar_button_color=("#94a3b8", "#38bdf8"),
            scrollbar_button_hover_color=("#64748b", "#67e8f9"),
            orientation="vertical",
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self._page_frames[page] = frame
        self._page_built[page] = False
        return frame

    def _setup_page_scroll(self, frame):
        try:
            canvas = frame._parent_canvas
            canvas.configure(yscrollincrement=24)
        except Exception:
            pass

    def _activate_page(self, page):
        target = self._page_frames.get(page)
        if target is None:
            target = self._page_scrollable(page)
        for key, frame in self._page_frames.items():
            try:
                if key == page:
                    frame.grid()
                else:
                    frame.grid_remove()
            except Exception:
                pass
        self.content = target
        self.current_page = page
        self.dash_widgets = self._dashboard_widgets if page == "dashboard" else {}

        try:
            self._setup_content_scrolling()
        except Exception:
            pass
        return target

    def _begin_page_build(self, page):
        frame = self._page_frames.get(page)
        if frame is None:
            frame = self._page_scrollable(page)
        self._activate_page(page)
        self.content = frame
        self.clear_content()
        self._page_built[page] = True
        return frame

    def navigate(self, page):
        self.current_page = page
        titles = {"dashboard":"Genel Bakış", "apps":"Uygulamalar", "sessions":"Oturumlar", "stats":"İstatistikler", "settings":"Ayarlar"}
        self.page_title.configure(text=translate(titles[page], self.language))
        for key in self.nav_buttons:
            self._set_nav_active(key, key == page)


        if self._page_built.get(page, False):
            self._activate_page(page)
            return

        getattr(self, {"dashboard":"show_dashboard", "apps":"show_apps", "sessions":"show_sessions", "stats":"show_stats", "settings":"show_settings"}[page])()

    def clear_content(self):
        for w in self.content.winfo_children():
            w.destroy()
        self.dash_widgets = {}

    def create_card(self, parent, title, value, subtitle="", accent="#38bdf8", icon="◷", trend=""):

        card = ctk.CTkFrame(
            parent,
            fg_color=("#eef2f4", "#102033"),
            border_width=1,
            border_color=("#d6dde2", "#1b3349"),
            corner_radius=15
        )

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))

        icon_box = ctk.CTkFrame(
            top,
            width=42,
            height=42,
            corner_radius=12,
            fg_color=accent
        )
        icon_box.pack(side="left")
        icon_box.pack_propagate(False)

        ctk.CTkLabel(
            icon_box, text=icon,
            font=("Segoe UI Symbol", 22),
            text_color="#ffffff"
        ).place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            top,
            text=translate(title, self.language),
            font=("Segoe UI", 15),
            text_color=("#64748b", "#94a3b8")
        ).pack(side="left", padx=10)

        val = ctk.CTkLabel(
            card,
            text=value,
            font=("Segoe UI", 27, "bold"),
            text_color=("#0f172a", "#f1f5f9")
        )
        val.pack(anchor="w", padx=18, pady=(2, 0))

        if subtitle:
            sub_row = ctk.CTkFrame(card, fg_color="transparent")
            sub_row.pack(fill="x", padx=18, pady=(1, 12))

            if trend:
                ctk.CTkLabel(
                    sub_row, text=trend,
                    font=("Segoe UI", 12, "bold"),
                    text_color="#34d399"
                ).pack(side="left")

            ctk.CTkLabel(
                sub_row,
                text=translate(subtitle, self.language),
                font=("Segoe UI", 13),
                text_color=("#64748b", "#94a3b8")
            ).pack(side="left", padx=(5 if trend else 0, 0))

        return card, val, None

    def get_today_light(self):

        today = datetime.now().strftime("%Y-%m-%d")
        return today, get_day_snapshot(today)

    def get_today(self):

        today, day = self.get_today_light()
        return day, today, day

    def show_dashboard(self):
        self._begin_page_build("dashboard")
        self.page_title.configure(text=translate("Genel Bakış", self.language))

        now = datetime.now()
        self.page_date.configure(
            text=f"{self.localized_month_year(now)} • {translate('Bugünkü dijital dengen nasıl?', self.language)}"
        )

        today = datetime.now().strftime("%Y-%m-%d")
        day = get_day_snapshot(today)

        week_keys = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(8)]
        week_days = get_days_snapshot(week_keys)
        status = get_live_status()
        total = int(day.get("total_active_seconds", 0))
        longest = int(day.get("longest_continuous_seconds", 0))
        breaks = day.get("breaks", [])
        score = self.calculate_score(day)

        # ---------------- HERO ----------------
        hero = ctk.CTkFrame(self.content, fg_color="transparent")
        hero.pack(fill="x", padx=5, pady=(2, 10))

        hero_left = ctk.CTkFrame(hero, fg_color="transparent")
        hero_left.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            hero_left,
            text=translate("Merhaba!", self.language),
            font=("Segoe UI", 30, "bold"),
            text_color=("#0f172a", "#f8fafc")
        ).pack(anchor="w")

        ctk.CTkLabel(
            hero_left,
            text=translate("Bugünkü dijital dengen nasıl?", self.language),
            font=("Segoe UI", 15),
            text_color=("#64748b", "#94a3b8")
        ).pack(anchor="w", pady=(1, 0))

        hero_right = ctk.CTkFrame(hero, fg_color="transparent")
        hero_right.pack(side="right", anchor="e", pady=(6, 0))

        ctk.CTkLabel(
            hero_right, text="☀",
            font=("Segoe UI Symbol", 21),
            text_color="#fbbf24"
        ).pack(side="left", padx=(0, 7))

        ctk.CTkLabel(
            hero_right,
            text=translate("Daha iyi bir sen mümkün.", self.language),
            font=("Segoe UI", 15),
            text_color=("#64748b", "#94a3b8")
        ).pack(side="left")

        # ---------------- TOP CARDS ----------------
        cards = ctk.CTkFrame(self.content, fg_color="transparent")
        cards.pack(fill="x", padx=5, pady=(0, 10))
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1, uniform="dashboard_cards")

        yesterday = week_days.get(
            (now - timedelta(days=1)).strftime("%Y-%m-%d"), {}
        )
        yesterday_total = int(yesterday.get("total_active_seconds", 0))
        if yesterday_total:
            diff = ((total - yesterday_total) / yesterday_total) * 100
            trend = f"{'↓' if diff <= 0 else '↑'} %{abs(diff):.0f}"
        else:
            trend = ""

        card_specs = [
            (translate("Bugünkü kullanım", self.language), format_seconds(total), translate("AFK hariç", self.language), "#1598ed", "◷", trend),
            ("En uzun oturum", format_seconds(longest), "rekor", "#8b4de8", "⏱", ""),
            (translate("Verilen mola sayısı", self.language), str(len(breaks)), translate("kayıtlı mola", self.language), "#21b99a", "☕", ""),
            (translate("Denge Skoru", self.language), f"{score}/100", translate("düne göre", self.language), "#f39a2e", "▥", ""),
        ]

        vals = []
        for i, spec in enumerate(card_specs):
            card, val, _ = self.create_card(cards, *spec)
            card.grid(row=0, column=i, sticky="nsew", padx=4)
            vals.append(val)

        self.dash_widgets = {
            "total": vals[0],
            "longest": vals[1],
            "breaks": vals[2],
            "score": vals[3],
        }

        if status.get("warning_active"):
            banner = ctk.CTkFrame(
                self.content,
                fg_color=("#fee2e2", "#4c1d1d"),
                border_width=1,
                border_color=("#fecaca", "#7f1d1d"),
                corner_radius=12
            )
            banner.pack(fill="x", padx=5, pady=(0, 10))
            ctk.CTkLabel(
                banner,
                text=translate(
                    "⚠  2 saattir mola vermeden kullanıyorsun. Kısa bir mola ver.",
                    self.language
                ),
                font=("Segoe UI", 15, "bold"),
                text_color=("#b91c1c", "#fecaca")
            ).pack(padx=16, pady=10, anchor="w")

        # ---------------- TWO COLUMN BODY ----------------
        body = ctk.CTkFrame(self.content, fg_color="transparent")
        body.pack(fill="x", padx=5, pady=(0, 10))
        body.grid_columnconfigure(0, weight=1, uniform="dashboard_body")
        body.grid_columnconfigure(1, weight=1, uniform="dashboard_body")

        self.section_categories(body, total, day, column=0)
        self.section_week(body, week_days, column=1)

        # ---------------- DAILY GOAL / MOTIVATION ----------------
        self.section_goals(body, day, column=0)

        settings = get_settings()
        goal = int(settings.get("daily_goal_seconds", 6 * 3600))
        goal_ratio = min(1.0, total / goal) if goal else 0

        motivation = ctk.CTkFrame(
            body,
            fg_color=("#dbeaf4", "#17354d"),
            border_width=1,
            border_color=("#c7dce8", "#24516f"),
            corner_radius=15
        )
        motivation.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(8, 0))

        inner = ctk.CTkFrame(motivation, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=13)

        ctk.CTkLabel(
            inner,
            text="◢",
            font=("Segoe UI Symbol", 30),
            text_color="#22c55e"
        ).pack(side="left", padx=(0, 13))

        text_box = ctk.CTkFrame(inner, fg_color="transparent")
        text_box.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            text_box,
            text=translate("Harika gidiyorsun!", self.language),
            font=("Segoe UI", 15, "bold"),
            text_color=("#0f172a", "#f1f5f9")
        ).pack(anchor="w")

        ctk.CTkLabel(
            text_box,
            text=translate("Devam et, iyi gidiyorsun.", self.language),
            font=("Segoe UI", 13),
            text_color=("#64748b", "#a8bac8")
        ).pack(anchor="w", pady=(2, 6))

        goal_bar = ctk.CTkProgressBar(
            text_box, height=7,
            progress_color="#22c55e",
            fg_color=("#cbd5e1", "#27465c")
        )
        goal_bar.set(goal_ratio)
        goal_bar.pack(fill="x", pady=(0, 3))

        ctk.CTkLabel(
            text_box,
            text=f"{format_seconds(total)} / {format_seconds(goal)}",
            font=("Segoe UI", 12),
            text_color=("#64748b", "#94a3b8")
        ).pack(anchor="w")

        ctk.CTkLabel(
            inner,
            text="›",
            font=("Segoe UI", 27),
            text_color=("#64748b", "#94a3b8")
        ).pack(side="right", padx=(10, 0))

    def _dashboard_panel(self, parent, title):
        frame = ctk.CTkFrame(
            parent,
            fg_color=("#f4f6f7", "#102033"),
            border_width=1,
            border_color=("#d7dde2", "#1b3349"),
            corner_radius=15
        )
        ctk.CTkLabel(
            frame,
            text=translate(title, self.language),
            font=("Segoe UI", 15, "bold"),
            text_color=("#1e293b", "#e2e8f0")
        ).pack(anchor="w", padx=17, pady=(14, 10))
        return frame

    def section_categories(self, parent, total, day, column=0):
        frame = self._dashboard_panel(parent, translate("Kullanım Dağılımı (Bugün)", self.language))
        frame.grid(row=0, column=column, sticky="nsew",
                   padx=(0 if column == 0 else 4, 4 if column == 0 else 0), pady=0)

        cats = sorted(
            day.get("categories", {}).items(),
            key=lambda x: x[1],
            reverse=True
        )

        if not cats:
            ctk.CTkLabel(
                frame,
                text=translate("Henüz yeterli veri yok.", self.language),
                text_color=("#64748b", "#94a3b8")
            ).pack(pady=30)
            return

        cats = cats[:8]
        for cat, sec in cats:
            ratio = sec / total if total else 0
            color = CATEGORY_COLORS.get(cat, "#94a3b8")

            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", padx=15, pady=3)

            icon_box = ctk.CTkFrame(
                row, width=24, height=24,
                fg_color=color, corner_radius=7
            )
            icon_box.pack(side="left")
            icon_box.pack_propagate(False)

            icon_text = {
                "Mühendislik": "⚒", "Tarayıcı": "◎", "Geliştirme": "</>",
                "Çalışma": "▣", "Medya": "♫", "İletişim": "◉",
                "Oyun": "◆", "Belge": "▤", "Tasarım": "✦",
                "Sistem": "⚙", "Diğer": "•"
            }.get(cat, "•")

            ctk.CTkLabel(
                icon_box, text=icon_text,
                font=("Segoe UI Symbol", 12, "bold"),
                text_color="#ffffff"
            ).place(relx=0.5, rely=0.5, anchor="center")

            ctk.CTkLabel(
                row,
                text=translate(cat, self.language),
                width=82,
                anchor="w",
                font=("Segoe UI", 12, "bold")
            ).pack(side="left", padx=(8, 8))

            bar = ctk.CTkProgressBar(
                row, height=7,
                progress_color=color,
                fg_color=("#e2e8f0", "#172c40")
            )
            bar.set(ratio)
            bar.pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(
                row,
                text=f"{format_short(sec)}",
                width=62,
                anchor="e",
                font=("Segoe UI", 12),
                text_color=("#475569", "#cbd5e1")
            ).pack(side="right", padx=(8, 0))

            ctk.CTkLabel(
                row,
                text=f"%{int(ratio * 100)}",
                width=32,
                anchor="e",
                font=("Segoe UI", 12),
                text_color=("#64748b", "#94a3b8")
            ).pack(side="right")

    def section_week(self, parent, week_days, column=1):
        frame = self._dashboard_panel(parent, translate("Son 7 Gün", self.language))
        frame.grid(row=0, column=column, sticky="nsew",
                   padx=(4, 0), pady=0)

        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x", padx=17, pady=(0, 3))

        ctk.CTkLabel(
            top,
            text=translate("Tümünü Gör", self.language) + "  →",
            font=("Segoe UI", 12, "bold"),
            text_color="#38bdf8"
        ).pack(side="right")

        self.draw_week_chart(frame, week_days)

    def draw_week_chart(self, parent, days):
        chart = tk.Canvas(
            parent,
            height=205,
            bg="#f4f6f7" if ctk.get_appearance_mode() == "Light" else "#102033",
            highlightthickness=0
        )
        chart.pack(fill="x", padx=14, pady=(2, 10))

        chart.update_idletasks()
        width = max(chart.winfo_width(), 500)
        height = 205

        now = datetime.now()
        values = []
        for i in range(6, -1, -1):
            d = now - timedelta(days=i)
            values.append((
                d,
                days.get(d.strftime("%Y-%m-%d"), {}).get("total_active_seconds", 0)
            ))

        max_sec = max([sec for _, sec in values] + [3600])
        # Dashboard "Son 7 Gün" grafiğinde en uzun sütunun değer etiketi
        # sütunun içine girmesin diye üstte ekstra alan bırakıyorum.
        left, right, top, bottom = 12, 12, 34, 35
        plot_w = width - left - right
        plot_h = height - top - bottom
        gap = max(10, plot_w * 0.025)
        bar_w = max(18, (plot_w - gap * 6) / 7)

        bg = "#f4f6f7" if ctk.get_appearance_mode() == "Light" else "#102033"
        text_color = "#64748b" if ctk.get_appearance_mode() == "Light" else "#94a3b8"
        bar_color = "#329bea"

        chart.configure(bg=bg)

        for i, (d, sec) in enumerate(values):
            x = left + i * (bar_w + gap)
            bh = (sec / max_sec) * (plot_h - 10) if max_sec else 0
            y = top + plot_h - bh

            # Hafif taban çizgisi
            chart.create_line(
                x, top + plot_h, x + bar_w, top + plot_h,
                fill="#d5dce2" if ctk.get_appearance_mode() == "Light" else "#20384d"
            )

            # Bar
            chart.create_rectangle(
                x, y, x + bar_w, top + plot_h,
                fill=bar_color, outline=""
            )

            if sec > 0:
                # Değer etiketi artık sütunun üstünde ayrı bir bölgede duruyor.
                # Özellikle 2s 03dk gibi uzun metinler sütunla çakışmıyor.
                label_y = max(14, y - 14)
                chart.create_text(
                    x + bar_w / 2,
                    label_y,
                    text=format_short(sec),
                    fill=text_color,
                    font=("Segoe UI", 11)
                )

            chart.create_text(
                x + bar_w / 2,
                top + plot_h + 17,
                text=self.localized_weekday(d),
                fill=text_color,
                font=("Segoe UI", 12)
            )

    def section_goals(self, parent, day, column=0):
        settings = get_settings()
        goal = int(settings.get("daily_goal_seconds", 6 * 3600))
        total = int(day.get("total_active_seconds", 0))
        ratio = min(1.0, total / goal) if goal else 0

        frame = ctk.CTkFrame(
            parent,
            fg_color=("#e9edf0", "#12283b"),
            border_width=1,
            border_color=("#d4dbe0", "#1d3b52"),
            corner_radius=15
        )
        frame.grid(row=1, column=column, sticky="nsew",
                   padx=(0 if column == 0 else 4, 4 if column == 0 else 0),
                   pady=(8, 0))

        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x", padx=17, pady=(13, 3))

        ctk.CTkLabel(
            top,
            text=translate("Günlük hedef", self.language),
            font=("Segoe UI", 15, "bold"),
            text_color=("#1e293b", "#e2e8f0")
        ).pack(side="left")

        ctk.CTkLabel(
            top,
            text=f"%{int(ratio * 100)}",
            font=("Segoe UI", 12, "bold"),
            text_color="#22c55e"
        ).pack(side="right")

        bar = ctk.CTkProgressBar(
            frame, height=8,
            progress_color="#22c55e",
            fg_color=("#d4dbe1", "#23445b")
        )
        bar.set(ratio)
        bar.pack(fill="x", padx=17, pady=(4, 3))

        ctk.CTkLabel(
            frame,
            text=f"{format_seconds(total)} / {format_seconds(goal)}",
            font=("Segoe UI", 12),
            text_color=("#64748b", "#94a3b8")
        ).pack(anchor="w", padx=17, pady=(0, 12))

        self._dashboard_widgets = self.dash_widgets

    def calculate_score(self,day):
        total=day.get("total_active_seconds",0); longest=day.get("longest_continuous_seconds",0); breaks=len(day.get("breaks",[])); score=100
        if total>8*3600: score-=min(25,int((total-8*3600)/1800)*3)
        if longest>90*60: score-=min(25,int((longest-90*60)/900)*3)
        if total>3*3600 and breaks==0: score-=15
        elif total>5*3600 and breaks<2: score-=10
        return max(0,min(100,score))

    def update_dashboard_live(self):
        if self.current_page != "dashboard" or not self.dash_widgets:
            return
        _, day = get_today_snapshot()
        status = get_live_status()

    def show_apps(self):
        self._begin_page_build("apps")
        self.page_title.configure(text=translate("Uygulamalar", self.language))
        _, day = self.get_today_light()
        apps = day.get("apps", {})
        total = day.get("total_active_seconds", 0)
        sessions = day.get("sessions", [])

        # Uygulama oturumlarını tek geçişte grupla. Böylece her uygulama
        # için sessions listesini tekrar taramak yerine O(n) işlem yapılır.
        sessions_by_app = {}
        for session in sessions:
            if session.get("type") != "application":
                continue
            app_name = session.get("app")
            if not app_name:
                continue
            bucket = sessions_by_app.setdefault(app_name, {"count": 0, "longest": 0})
            bucket["count"] += 1
            bucket["longest"] = max(
                bucket["longest"], int(session.get("duration_seconds", 0) or 0)
            )

        stats = {}
        custom = get_custom_app_categories_snapshot()
        for app, sec in apps.items():
            app_stats = sessions_by_app.get(app, {"count": 0, "longest": 0})
            stats[app] = {
                "seconds": sec,
                "count": app_stats["count"],
                "longest": app_stats["longest"],
                "category": custom.get(app.lower(), get_category(app)),
            }

        if not stats:
            ctk.CTkLabel(self.content, text=translate("Bugün henüz uygulama verisi yok.", self.language), font=("Segoe UI", 20), text_color=("#64748b", "#94a3b8")).pack(pady=100)
            return

        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.pack(fill="x", padx=7, pady=(2, 8))
        ctk.CTkLabel(header, text=translate("Uygulamalar", self.language), font=("Segoe UI", 26, "bold"), text_color=("#0f172a", "#f8fafc")).pack(side="left")
        ctk.CTkLabel(header, text=f"{len(stats)} {translate('uygulama', self.language)}", font=("Segoe UI", 12), text_color=("#64748b", "#94a3b8")).pack(side="left", padx=12, pady=(8, 0))

        list_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        list_frame.pack(fill="x", padx=5, pady=2)

        for app, st in sorted(stats.items(), key=lambda x: x[1]["seconds"], reverse=True):
            ratio = st["seconds"] / total if total else 0
            category = st["category"]
            accent = CATEGORY_COLORS.get(category, "#38bdf8")
            card = ctk.CTkFrame(list_frame, fg_color=("#f4f6f8", "#10283d"), border_width=1, border_color=("#d8dee5", "#1d3b55"), corner_radius=14)
            card.pack(fill="x", pady=4)
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=11)

            icon_image = get_app_icon_image(app, 44)
            icon_ctk = ctk.CTkImage(light_image=icon_image, dark_image=icon_image, size=(44, 44))
            icon_label = ctk.CTkLabel(row, image=icon_ctk, text="", width=44)
            icon_label.pack(side="left", padx=(0, 12))
            icon_label._app_icon_ref = icon_ctk

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(info, text=app.replace(".exe", ""), font=("Segoe UI", 16, "bold"), anchor="w", text_color=("#0f172a", "#f8fafc")).pack(anchor="w")
            ctk.CTkLabel(info, text=f"{translate('Oturum:', self.language)} {st['count']}  •  {translate('En uzun:', self.language)} {format_seconds(st['longest'])}", font=("Segoe UI", 11), text_color=("#64748b", "#94a3b8")).pack(anchor="w", pady=(3, 0))

            ctk.CTkLabel(row, text=f"{format_seconds(st['seconds'])}\n%{int(ratio * 100)}", font=("Segoe UI", 13, "bold"), justify="right", text_color=("#334155", "#e2e8f0")).pack(side="right", padx=(12, 16))
            ctk.CTkButton(row, text=translate(category, self.language), width=125, height=34, corner_radius=9, fg_color=accent, hover_color=accent, font=("Segoe UI", 12, "bold"), command=lambda a=app: self.change_category(a)).pack(side="right", padx=4)

            bar_bg = ctk.CTkFrame(card, height=4, fg_color=("#e2e8f0", "#1b344a"), corner_radius=2)
            bar_bg.pack(fill="x", padx=14, pady=(0, 12)); bar_bg.pack_propagate(False)
            bar = ctk.CTkProgressBar(bar_bg, height=4, corner_radius=2, fg_color=("#dbe2e8", "#0f2135"), progress_color=accent)
            bar.set(ratio); bar.pack(fill="both", expand=True)

    def change_category(self,app):
        win=ctk.CTkToplevel(self); win.title(translate("Kategori değiştir",self.language)); win.geometry("360x190"); win.transient(self); win.grab_set()
        ctk.CTkLabel(win,text=app.replace(".exe","")+" "+translate("kategorisi",self.language)).pack(pady=(25,8))
        category_values = get_all_categories()
        translated_values = [translate(cat, self.language) for cat in category_values]
        combo = ctk.CTkComboBox(win, values=translated_values)
        combo.pack(padx=30, fill="x")
        current_category = get_category(app)
        combo.set(translate(current_category, self.language))
        def save():
            selected = combo.get()
            reverse = {translate(cat, self.language): cat for cat in category_values}
            selected_category = reverse.get(selected, selected)
            with data_lock:
                bellek_db.setdefault("custom_app_categories",{})[app.lower()] = selected_category
            veri_diske_yaz(); win.destroy(); self.show_apps()
        ctk.CTkButton(win,text=translate("Kaydet", self.language),command=save).pack(pady=20)

    def add_category_dialog(self):
        win=ctk.CTkToplevel(self); win.title(translate("Yeni kategori", self.language)); win.geometry("420x250"); win.transient(self); win.grab_set()
        ctk.CTkLabel(win,text=translate("Yeni kategori adı", self.language),font=("Segoe UI", 18,"bold")).pack(pady=(25,8))
        entry=ctk.CTkEntry(win,placeholder_text=translate("Örn. Eğitim, Sosyal Medya, Tasarım...", self.language)); entry.pack(fill="x",padx=30)
        ctk.CTkLabel(win,text=translate("Kategori, uygulama sınıflandırmasında kullanılabilir.", self.language),text_color=("#64748b","#94a3b8")).pack(padx=30,pady=10)
        def save():
            name=entry.get().strip()
            if not name: return
            if name in get_all_categories(): return
            with data_lock: bellek_db.setdefault("custom_categories",{})[name]={"created_at":datetime.now().isoformat()}
            veri_diske_yaz(); win.destroy(); self.show_apps()
        ctk.CTkButton(win,text=translate("Kategori Oluştur", self.language),command=save).pack(pady=20)

    def show_sessions(self):
        """Modern session timeline matching the app's navy/cyan visual language."""
        self._begin_page_build("sessions")
        self.page_title.configure(text=translate("Oturumlar", self.language))

        _, day = self.get_today_light()
        sessions = day.get("sessions", []) or []
        breaks = day.get("breaks", []) or []
        app_sessions = [s for s in sessions if s.get("type") in ("application", "continuous")]
        app_sessions = list(reversed(app_sessions[-100:]))

        def sec_value(item):
            return item.get("duration_seconds", item.get("duration", 0)) or 0

        total = day.get("total_active_seconds", 0) or 0
        longest = max([sec_value(s) for s in app_sessions] + [0])
        session_count = len([s for s in app_sessions if s.get("type") == "application"])
        break_count = len(breaks)

        # Header / filter-like toolbar
        toolbar = ctk.CTkFrame(
            self.content,
            fg_color=("#d1d5db", "#102238"),
            corner_radius=14,
            border_width=1,
            border_color=("#c4c9cf", "#1c3852")
        )
        toolbar.pack(fill="x", padx=5, pady=(2, 9))

        head = ctk.CTkFrame(toolbar, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 4))

        ctk.CTkLabel(
            head,
            text=translate("Bugünkü Oturumlar", self.language),
            font=("Segoe UI", 19, "bold"),
            text_color=("#0f172a", "#f8fafc")
        ).pack(side="left")

        ctk.CTkLabel(
            head,
            text=datetime.now().strftime("%d.%m.%Y"),
            font=("Segoe UI", 12),
            text_color=("#64748b", "#94a3b8")
        ).pack(side="right")

        ctk.CTkLabel(
            toolbar,
            text=translate("Uygulamalar arasında geçen aktif kullanım sürelerini ve kesintisiz oturumları zaman çizelgesinde inceleyebilirsin.", self.language),
            font=("Segoe UI", 12),
            text_color=("#64748b", "#94a3b8"),
            anchor="w",
            justify="left"
        ).pack(fill="x", padx=16, pady=(0, 12))

        # Summary cards
        summary = ctk.CTkFrame(self.content, fg_color="transparent")
        summary.pack(fill="x", padx=5, pady=(0, 8))
        for i in range(4):
            summary.grid_columnconfigure(i, weight=1)

        cards = [
            ("Toplam Kullanım", format_seconds(total), "◷", "#38bdf8"),
            ("Oturum Sayısı", str(session_count), "▤", "#a855f7"),
            ("En Uzun Oturum", format_seconds(longest), "↗", "#22c55e"),
            ("Mola Sayısı", str(break_count), "☕", "#f59e0b"),
        ]

        for i, (title, value, icon, accent) in enumerate(cards):
            card = ctk.CTkFrame(
                summary,
                fg_color=("#d1d5db", "#142b42"),
                corner_radius=12,
                border_width=1,
                border_color=("#c3c8ce", "#1d3a55")
            )
            card.grid(row=0, column=i, sticky="nsew", padx=4)

            icon_box = ctk.CTkFrame(card, width=38, height=38, corner_radius=10, fg_color=accent)
            icon_box.pack(side="left", padx=(12, 9), pady=12)
            icon_box.pack_propagate(False)
            ctk.CTkLabel(icon_box, text=icon, font=("Segoe UI Symbol", 18, "bold"), text_color="#ffffff").pack(expand=True)

            info = ctk.CTkFrame(card, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, pady=9)
            ctk.CTkLabel(info, text=translate(title, self.language), font=("Segoe UI", 12), text_color=("#64748b", "#94a3b8"), anchor="w").pack(anchor="w")
            ctk.CTkLabel(info, text=value, font=("Segoe UI", 18, "bold"), text_color=("#0f172a", "#f8fafc"), anchor="w").pack(anchor="w")

        # Timeline container
        timeline = ctk.CTkFrame(
            self.content,
            fg_color=("#d1d5db", "#102238"),
            corner_radius=14,
            border_width=1,
            border_color=("#c4c9cf", "#1c3852")
        )
        timeline.pack(fill="both", expand=True, padx=5, pady=(3, 5))

        topbar = ctk.CTkFrame(timeline, fg_color="transparent")
        topbar.pack(fill="x", padx=16, pady=(13, 7))
        ctk.CTkLabel(
            topbar,
            text=translate("Zaman Çizelgesi", self.language),
            font=("Segoe UI", 15, "bold"),
            text_color=("#0f172a", "#f8fafc")
        ).pack(side="left")
        ctk.CTkLabel(
            topbar,
            text=f"{len(app_sessions)} {translate('kayıt', self.language)}",
            font=("Segoe UI", 11),
            text_color=("#64748b", "#94a3b8")
        ).pack(side="right")

        scroll = ctk.CTkScrollableFrame(
            timeline,
            fg_color="transparent",
            scrollbar_button_color=("#9ca3af", "#245276"),
            scrollbar_button_hover_color=("#64748b", "#2e6b96")
        )
        scroll.pack(fill="both", expand=True, padx=9, pady=(0, 10))

        if not app_sessions and not breaks:
            ctk.CTkLabel(
                scroll,
                text=translate("Bugün henüz tamamlanmış bir oturum bulunmuyor.", self.language),
                font=("Segoe UI", 14),
                text_color=("#64748b", "#94a3b8")
            ).pack(pady=80)
            return

        # Merge session and break events into one chronological timeline.
        events = []
        for s in app_sessions:
            start = s.get("start")
            dt = None
            if start:
                try:
                    dt = datetime.fromisoformat(start)
                except Exception:
                    dt = None
            events.append((dt or datetime.min, "session", s))

        for b in breaks:
            start = b.get("start") or b.get("time")
            dt = None
            if start:
                try:
                    dt = datetime.fromisoformat(start)
                except Exception:
                    dt = None
            events.append((dt or datetime.min, "break", b))

        events.sort(key=lambda x: x[0], reverse=True)

        for dt, event_type, item in events:
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(1, weight=1)

            # Time column
            time_text = dt.strftime("%H:%M") if dt != datetime.min else "--:--"
            time_box = ctk.CTkFrame(row, width=58, fg_color="transparent")
            time_box.grid(row=0, column=0, sticky="ns", padx=(3, 8))
            time_box.grid_propagate(False)
            ctk.CTkLabel(
                time_box,
                text=time_text,
                font=("Segoe UI", 12, "bold"),
                text_color=("#475569", "#cbd5e1")
            ).pack(anchor="e", pady=10)

            accent = "#f59e0b" if event_type == "break" else "#38bdf8"
            icon = "☕" if event_type == "break" else "◷"

            icon_box = ctk.CTkFrame(row, width=36, height=36, corner_radius=10, fg_color=accent)
            icon_box.grid(row=0, column=1, sticky="nw", pady=3)
            icon_box.grid_propagate(False)
            ctk.CTkLabel(icon_box, text=icon, font=("Segoe UI Symbol", 16, "bold"), text_color="#ffffff").pack(expand=True)

            body = ctk.CTkFrame(
                row,
                fg_color=("#e5e7eb", "#142b42"),
                corner_radius=10,
                border_width=1,
                border_color=("#d0d4d8", "#1d3a55")
            )
            body.grid(row=0, column=2, sticky="ew", padx=(8, 3))
            body.grid_columnconfigure(0, weight=1)

            if event_type == "break":
                title = translate("Mola", self.language)
                duration = item.get("duration_seconds", item.get("duration", 0)) or 0
                category = translate("Dinlenme", self.language)
            else:
                is_cont = item.get("type") == "continuous"
                raw_app = item.get("app", "")
                title = (
                    translate("Kesintisiz Bilgisayar Kullanımı", self.language)
                    if is_cont else raw_app.replace(".exe", "")
                )
                duration = sec_value(item)
                category = translate(item.get("category", "Sistem"), self.language)

            line1 = ctk.CTkFrame(body, fg_color="transparent")
            line1.pack(fill="x", padx=12, pady=(8, 1))
            ctk.CTkLabel(
                line1,
                text=title,
                font=("Segoe UI", 13, "bold"),
                text_color=("#0f172a", "#f8fafc"),
                anchor="w"
            ).pack(side="left")
            ctk.CTkLabel(
                line1,
                text=format_seconds(duration),
                font=("Segoe UI", 12, "bold"),
                text_color=accent
            ).pack(side="right")

            line2 = ctk.CTkFrame(body, fg_color="transparent")
            line2.pack(fill="x", padx=12, pady=(0, 8))
            ctk.CTkLabel(
                line2,
                text=category,
                font=("Segoe UI", 11),
                text_color=("#64748b", "#94a3b8"),
                anchor="w"
            ).pack(side="left")

            if event_type == "session" and item.get("end"):
                try:
                    end_dt = datetime.fromisoformat(item["end"])
                    end_text = end_dt.strftime("%H:%M")
                    ctk.CTkLabel(
                        line2,
                        text=f"→ {end_text}",
                        font=("Segoe UI", 11),
                        text_color=("#64748b", "#94a3b8")
                    ).pack(side="right")
                except Exception:
                    pass

        # Small footer note
        ctk.CTkLabel(
            timeline,
            text=translate("En yeni kayıtlar üstte gösterilir.", self.language),
            font=("Segoe UI", 10),
            text_color=("#64748b", "#94a3b8")
        ).pack(anchor="w", padx=18, pady=(0, 9))

    def localized_month_year(self, dt):
        months={
            "tr":["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"],
            "en":["January","February","March","April","May","June","July","August","September","October","November","December"],
            "de":["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"],
            "fr":["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"],
            "es":["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"],
            "it":["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"],
            "pt":["janeiro","fevereiro","março","abril","maio","junho","julho","agosto","setembro","outubro","novembro","dezembro"],
            "nl":["januari","februari","maart","april","mei","juni","juli","augustus","september","oktober","november","december"],
            "ar":["يناير","فبراير","مارس","أبريل","مايو","يونيو","يوليو","أغسطس","سبتمبر","أكتوبر","نوفمبر","ديسمبر"],
            "cs":["leden","únor","březen","duben","květen","červen","červenec","srpen","září","říjen","listopad","prosinec"],
            "hu":["január","február","március","április","május","június","július","augusztus","szeptember","október","november","december"],
            "ja":["1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"],
            "ko":["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"],
            "pl":["styczeń","luty","marzec","kwiecień","maj","czerwiec","lipiec","sierpień","wrzesień","październik","listopad","grudzień"],
            "ru":["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"],
            "zh-CN":["一月","二月","三月","四月","五月","六月","七月","八月","九月","十月","十一月","十二月"]}
        return f"{months.get(LANGUAGE_CODES.get(self.language, 'tr'), months['tr'])[dt.month-1]} {dt.year}"

    def localized_weekday(self, dt):
        names={"tr":["Pzt","Sal","Çar","Per","Cum","Cmt","Paz"],"en":["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],"de":["Mo","Di","Mi","Do","Fr","Sa","So"],"fr":["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"],"es":["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"],"it":["Lun","Mar","Mer","Gio","Ven","Sab","Dom"],"pt":["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"],"nl":["Ma","Di","Wo","Do","Vr","Za","Zo"],"ar":["الإث","الث","الأر","الخ","الجم","السب","الأحد"],"cs":["Po","Út","St","Čt","Pá","So","Ne"],"hu":["H","K","Sze","Cs","P","Szo","V"],"ja":["月","火","水","木","金","土","日"],"ko":["월","화","수","목","금","토","일"],"pl":["Pn","Wt","Śr","Cz","Pt","So","Nd"],"ru":["Пн","Вт","Ср","Чт","Пт","Сб","Вс"],"zh-CN":["周一","周二","周三","周四","周五","周六","周日"]}
        return names.get(LANGUAGE_CODES.get(self.language,"tr"),names["tr"])[dt.weekday()]

    def show_stats(self, month_offset=0, selected_key=None):
        """Modern statistics dashboard inspired by the supplied reference design."""
        import calendar

        self._begin_page_build("stats")
        self.page_title.configure(text=translate("İstatistikler", self.language))

        # Keep the selected month between refreshes.
        if not hasattr(self, "calendar_month") or month_offset != 0:
            base = getattr(self, "calendar_month", datetime.now().replace(day=1))
            if month_offset != 0:
                y = base.year + (base.month - 1 + month_offset) // 12
                m = (base.month - 1 + month_offset) % 12 + 1
                base = base.replace(year=y, month=m, day=1)
            self.calendar_month = base.replace(day=1)

        now = self.calendar_month
        prefix = now.strftime("%Y-%m")

        # This page only ever reads the browsed month (calendar / daily /
        # stacked / donut charts) plus the last 14 real days (7-day trend
        # and 7-vs-7 comparison, always relative to *today* even while
        # browsing another month) — so only fetch those, instead of every
        # day ever tracked like get_db_snapshot() used to. That full-history
        # copy was the main reason this page got slower the longer the app
        # had been in use.
        day_count_in_month = calendar.monthrange(now.year, now.month)[1]
        needed_keys = {f"{now.year:04d}-{now.month:02d}-{d:02d}" for d in range(1, day_count_in_month + 1)}
        real_now = datetime.now()
        needed_keys.update((real_now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14))
        provisional_today = real_now.strftime("%Y-%m-%d")
        full_keys = {provisional_today}
        if selected_key:
            full_keys.add(selected_key)
        days = get_days_stats_snapshot(needed_keys, full_keys=full_keys)

        month_items = sorted((k, d) for k, d in days.items() if k.startswith(prefix))

        # Selected day.
        if selected_key is None:
            today_key = real_now.strftime("%Y-%m-%d")
            selected_key = (
                today_key if today_key.startswith(prefix)
                else (month_items[-1][0] if month_items else None)
            )
        if selected_key and selected_key in days and "sessions" not in days[selected_key]:
            days[selected_key] = get_day_snapshot(selected_key)

        # Date/range header.
        self.page_date.configure(
            text=f"{self.localized_month_year(now)} • "
                 f"{translate('Detaylı kullanım analizi', self.language)}"
        )

        # --------------------------------------------------------
        # TOP TOOLBAR
        # --------------------------------------------------------
        toolbar = ctk.CTkFrame(
            self.content,
            fg_color=("#d1d5db", "#122235"),
            corner_radius=12
        )
        toolbar.pack(fill="x", padx=5, pady=(2, 8))

        left_tools = ctk.CTkFrame(toolbar, fg_color="transparent")
        left_tools.pack(side="left", padx=12, pady=9)

        ctk.CTkButton(
            left_tools,
            text=translate("‹ Önceki Ay", self.language),
            width=112,
            height=32,
            corner_radius=8,
            command=lambda: self.show_stats(-1)
        ).pack(side="left", padx=3)

        ctk.CTkLabel(
            left_tools,
            text=self.localized_month_year(now),
            font=("Segoe UI", 12, "bold")
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            left_tools,
            text=translate("Sonraki Ay ›", self.language),
            width=112,
            height=32,
            corner_radius=8,
            command=lambda: self.show_stats(1)
        ).pack(side="left", padx=3)

        ctk.CTkLabel(
            toolbar,
            text=translate("Analiz dönemi", self.language),
            font=("Segoe UI", 12),
            text_color=("#64748b", "#94a3b8")
        ).pack(side="right", padx=(8, 18))

        # --------------------------------------------------------
        # MONTH CALCULATIONS
        # --------------------------------------------------------
        month_total = sum(
            d.get("total_active_seconds", 0) for _, d in month_items
        )
        used_days = sum(
            1 for _, d in month_items
            if d.get("total_active_seconds", 0) > 0
        )
        month_avg = month_total / used_days if used_days else 0
        month_longest = max(
            [d.get("longest_continuous_seconds", 0) for _, d in month_items] + [0]
        )
        month_breaks = sum(
            len(d.get("breaks", [])) for _, d in month_items
        )

        # --------------------------------------------------------
        # SUMMARY CARDS
        # --------------------------------------------------------
        summary = ctk.CTkFrame(self.content, fg_color="transparent")
        summary.pack(fill="x", padx=5, pady=(0, 8))
        for i in range(4):
            summary.grid_columnconfigure(i, weight=1)

        summary_data = [
            (
                "Toplam Kullanım",
                format_seconds(month_total),
                f"{used_days} {translate('aktif gün', self.language)}",
                "#38bdf8",
                "◷"
            ),
            (
                "Günlük Ortalama",
                format_seconds(month_avg),
                translate("aktif gün ortalaması", self.language),
                "#a855f7",
                "▥"
            ),
            (
                "En Verimli Gün",
                self._stats_best_day_text(month_items),
                self._stats_best_day_subtitle(month_items),
                "#22c55e",
                "◆"
            ),
            (
                "En Yoğun Kategori",
                self._stats_top_category_text(month_items),
                self._stats_top_category_subtitle(month_items),
                "#f59e0b",
                "●"
            )
        ]

        for i, (title, value, subtitle, accent, icon) in enumerate(summary_data):
            card = ctk.CTkFrame(
                summary,
                fg_color=("#d1d5db", "#14283d"),
                corner_radius=12,
                border_width=1,
                border_color=("#c3c8ce", "#1d3852")
            )
            card.grid(row=0, column=i, sticky="nsew", padx=4)

            icon_box = ctk.CTkFrame(
                card,
                width=38,
                height=38,
                corner_radius=10,
                fg_color=accent
            )
            icon_box.pack(side="left", padx=(12, 9), pady=12)
            icon_box.pack_propagate(False)

            ctk.CTkLabel(
                icon_box,
                text=icon,
                font=("Segoe UI Symbol", 18, "bold"),
                text_color="#ffffff"
            ).pack(expand=True)

            info = ctk.CTkFrame(card, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, pady=9)

            ctk.CTkLabel(
                info,
                text=translate(title, self.language),
                font=("Segoe UI", 12),
                text_color=("#64748b", "#94a3b8"),
                anchor="w"
            ).pack(anchor="w")

            ctk.CTkLabel(
                info,
                text=value,
                font=("Segoe UI", 18, "bold"),
                text_color=("#0f172a", "#f8fafc"),
                anchor="w"
            ).pack(anchor="w")

            ctk.CTkLabel(
                info,
                text=subtitle,
                font=("Segoe UI", 11),
                text_color=accent,
                anchor="w"
            ).pack(anchor="w")

        # --------------------------------------------------------
        # ROW 1: DAILY USAGE + CATEGORY DONUT
        # --------------------------------------------------------
        row1 = ctk.CTkFrame(self.content, fg_color="transparent")
        row1.pack(fill="x", padx=5, pady=4)
        row1.grid_columnconfigure(0, weight=2)
        row1.grid_columnconfigure(1, weight=1)

        daily_box = self._stats_panel(
            row1,
            "Günlük Kullanım Süresi",
            translate("Bu ayın günlük aktif kullanım süresi", self.language)
        )
        daily_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        daily_values = []
        for day_num in range(1, calendar.monthrange(now.year, now.month)[1] + 1):
            key = f"{now.year:04d}-{now.month:02d}-{day_num:02d}"
            daily_values.append(
                (day_num, days.get(key, {}).get("total_active_seconds", 0))
            )
        daily_box._stats_renderer = lambda target, vals=daily_values: self.draw_usage_bar_chart(target, vals, height=430)
        self.draw_usage_bar_chart(daily_box, daily_values, height=245)

        cat_totals = self._stats_category_totals(month_items)
        category_box = self._stats_panel(
            row1,
            "Kategori Dağılımı",
            translate("Toplam kullanımın kategori bazında dağılımı", self.language)
        )
        category_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        category_box._stats_renderer = lambda target, cats=cat_totals, total=month_total: self.draw_stats_donut(target, cats, total, height=430)
        self.draw_stats_donut(category_box, cat_totals, month_total, height=245)

        # --------------------------------------------------------
        # ROW 2: STACKED CATEGORY + TOP APPS
        # --------------------------------------------------------
        row2 = ctk.CTkFrame(self.content, fg_color="transparent")
        row2.pack(fill="x", padx=5, pady=4)
        row2.grid_columnconfigure(0, weight=2)
        row2.grid_columnconfigure(1, weight=1)

        stacked_box = self._stats_panel(
            row2,
            "Kategori Bazında Günlük Dağılım",
            translate("Günlük kullanımın kategorilere göre dağılımı", self.language)
        )
        stacked_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        stacked_box._stats_renderer = lambda target, month=now, all_days=days: self.draw_category_stacked_chart(target, month, all_days, height=430)
        self.draw_category_stacked_chart(
            stacked_box,
            now,
            days,
            height=245
        )

        top_apps_box = self._stats_panel(
            row2,
            "En Çok Kullanılan Uygulamalar",
            translate("Bu ay en fazla kullanılan uygulamalar", self.language)
        )
        top_apps_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        top_apps_box._stats_renderer = lambda target, items=month_items, total=month_total: self.draw_top_apps_list(target, items, total)
        self.draw_top_apps_list(top_apps_box, month_items, month_total)

        # --------------------------------------------------------
        # ROW 3: TREND + COMPARISON
        # --------------------------------------------------------
        row3 = ctk.CTkFrame(self.content, fg_color="transparent")
        row3.pack(fill="x", padx=5, pady=4)
        row3.grid_columnconfigure(0, weight=2)
        row3.grid_columnconfigure(1, weight=1)

        trend_box = self._stats_panel(
            row3,
            "Kullanım Trendleri",
            translate("Son 7 günün kullanım eğilimi", self.language)
        )
        trend_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        trend_header = ctk.CTkFrame(trend_box, fg_color="transparent")
        trend_header.pack(fill="x", padx=18, pady=(0, 2))
        ctk.CTkLabel(
            trend_header,
            text=translate("Toplam Kullanım", self.language),
            font=("Segoe UI", 11, "bold"),
            text_color=("#64748b", "#94a3b8")
        ).pack(side="left")

        ctk.CTkLabel(
            trend_header,
            text=translate("7 Gün", self.language),
            font=("Segoe UI", 11, "bold"),
            text_color=("#38bdf8", "#67e8f9")
        ).pack(side="right")

        trend_box._stats_renderer = lambda target, all_days=days: self.draw_stats_trend_chart(target, all_days, height=430)
        self.draw_stats_trend_chart(trend_box, days, height=230)

        comparison_box = self._stats_panel(
            row3,
            "Karşılaştırma",
            translate("Bu hafta ve önceki haftanın özeti", self.language)
        )
        comparison_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        comparison_box._stats_renderer = lambda target, all_days=days: self.draw_week_comparison(target, all_days)
        self.draw_week_comparison(comparison_box, days)

        # --------------------------------------------------------
        # SELECTED DAY DETAILS
        # --------------------------------------------------------
        detail_title = ctk.CTkFrame(
            self.content,
            fg_color="transparent"
        )
        detail_title.pack(fill="x", padx=7, pady=(12, 3))

        ctk.CTkLabel(
            detail_title,
            text=translate("Seçilen Gün", self.language),
            font=("Segoe UI", 19, "bold"),
            text_color=("#0f172a", "#f8fafc")
        ).pack(side="left")

        if selected_key:
            ctk.CTkLabel(
                detail_title,
                text=selected_key,
                font=("Segoe UI", 12),
                text_color=("#64748b", "#94a3b8")
            ).pack(side="left", padx=12)

        # Calendar remains available below the modern analytics.
        calendar_box = self._stats_panel(
            self.content,
            "Kullanım Takvimi",
            translate("Bir güne tıklayarak o günün ayrıntılarını açabilirsin.", self.language)
        )
        calendar_box.pack(fill="x", padx=5, pady=4)
        self.draw_month_calendar(calendar_box, now, days, selected_key)

        self.show_selected_day(self.content, selected_key, days)

    def _stats_panel(self, parent, title, subtitle=""):
        """Create a modern analytics card with a compact expand control."""
        frame = ctk.CTkFrame(
            parent,
            fg_color=("#d1d5db", "#14283d"),
            corner_radius=12,
            border_width=1,
            border_color=("#c3c8ce", "#1d3852")
        )

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(10, 2))

        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            title_frame,
            text=translate(title, self.language),
            font=("Segoe UI", 16, "bold"),
            text_color=("#0f172a", "#f8fafc"),
            anchor="w"
        ).pack(anchor="w")

        if subtitle:
            ctk.CTkLabel(
                title_frame,
                text=subtitle,
                font=("Segoe UI", 12),
                text_color=("#64748b", "#94a3b8"),
                anchor="w"
            ).pack(anchor="w", pady=(1, 0))

        expand_btn = ctk.CTkButton(
            header,
            text="↗",
            width=30,
            height=30,
            corner_radius=8,
            font=("Segoe UI Symbol", 17, "bold"),
            fg_color=("#e5e7eb", "#1b344c"),
            hover_color=("#cbd5e1", "#244764"),
            text_color=("#334155", "#dbeafe"),
            command=lambda f=frame: self._open_stats_panel(f)
        )
        expand_btn.pack(side="right", padx=(8, 0))

        frame._stats_title = title
        frame._stats_subtitle = subtitle
        frame._stats_renderer = None
        return frame

    def _open_stats_panel(self, panel):
        """Open a statistics card in a large, closable detail window."""
        renderer = getattr(panel, "_stats_renderer", None)
        if renderer is None:
            return

        # Keep only one expanded analytics window at a time.
        existing = getattr(self, "_stats_detail_window", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.destroy()
        except Exception:
            pass

        win = ctk.CTkToplevel(self)
        self._stats_detail_window = win
        win.title(f"Dijital Denge • {translate(panel._stats_title, self.language)}")
        win.geometry("1120x760")
        win.minsize(860, 620)
        win.configure(fg_color=("#e5e7eb", "#07111f"))

        # Center the detail window.
        try:
            win.update_idletasks()
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            ww, wh = 1120, 760
            win.geometry(f"{ww}x{wh}+{max(0, (sw-ww)//2)}+{max(0, (sh-wh)//2)}")
        except Exception:
            pass

        top = ctk.CTkFrame(win, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(14, 8))

        ctk.CTkLabel(
            top,
            text=translate(panel._stats_title, self.language),
            font=("Segoe UI", 21, "bold"),
            text_color=("#0f172a", "#f8fafc")
        ).pack(side="left")

        if panel._stats_subtitle:
            ctk.CTkLabel(
                top,
                text=panel._stats_subtitle,
                font=("Segoe UI", 12),
                text_color=("#64748b", "#94a3b8")
            ).pack(side="left", padx=14)

        close_btn = ctk.CTkButton(
            top,
            text="×",
            width=36,
            height=32,
            corner_radius=9,
            font=("Segoe UI", 20, "bold"),
            fg_color=("#d1d5db", "#182c40"),
            hover_color=("#ef4444", "#b91c1c"),
            text_color=("#334155", "#f8fafc"),
            command=win.destroy
        )
        close_btn.pack(side="right")

        content = ctk.CTkFrame(
            win,
            fg_color=("#d1d5db", "#102235"),
            corner_radius=14,
            border_width=1,
            border_color=("#c3c8ce", "#1d3852")
        )
        content.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        try:
            renderer(content)
        except Exception as exc:
            log_error("İstatistik detay penceresi", exc)
            ctk.CTkLabel(
                content,
                text=f"{translate('Grafik yüklenemedi', self.language)}\n{exc}",
                font=("Segoe UI", 13),
                text_color="#ef4444"
            ).pack(expand=True)

        win.protocol("WM_DELETE_WINDOW", win.destroy)

    def _stats_category_totals(self, month_items):
        totals = {}
        for _, day in month_items:
            for cat, sec in day.get("categories", {}).items():
                totals[cat] = totals.get(cat, 0) + sec
        return dict(sorted(totals.items(), key=lambda x: x[1], reverse=True))

    def _stats_best_day_text(self, month_items):
        if not month_items:
            return translate("Veri yok", self.language)
        key, day = max(
            month_items,
            key=lambda item: item[1].get("total_active_seconds", 0)
        )
        try:
            dt = datetime.strptime(key, "%Y-%m-%d")
            return self.localized_weekday(dt)
        except Exception:
            return key[-2:]

    def _stats_best_day_subtitle(self, month_items):
        if not month_items:
            return translate("Henüz veri yok", self.language)
        _, day = max(
            month_items,
            key=lambda item: item[1].get("total_active_seconds", 0)
        )
        return format_seconds(day.get("total_active_seconds", 0), self.language)

    def _stats_top_category_text(self, month_items):
        totals = self._stats_category_totals(month_items)
        if not totals:
            return translate("Veri yok", self.language)
        return translate(next(iter(totals)), self.language)

    def _stats_top_category_subtitle(self, month_items):
        totals = self._stats_category_totals(month_items)
        if not totals:
            return translate("Henüz veri yok", self.language)
        top = next(iter(totals.values()))
        return format_seconds(top, self.language)

    def draw_stats_donut(self, parent, cat_totals, total, height=245):
        """Modern category donut with compact legend."""
        canvas = tk.Canvas(
            parent,
            height=height,
            bg="#d1d5db" if ctk.get_appearance_mode() == "Light" else "#14283d",
            highlightthickness=0
        )
        canvas.pack(fill="x", padx=8, pady=(0, 10))
        canvas.update_idletasks()

        width = max(canvas.winfo_width(), 520)
        if not cat_totals or total <= 0:
            canvas.create_text(
                width / 2,
                height / 2,
                text=translate("Henüz yeterli veri yok.", self.language),
                fill="#64748b",
                font=("Segoe UI", 12, "bold")
            )
            return

        items = list(cat_totals.items())[:7]
        other = sum(v for _, v in list(cat_totals.items())[7:])
        if other > 0:
            items.append(("Diğer", other))

        palette = [
            "#38bdf8", "#f59e0b", "#a855f7", "#22c55e",
            "#ec4899", "#14b8a6", "#818cf8", "#94a3b8"
        ]

        cx = min(105, width * 0.25)
        cy = height * 0.50
        radius = min(78, height * 0.34)
        start = -90

        for i, (_, sec) in enumerate(items):
            extent = sec / total * 360
            canvas.create_arc(
                cx - radius, cy - radius,
                cx + radius, cy + radius,
                start=start,
                extent=extent,
                fill=palette[i % len(palette)],
                outline=""
            )
            start += extent

        inner = radius * 0.58
        bg = "#d1d5db" if ctk.get_appearance_mode() == "Light" else "#14283d"
        canvas.create_oval(
            cx - inner, cy - inner,
            cx + inner, cy + inner,
            fill=bg,
            outline=""
        )
        canvas.create_text(
            cx, cy - 7,
            text=format_seconds(total, self.language),
            fill="#0f172a" if ctk.get_appearance_mode() == "Light" else "#f8fafc",
            font=("Segoe UI", 12, "bold")
        )
        canvas.create_text(
            cx, cy + 12,
            text=translate("Toplam", self.language),
            fill="#64748b",
            font=("Segoe UI", 12)
        )

        text_color = "#334155" if ctk.get_appearance_mode() == "Light" else "#cbd5e1"
        # Legend is arranged in two fixed columns.  Keeping the value in the
        # same row but in a separate column prevents long application/category
        # names from colliding with the duration/percentage text.
        legend_x = max(245, min(300, width * 0.43))
        legend_y = 28
        col_w = max(190, (width - legend_x - 18) / 2)
        row_h = 43
        for i, (name, sec) in enumerate(items):
            col, row = divmod(i, 4)
            x = legend_x + col * col_w
            y = legend_y + row * row_h
            canvas.create_oval(
                x, y, x + 9, y + 9,
                fill=palette[i % len(palette)],
                outline=""
            )
            pct = sec / total * 100
            label = translate(name, self.language)
            # Leave a dedicated value area; shorten only the display label.
            canvas.create_text(
                x + 16, y + 4,
                anchor="w",
                text=label[:14],
                fill=text_color,
                font=("Segoe UI", 11)
            )
            canvas.create_text(
                x + col_w - 6, y + 4,
                anchor="e",
                text=f"{format_short(sec)}  (%{pct:.1f})",
                fill=text_color,
                font=("Segoe UI", 10)
            )

    def draw_category_stacked_chart(self, parent, now, days, height=245):
        """Readable stacked category chart with a separated legend."""
        import calendar

        outer = ctk.CTkFrame(parent, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        canvas = tk.Canvas(
            outer,
            height=height,
            bg="#d1d5db" if ctk.get_appearance_mode() == "Light" else "#14283d",
            highlightthickness=0
        )
        canvas.pack(fill="x", expand=True)
        canvas.update_idletasks()

        width = max(canvas.winfo_width(), 700)
        day_count = calendar.monthrange(now.year, now.month)[1]
        values = []
        for day_num in range(1, day_count + 1):
            key = f"{now.year:04d}-{now.month:02d}-{day_num:02d}"
            values.append((day_num, days.get(key, {}).get("categories", {})))

        totals = [sum(cat_data.values()) for _, cat_data in values]
        max_total = max(totals + [3600])

        # More room for readable y-axis labels and a clean x-axis.
        left, right, top, bottom = 68, 18, 14, 50
        plot_w = width - left - right
        plot_h = height - top - bottom
        gap = max(3, min(6, plot_w / max(day_count * 7, 1)))
        bar_w = max(3, (plot_w - gap * (day_count - 1)) / day_count)

        grid_color = "#b7bdc4" if ctk.get_appearance_mode() == "Light" else "#243a50"
        text_color = "#475569" if ctk.get_appearance_mode() == "Light" else "#cbd5e1"

        for n in range(5):
            y = top + plot_h - n * (plot_h / 4)
            canvas.create_line(left, y, left + plot_w, y, fill=grid_color)
            value = max_total * n / 4
            canvas.create_text(
                left - 10, y,
                text=format_short(value, self.language),
                anchor="e",
                fill=text_color,
                font=("Segoe UI", 11)
            )

        categories = CATEGORY_LIST[:]
        palette = CATEGORY_COLORS

        for i, (day_num, cat_data) in enumerate(values):
            x = left + i * (bar_w + gap)
            current_y = top + plot_h
            for cat in categories:
                sec = cat_data.get(cat, 0)
                if sec <= 0:
                    continue
                bh = (sec / max_total) * plot_h
                current_y -= bh
                canvas.create_rectangle(
                    x, current_y, x + bar_w, current_y + bh,
                    fill=palette.get(cat, "#94a3b8"), outline=""
                )

            # Avoid the previous label collisions: show only useful dates.
            if day_num == 1 or day_num % 5 == 0 or day_count <= 16:
                canvas.create_text(
                    x + bar_w / 2,
                    top + plot_h + 17,
                    text=str(day_num),
                    fill=text_color,
                    font=("Segoe UI", 10)
                )

        legend_frame = ctk.CTkFrame(outer, fg_color="transparent")
        legend_frame.pack(fill="x", pady=(2, 0))

        legend = [c for c in categories if any(d.get(c, 0) for _, d in values)][:8]
        for i, cat in enumerate(legend):
            item = ctk.CTkFrame(legend_frame, fg_color="transparent")
            item.pack(side="left", padx=(2, 9))
            swatch = ctk.CTkFrame(
                item,
                width=8,
                height=8,
                corner_radius=2,
                fg_color=palette.get(cat, "#94a3b8")
            )
            swatch.pack(side="left", pady=3)
            swatch.pack_propagate(False)
            ctk.CTkLabel(
                item,
                text=translate(cat, self.language)[:12],
                font=("Segoe UI", 11),
                text_color=text_color
            ).pack(side="left", padx=(4, 0))

    def draw_top_apps_list(self, parent, month_items, total):
        """Top applications list with progress bars."""
        apps = {}
        for _, day in month_items:
            for app, sec in day.get("apps", {}).items():
                apps[app] = apps.get(app, 0) + sec

        ranked = sorted(apps.items(), key=lambda x: x[1], reverse=True)[:7]
        if not ranked:
            ctk.CTkLabel(
                parent,
                text=translate("Henüz uygulama verisi yok.", self.language),
                text_color=("#64748b", "#94a3b8")
            ).pack(pady=55)
            return

        max_sec = ranked[0][1]
        list_frame = ctk.CTkFrame(parent, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=14, pady=(1, 10))

        for i, (app, sec) in enumerate(ranked):
            row = ctk.CTkFrame(list_frame, fg_color="transparent")
            row.pack(fill="x", pady=4)

            name = app.replace(".exe", "")
            pct = sec / total * 100 if total else 0
            category = get_category(app)
            accent = CATEGORY_COLORS.get(category, "#38bdf8")

            ctk.CTkLabel(
                row,
                text=name[:17],
                width=112,
                anchor="w",
                font=("Segoe UI", 13, "bold")
            ).pack(side="left")

            bar = ctk.CTkProgressBar(
                row,
                height=7,
                progress_color=accent
            )
            bar.set(sec / max_sec if max_sec else 0)
            bar.pack(side="left", fill="x", expand=True, padx=7)

            ctk.CTkLabel(
                row,
                text=f"{format_short(sec)}  %{pct:.0f}",
                width=82,
                anchor="e",
                font=("Segoe UI", 12),
                text_color=("#64748b", "#94a3b8")
            ).pack(side="right")

    def draw_stats_trend_chart(self, parent, days, height=255):
        """Smooth-looking line/area chart for the last seven days."""
        canvas = tk.Canvas(
            parent,
            height=height,
            bg="#d1d5db" if ctk.get_appearance_mode() == "Light" else "#14283d",
            highlightthickness=0
        )
        canvas.pack(fill="x", padx=10, pady=(0, 12))
        canvas.update_idletasks()

        width = max(canvas.winfo_width(), 700)
        now = datetime.now()
        values = []

        for i in range(6, -1, -1):
            d = now - timedelta(days=i)
            key = d.strftime("%Y-%m-%d")
            values.append((d, days.get(key, {}).get("total_active_seconds", 0)))

        max_sec = max([v for _, v in values] + [3600])
        left, right, top, bottom = 58, 15, 18, 50
        plot_w = width - left - right
        plot_h = height - top - bottom

        grid_color = "#b7bdc4" if ctk.get_appearance_mode() == "Light" else "#243a50"
        text_color = "#475569" if ctk.get_appearance_mode() == "Light" else "#cbd5e1"

        for n in range(5):
            y = top + plot_h - n * (plot_h / 4)
            canvas.create_line(
                left, y, left + plot_w, y,
                fill=grid_color
            )
            if n < 4:
                canvas.create_text(
                    left - 5, y,
                    text=format_short(max_sec * n / 4),
                    anchor="e",
                    fill=text_color,
                    font=("Segoe UI", 11)
                )

        points = []
        for i, (d, sec) in enumerate(values):
            x = left + (plot_w * i / 6)
            y = top + plot_h - (sec / max_sec) * plot_h
            points.append((x, y))

        # Area under line.
        area_points = [(points[0][0], top + plot_h)] + points + [
            (points[-1][0], top + plot_h)
        ]
        canvas.create_polygon(
            area_points,
            fill="#1f5f83" if ctk.get_appearance_mode() == "Dark" else "#b9dceb",
            outline=""
        )

        if len(points) > 1:
            canvas.create_line(
                points,
                fill="#38bdf8",
                width=3,
                smooth=True
            )

        for i, (d, sec) in enumerate(values):
            x, y = points[i]
            canvas.create_oval(
                x - 3, y - 3, x + 3, y + 3,
                fill="#38bdf8",
                outline=""
            )
            canvas.create_text(
                x,
                top + plot_h + 20,
                text=self.localized_weekday(d),
                fill=text_color,
                font=("Segoe UI", 10)
            )
            if sec > 0:
                # Nokta değerlerini dönüşümlü konumlandırarak üst üste binmelerini önle.
                label_y = max(top + 10, y - (22 if i % 2 == 0 else 11))
                canvas.create_text(
                    x, label_y, text=format_short(sec),
                    fill=text_color, font=("Segoe UI", 10, "bold")
                )

    def draw_week_comparison(self, parent, days):
        """Compare the latest seven days with the previous seven days."""
        now = datetime.now()

        current = []
        previous = []

        for i in range(7):
            d = now - timedelta(days=i)
            current.append(
                days.get(d.strftime("%Y-%m-%d"), {}).get("total_active_seconds", 0)
            )

            p = now - timedelta(days=i + 7)
            previous.append(
                days.get(p.strftime("%Y-%m-%d"), {}).get("total_active_seconds", 0)
            )

        current_total = sum(current)
        previous_total = sum(previous)

        def delta_text(cur, prev):
            if prev <= 0:
                return "—"
            pct = (cur - prev) / prev * 100
            arrow = "↑" if pct >= 0 else "↓"
            return f"{arrow} %{abs(pct):.0f}"

        rows = [
            ("Toplam Kullanım", current_total, previous_total),
            ("Günlük Ortalama", current_total / 7, previous_total / 7),
            ("En Uzun Gün", max(current + [0]), max(previous + [0])),
            ("Mola Sayısı", self._week_break_count(days, 0), self._week_break_count(days, 7))
        ]

        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        for title, cur, prev in rows:
            row = ctk.CTkFrame(
                frame,
                fg_color="transparent"
            )
            row.pack(fill="x", pady=5)

            ctk.CTkLabel(
                row,
                text=translate(title, self.language),
                anchor="w",
                font=("Segoe UI", 11)
            ).pack(side="left")

            if title == "Mola Sayısı":
                cur_text = str(int(cur))
                prev_text = str(int(prev))
            else:
                cur_text = format_seconds(cur, self.language)
                prev_text = format_seconds(prev, self.language)

            ctk.CTkLabel(
                row,
                text=cur_text,
                width=75,
                anchor="e",
                font=("Segoe UI", 13, "bold")
            ).pack(side="right")

            ctk.CTkLabel(
                row,
                text=delta_text(cur, prev),
                width=55,
                anchor="e",
                text_color="#22c55e" if cur <= prev or prev == 0 else "#ef4444",
                font=("Segoe UI", 12, "bold")
            ).pack(side="right", padx=4)

            ctk.CTkLabel(
                row,
                text=translate("Geçen hafta", self.language) + ": " + prev_text,
                anchor="w",
                text_color=("#64748b", "#94a3b8"),
                font=("Segoe UI", 12)
            ).pack(side="left", padx=(7, 0))

        ctk.CTkLabel(
            frame,
            text=translate("Son 7 gün ile önceki 7 gün karşılaştırması.", self.language),
            font=("Segoe UI", 12),
            text_color=("#64748b", "#94a3b8"),
            wraplength=360,
            justify="left"
        ).pack(anchor="w", pady=(9, 0))

    def _week_break_count(self, days, offset):
        now = datetime.now()
        total = 0
        for i in range(7):
            d = now - timedelta(days=i + offset)
            item = days.get(d.strftime("%Y-%m-%d"), {})
            total += len(item.get("breaks", []))
        return total

    def draw_usage_bar_chart(self, parent, values, height=285):
        """Bar chart with dedicated top/label space so values never clip or overlap."""
        canvas = tk.Canvas(
            parent, height=height,
            bg="#bcc1c7" if ctk.get_appearance_mode() == "Light" else "#1e293b",
            highlightthickness=0
        )
        canvas.pack(fill="x", padx=20, pady=(5, 20))
        canvas.update_idletasks()
        width = max(canvas.winfo_width(), 700)
        if not values:
            return

        max_sec = max([v for _, v in values] + [3600])
        # Extra top room is intentional: the tallest bar's value is drawn above it.
        left, right, top, bottom = 58, 18, 42, 55
        plot_w = width - left - right
        plot_h = height - top - bottom
        bg = "#bcc1c7" if ctk.get_appearance_mode() == "Light" else "#1e293b"
        fg = "#334155" if ctk.get_appearance_mode() == "Light" else "#cbd5e1"
        grid = "#aeb4ba" if ctk.get_appearance_mode() == "Light" else "#334155"
        canvas.configure(bg=bg)
        canvas.create_line(left, top, left, top + plot_h, fill=grid)
        canvas.create_line(left, top + plot_h, left + plot_w, top + plot_h, fill=grid)

        count = len(values)
        gap = max(4, min(8, plot_w / max(count * 10, 1)))
        bar_w = max(5, (plot_w - gap * (count - 1)) / count)
        label_step = max(1, int((count + 6) // 7))
        value_step = max(1, int((count + 3) // 5))

        for i, (label, sec) in enumerate(values):
            x = left + i * (bar_w + gap)
            bh = (sec / max_sec) * (plot_h - 8) if max_sec else 0
            y = top + plot_h - bh
            canvas.create_rectangle(x, y, x + bar_w, top + plot_h, fill="#38bdf8", outline="")

            if i == 0 or i == count - 1 or i % label_step == 0:
                canvas.create_text(
                    x + bar_w / 2, top + plot_h + 22,
                    text=str(label), fill=fg, font=("Segoe UI", 10)
                )


            if sec > 0 and (count <= 12 or i % value_step == 0 or sec == max_sec):

                value_y = max(top + 14, y - 20)
                canvas.create_text(
                    x + bar_w / 2, value_y,
                    text=format_short(sec), fill=fg,
                    font=("Segoe UI", 10, "bold")
                )



    def draw_hourly_usage(self, parent, day_data, height=270):

        canvas = tk.Canvas(
            parent, height=height,
            bg="#bcc1c7" if ctk.get_appearance_mode() == "Light" else "#1e293b",
            highlightthickness=0
        )
        canvas.pack(fill="x", padx=20, pady=(4, 18))
        canvas.update_idletasks()
        width = max(canvas.winfo_width(), 700)
        hours = [0.0] * 24
        for item in day_data.get("sessions", []):
            try:
                start = datetime.fromisoformat(item.get("start"))
                remaining = max(0.0, float(item.get("duration_seconds", 0)))
                current = start
                while remaining > 0:
                    next_hour = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                    chunk = min(remaining, max(1.0, (next_hour - current).total_seconds()))
                    hours[current.hour] += chunk
                    remaining -= chunk
                    current = next_hour
            except Exception:
                continue

        max_sec = max(max(hours), 3600)
        left, right, top, bottom = 52, 18, 42, 50
        plot_w, plot_h = width - left - right, height - top - bottom
        grid = "#aeb4ba" if ctk.get_appearance_mode() == "Light" else "#334155"
        text = "#334155" if ctk.get_appearance_mode() == "Light" else "#cbd5e1"
        canvas.create_line(left, top, left, top + plot_h, fill=grid)
        canvas.create_line(left, top + plot_h, left + plot_w, top + plot_h, fill=grid)

        gap = 3
        bar_w = max(7, (plot_w - gap * 23) / 24)
        max_hour = max(range(24), key=lambda i: hours[i])
        for h, sec in enumerate(hours):
            x = left + h * (bar_w + gap)
            bh = (sec / max_sec) * (plot_h - 8)
            y = top + plot_h - bh
            canvas.create_rectangle(x, y, x + bar_w, top + plot_h, fill="#38bdf8", outline="")

            if h % 3 == 0:
                canvas.create_text(
                    x + bar_w / 2, top + plot_h + 22,
                    text=f"{h:02d}", fill=text, font=("Segoe UI", 10)
                )
            if sec > 0 and (h == max_hour or h % 3 == 0):
                canvas.create_text(
                    x + bar_w / 2, max(15, y - 12),
                    text=format_short(sec), fill=text,
                    font=("Segoe UI", 10, "bold")
                )

        canvas.create_text(
            left + 5, top - 10, text=format_short(max_sec),
            anchor="w", fill=text, font=("Segoe UI", 11)
        )

    def draw_app_pie_chart(self, parent, apps, total, height=300):

        canvas = tk.Canvas(parent, height=height, bg="#d6d9dd" if ctk.get_appearance_mode() == "Light" else "#172033", highlightthickness=0)
        canvas.pack(fill="x", padx=10, pady=(0, 12))
        canvas.update_idletasks()
        width = max(canvas.winfo_width(), 700)
        top_apps = apps[:8]
        if len(apps) > 8:
            other = sum(v for _, v in apps[8:])
            if other > 0: top_apps.append(("Diğer", other))
        if not top_apps or total <= 0:
            canvas.create_text(width/2, height/2, text=translate("Bu tarihte uygulama kullanımı bulunmuyor.", self.language), fill="#64748b", font=("Segoe UI", 12, "bold"))
            return
        cx, cy, r = min(150, width*0.23), height/2, min(105, height*0.35)
        palette = ["#38bdf8", "#22c55e", "#f59e0b", "#a855f7", "#ec4899", "#14b8a6", "#818cf8", "#f472b6", "#94a3b8"]
        start_angle = -90
        for i, (name, sec) in enumerate(top_apps):
            extent = sec/total*360
            canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=start_angle, extent=extent, fill=palette[i%len(palette)], outline="")
            start_angle += extent
        inner = r*0.55
        canvas.create_oval(cx-inner, cy-inner, cx+inner, cy+inner, fill="#d6d9dd" if ctk.get_appearance_mode()=="Light" else "#172033", outline="")
        canvas.create_text(cx, cy-8, text=format_seconds(total), fill="#0f172a" if ctk.get_appearance_mode()=="Light" else "#e7e9ec", font=("Segoe UI", 13, "bold"))
        canvas.create_text(cx, cy+12, text=translate("Toplam", self.language), fill="#475569" if ctk.get_appearance_mode()=="Light" else "#94a3b8", font=("Segoe UI", 11))
        legend_x, legend_y = min(320, width * 0.45), 28
        text = "#334155" if ctk.get_appearance_mode()=="Light" else "#cbd5e1"
        col_w = max(185, (width - legend_x - 18) / 2)
        for i, (name, sec) in enumerate(top_apps):
            col, row = divmod(i, 5)
            x, y = legend_x + col * col_w, legend_y + row * 43
            canvas.create_rectangle(x, y, x+10, y+10, fill=palette[i%len(palette)], outline="")
            pct = sec/total*100
            canvas.create_text(
                x+16, y+5, anchor="w", text=name[:14],
                fill=text, font=("Segoe UI", 10)
            )
            canvas.create_text(
                x+col_w-6, y+5, anchor="e",
                text=f"{format_short(sec)}  (%{pct:.1f})",
                fill=text, font=("Segoe UI", 9)
            )

    def show_selected_day(self, parent, date_key, days):
        if not date_key:
            return
        d = days.get(date_key, yeni_gun())
        try:
            date_obj = datetime.strptime(date_key, "%Y-%m-%d")
            title = date_obj.strftime("%d.%m.%Y")
            weekday = self.localized_weekday(date_obj)
        except Exception:
            title, weekday = date_key, ""
        total = d.get("total_active_seconds", 0)
        box = ctk.CTkFrame(parent, fg_color=("#bcc1c7", "#1e293b"), corner_radius=14)
        box.pack(fill="x", padx=5, pady=8)
        ctk.CTkLabel(box, text=f"{translate('SEÇİLEN TARİH', self.language)} • {title} • {weekday}", font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=20, pady=(18, 8))
        ctk.CTkLabel(box, text=f"{translate('Toplam aktif kullanım', self.language)}: {format_seconds(total)}   •   {translate('En uzun kesintisiz kullanım', self.language)}: {format_seconds(d.get('longest_continuous_seconds', 0))}   •   {translate('Mola', self.language)}: {len(d.get('breaks', []))}", text_color=("#64748b", "#94a3b8")).pack(anchor="w", padx=20, pady=(0, 14))

        # Uygulama kullanımı — daire grafik + tablo
        apps = sorted(d.get("apps", {}).items(), key=lambda x: x[1], reverse=True)
        pie_box = ctk.CTkFrame(box, fg_color=("#d6d9dd", "#172033"), corner_radius=12)
        pie_box.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkLabel(pie_box, text=translate("UYGULAMA KULLANIMI • DAİRE GRAFİĞİ", self.language), font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        self.draw_app_pie_chart(pie_box, apps, total)

        # Uygulama tablosu
        apps = apps
        ctk.CTkLabel(box, text=translate("UYGULAMA KULLANIMI", self.language), font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=20, pady=(4, 6))
        table = ctk.CTkFrame(box, fg_color=("#d6d9dd", "#172033"), corner_radius=10)
        table.pack(fill="x", padx=20, pady=(0, 12))
        headers = [(translate("Uygulama",self.language), 0), (translate("Kategori",self.language), 1), (translate("Süre",self.language), 2), (translate("Pay",self.language), 3)]
        for text, col in headers:
            table.grid_columnconfigure(col, weight=1)
            ctk.CTkLabel(table, text=text, font=("Segoe UI", 12, "bold"), text_color=("#64748b", "#94a3b8")).grid(row=0, column=col, sticky="w", padx=12, pady=9)
        if apps:
            for r, (app, sec) in enumerate(apps[:25], 1):
                share = sec / total if total else 0
                vals = [app.replace(".exe", ""), translate(get_category(app),self.language), format_seconds(sec), f"%{share*100:.1f}"]
                for col, val in enumerate(vals):
                    ctk.CTkLabel(table, text=val, font=("Segoe UI", 12)).grid(row=r, column=col, sticky="w", padx=12, pady=6)
        else:
            ctk.CTkLabel(table, text=translate("Bu tarihte kayıtlı uygulama verisi yok.", self.language), text_color=("#64748b", "#94a3b8")).grid(row=1, column=0, columnspan=4, padx=12, pady=18)

        # Oturum tablosu
        sessions = d.get("sessions", [])
        ctk.CTkLabel(box, text=translate("OTURUMLAR", self.language), font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=20, pady=(4, 6))
        sess = ctk.CTkFrame(box, fg_color=("#d6d9dd", "#172033"), corner_radius=10)
        sess.pack(fill="x", padx=20, pady=(0, 20))
        for col, weight in enumerate((1, 3, 2, 2)):
            sess.grid_columnconfigure(col, weight=weight)
        for col, text in enumerate([translate(x,self.language) for x in ["Saat", "Uygulama / Oturum", "Kategori", "Süre"]]):
            ctk.CTkLabel(sess, text=text, font=("Segoe UI", 12, "bold"), text_color=("#64748b", "#94a3b8")).grid(row=0, column=col, sticky="w", padx=10, pady=9)
        if sessions:
            for r, item in enumerate(reversed(sessions[-30:]), 1):
                start = item.get("start")
                clock = "--:--"
                if start:
                    try: clock = datetime.fromisoformat(start).strftime("%H:%M")
                    except Exception: pass
                name = item.get("app") or translate("Kesintisiz bilgisayar kullanımı", self.language); name = name.replace(".exe", "")
                cat = translate(item.get("category", "Sistem"),self.language)
                vals = [clock, name, cat, format_seconds(item.get("duration_seconds", 0))]
                for col, val in enumerate(vals):
                    ctk.CTkLabel(sess, text=val).grid(row=r, column=col, sticky="w", padx=10, pady=5)
        else:
            ctk.CTkLabel(sess, text=translate("Bu tarihte oturum kaydı yok.", self.language), text_color=("#64748b", "#94a3b8")).grid(row=1, column=0, columnspan=4, padx=10, pady=18)

    def draw_month_calendar(self, parent, now, days, selected_key=None):

        import calendar
        is_light = ctk.get_appearance_mode() == "Light"
        names = [translate(x, self.language) for x in ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]]
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(now.year, now.month)

        outer = ctk.CTkFrame(parent, fg_color="transparent")
        outer.pack(fill="x", padx=15, pady=(0, 18))

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")
        for col, name in enumerate(names):
            header.grid_columnconfigure(col, weight=1)
            ctk.CTkLabel(
                header, text=name, text_color=("#475569", "#94a3b8"),
                font=("Segoe UI", 13, "bold")
            ).grid(row=0, column=col, padx=4, pady=5, sticky="nsew")

        row_h = 82
        canvas = tk.Canvas(
            outer, height=row_h * len(weeks),
            bg="#d1d5db" if is_light else "#14283d",
            highlightthickness=0
        )
        canvas.pack(fill="x")
        canvas.update_idletasks()
        width = max(canvas.winfo_width(), 700)
        col_w = width / 7

        # Fetched once for the whole grid instead of once per day cell.
        goal = max(1, int(get_settings().get("daily_goal_seconds", 6 * 3600)))
        day_text_color = "#111827" if is_light else "#000000"
        sub_text_color = "#334155" if is_light else "#cbd5e1"
        track_color = "#aeb4ba" if is_light else "#334155"
        border_color = "#8b949e" if is_light else "#475569"

        cells = []  # (x0, y0, x1, y1, key) for click hit-testing
        for r, week in enumerate(weeks):
            for c, day_num in enumerate(week):
                if not day_num:
                    continue
                x0, y0 = c * col_w + 4, r * row_h + 4
                x1, y1 = x0 + col_w - 8, y0 + row_h - 8
                key = f"{now.year:04d}-{now.month:02d}-{day_num:02d}"
                sec = days.get(key, {}).get("total_active_seconds", 0)
                ratio = min(1.0, sec / goal)
                if sec <= 0:
                    cell_fg, accent = "#d6d9dd", "#64748b"
                elif ratio < 0.50:
                    cell_fg, accent = "#cfe8d5", "#16a34a"
                elif ratio < 0.80:
                    cell_fg, accent = "#eee4b4", "#ca8a04"
                else:
                    cell_fg, accent = "#eccaca", "#dc2626"
                if key == selected_key:
                    cell_fg = "#c1d5e6" if is_light else "#1e3a5f"

                canvas.create_rectangle(x0, y0, x1, y1, fill=cell_fg, outline=border_color, width=2)
                canvas.create_text(
                    x0 + (x1 - x0) / 2, y0 + 19,
                    text=str(day_num), fill=day_text_color, font=("Segoe UI", 15, "bold")
                )
                canvas.create_text(
                    x0 + (x1 - x0) / 2, y0 + 44,
                    text=(format_short(sec) if sec else translate("0 dk", self.language)),
                    fill=sub_text_color, font=("Segoe UI", 12, "bold")
                )
                track_y = y1 - 13
                canvas.create_rectangle(x0 + 9, track_y, x1 - 9, track_y + 7, fill=track_color, outline="")
                fill_w = max(2, (x1 - x0 - 18) * ratio)
                canvas.create_rectangle(x0 + 9, track_y, x0 + 9 + fill_w, track_y + 7, fill=accent, outline="")
                cells.append((x0, y0, x1, y1, key))

        def on_click(event):
            for x0, y0, x1, y1, key in cells:
                if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                    self.show_stats(0, key)
                    return
        canvas.bind("<Button-1>", on_click)

    def show_settings(self):
        self._begin_page_build("settings"); self.page_title.configure(text=translate("Ayarlar", self.language)); s=get_settings()
        themef=ctk.CTkFrame(self.content,fg_color=("#bcc1c7","#1e293b"),corner_radius=14); themef.pack(fill="x",padx=5,pady=8)
        ctk.CTkLabel(themef,text=translate("GÖRÜNÜM",self.language),font=("Segoe UI", 17,"bold")).pack(anchor="w",padx=20,pady=(18,8))
        ctk.CTkLabel(themef,text=translate("Uygulamanın aydınlık, karanlık veya Windows sistem temasını kullanmasını seç.", self.language),text_color=("#64748b","#94a3b8")).pack(anchor="w",padx=20,pady=(0,8))
        ctk.CTkLabel(themef,text=translate("Dil",self.language),font=("Segoe UI", 15,"bold")).pack(anchor="w",padx=20,pady=(8,4))
        lang_combo=ctk.CTkComboBox(themef,values=LANGUAGE_OPTIONS,width=220); lang_combo.set(self.language); lang_combo.pack(anchor="w",padx=20,pady=(0,12)); lang_combo.configure(command=self.save_language)
        theme_values=[translate(x,self.language) for x in ["Karanlık","Aydınlık","Sistem"]]; theme_combo=ctk.CTkComboBox(themef,values=theme_values,width=180); theme_map={"dark":"Karanlık","light":"Aydınlık","system":"Sistem"}; current_theme=theme_map.get(s.get("theme","dark"),"Karanlık"); theme_combo.set(translate(current_theme,self.language)); theme_combo.pack(anchor="w",padx=20,pady=(0,18)); theme_combo.configure(command=lambda v:self.save_theme(next((k for k in theme_map if translate(theme_map[k],self.language)==v),"dark")))
        # Windows ile başlatma seçeneği
        startupf = ctk.CTkFrame(
            themef,
            fg_color=("#c5cbd1", "#162a40"),
            corner_radius=12,
            border_width=1,
            border_color=("#b7bec6", "#223d58")
        )
        startupf.pack(fill="x", padx=20, pady=(0, 18))

        startup_row = ctk.CTkFrame(startupf, fg_color="transparent")
        startup_row.pack(fill="x", padx=14, pady=12)

        startup_info = ctk.CTkFrame(startup_row, fg_color="transparent")
        startup_info.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            startup_info,
            text=translate("Windows ile başlat", self.language),
            font=("Segoe UI", 13, "bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            startup_info,
            text=translate("Bilgisayar açıldığında Dijital Denge otomatik olarak çalışsın.", self.language),
            font=("Segoe UI", 10),
            text_color=("#64748b", "#94a3b8")
        ).pack(anchor="w", pady=(2, 0))

        startup_switch = ctk.CTkSwitch(
            startup_row,
            text="",
            width=46,
            command=lambda sw=None: self.save_windows_startup(bool(startup_switch.get()))
        )
        startup_switch.pack(side="right", padx=(12, 0))
        if is_windows_startup_enabled():
            startup_switch.select()
        else:
            startup_switch.deselect()
        self.setting_int(self.content,"MOLA","Boşta kalma süresi (dakika)",max(1,int(s.get("idle_threshold_seconds",180)/60)),1,120,self.save_idle)
        self.setting_int(self.content,"MOLA","Mola hatırlatıcısı (dakika)",int(s.get("reminder_minutes",45)),5,240,self.save_reminder_minutes)
        self.switch_setting(self.content,"MOLA","Mola hatırlatıcısını etkinleştir",s.get("reminder_enabled",True),self.save_reminder_enabled)
        self.switch_setting(self.content,"MOLA","Sesli uyarı",s.get("sound_enabled",True),self.save_sound)
        self.switch_setting(self.content,"GİZLİLİK","Web takibi (yalnızca pencere başlığı; URL kaydı yok)",s.get("web_tracking_enabled",False),self.save_web)
        self.setting_int(self.content,"HEDEFLER","Günlük aktif kullanım hedefi (saat)",int(s.get("daily_goal_seconds",21600)/3600),1,24,self.save_daily_goal)
        catf=ctk.CTkFrame(self.content,fg_color=("#bcc1c7","#1e293b"),corner_radius=14); catf.pack(fill="x",padx=5,pady=10)
        ctk.CTkLabel(catf,text=translate("KATEGORİLER",self.language),font=("Segoe UI", 18,"bold")).pack(anchor="w",padx=20,pady=(18,8))
        ctk.CTkLabel(catf,text=translate("Otomatik kategorilere ek olarak kendi kategorilerini oluşturabilir ve Uygulamalar sayfasında uygulamalara atayabilirsin.", self.language),wraplength=800,justify="left",text_color=("#64748b","#94a3b8")).pack(anchor="w",padx=20,pady=(0,10))
        ctk.CTkButton(catf,text=translate("＋ Yeni Kategori", self.language),command=self.add_category_dialog).pack(anchor="w",padx=20,pady=(0,18))
        info=ctk.CTkFrame(self.content,fg_color=("#bcc1c7","#1e293b"),corner_radius=14); info.pack(fill="x",padx=5,pady=10); ctk.CTkLabel(info,text=translate("VERİ",self.language),font=("Segoe UI", 18,"bold")).pack(anchor="w",padx=20,pady=(18,5)); ctk.CTkLabel(info,text=f"{translate('Yerel dosya:', self.language)}\n{DATA_FILE}\n\n{translate('Log:', self.language)}\n{LOG_FILE}",text_color=("#64748b","#94a3b8"),justify="left").pack(anchor="w",padx=20,pady=(0,12)); ctk.CTkButton(info,text=translate("Verileri dışa aktar (JSON)", self.language),command=self.export_json).pack(anchor="w",padx=20,pady=5); resetf = ctk.CTkFrame(info, fg_color=("#c5cbd1", "#162a40"), corner_radius=12, border_width=1, border_color=("#b7bec6", "#223d58"))
        resetf.pack(fill="x", padx=20, pady=(12,20))
        ctk.CTkLabel(resetf, text=translate("VERİ SIFIRLAMA", self.language), font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=14, pady=(13,3))
        ctk.CTkLabel(resetf, text=translate("Aylık Veri Sıfırlama", self.language), font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(8,2))
        ctk.CTkLabel(resetf, text=translate("Aşağıdaki aydaki tüm kullanım kayıtlarını sil", self.language), font=("Segoe UI", 10), text_color=("#64748b", "#94a3b8")).pack(anchor="w", padx=14, pady=(0,7))
        month_row = ctk.CTkFrame(resetf, fg_color="transparent")
        month_row.pack(fill="x", padx=14, pady=(0,8))
        ctk.CTkLabel(month_row, text=translate("Ay seçin", self.language)).pack(side="left")
        month_values = self.get_available_months()
        month_combo = ctk.CTkComboBox(month_row, values=month_values, width=150)
        current_month = datetime.now().strftime("%Y-%m")
        month_combo.set(current_month if current_month in month_values else month_values[0])
        month_combo.pack(side="right")
        ctk.CTkButton(resetf, text=translate("Seçilen Ayın Verilerini Sıfırla", self.language), fg_color="#b45309", hover_color="#92400e", command=lambda: self.reset_selected_month(month_combo.get())).pack(anchor="w", padx=14, pady=(0,13))
        ctk.CTkLabel(resetf, text=translate("Tüm Verileri Sıfırla", self.language), font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(2,2))
        ctk.CTkLabel(resetf, text=translate("Sistemde tutulan tüm kullanım verileri silinecek. Ayarlar korunur ve bu işlem geri alınamaz.", self.language), font=("Segoe UI", 10), text_color=("#64748b", "#94a3b8"), wraplength=760, justify="left").pack(anchor="w", padx=14, pady=(0,7))
        ctk.CTkButton(resetf, text=translate("Tüm Verileri Sıfırla", self.language), fg_color="#7f1d1d", hover_color="#991b1b", command=self.reset_all_usage_data).pack(anchor="w", padx=14, pady=(0,15))

    def setting_int(self,parent,section,label,value,minv,maxv,callback):
        f=ctk.CTkFrame(parent,fg_color=("#bcc1c7","#1e293b"),corner_radius=14); f.pack(fill="x",padx=5,pady=5); ctk.CTkLabel(f,text=translate(section,self.language),font=("Segoe UI", 17,"bold")).pack(anchor="w",padx=20,pady=(15,5)); row=ctk.CTkFrame(f,fg_color="transparent"); row.pack(fill="x",padx=20,pady=(5,15)); ctk.CTkLabel(row,text=translate(label,self.language)).pack(side="left"); e=ctk.CTkEntry(row,width=100); e.insert(0,str(value)); e.pack(side="right"); ctk.CTkButton(row,text=translate("Kaydet", self.language),width=90,command=lambda e=e:callback(e.get())).pack(side="right",padx=8)

    def switch_setting(self,parent,section,label,value,callback):
        f=ctk.CTkFrame(parent,fg_color=("#bcc1c7","#1e293b"),corner_radius=14); f.pack(fill="x",padx=5,pady=5); sw=ctk.CTkSwitch(f,text=translate(label,self.language)); sw.configure(command=lambda: callback(bool(sw.get()))); sw.pack(anchor="w",padx=20,pady=18); sw.select() if value else sw.deselect()

    def save_windows_startup(self, enabled):
        """Windows başlangıç ayarını checkbox durumuna göre güncelle."""
        if set_windows_startup(enabled):
            veri_diske_yaz()

    def save_idle(self,x):
        try: update_settings(idle_threshold_seconds=max(60,min(7200,int(float(x))*60))); veri_diske_yaz(); self.show_settings()
        except Exception: pass
    def save_reminder_minutes(self,x):
        try: update_settings(reminder_minutes=max(5,min(240,int(float(x))))); veri_diske_yaz(); self.show_settings()
        except Exception: pass
    def save_daily_goal(self,x):
        try: update_settings(daily_goal_seconds=max(3600,min(24*3600,int(float(x))*3600))); veri_diske_yaz(); self.show_settings()
        except Exception: pass
    def save_reminder_enabled(self,x): update_settings(reminder_enabled=x); veri_diske_yaz()
    def save_sound(self,x): update_settings(sound_enabled=x); veri_diske_yaz()
    def save_web(self,x): update_settings(web_tracking_enabled=x); veri_diske_yaz()

    def save_language(self, language):
        if language not in LANGUAGE_OPTIONS:
            return
        self.language = language
        global CURRENT_LANGUAGE
        CURRENT_LANGUAGE = language
        update_settings(language=language); veri_diske_yaz()
        labels={"dashboard":"Genel Bakış","apps":"Uygulamalar","sessions":"Oturumlar","stats":"İstatistikler","settings":"Ayarlar"}
        for key in self.nav_text_labels:
            self.nav_text_labels[key].configure(text=translate(labels[key], self.language))
        for key in self.nav_buttons:
            self._set_nav_active(key, key == self.current_page)
        self.nav_label.configure(text=translate("MENÜ", self.language))
        self.sidebar_bottom.configure(text=translate("V3.0\nYerel veri • Gizlilik öncelikli", self.language))


        for page in self._page_built:
            self._page_built[page] = False
        self._dashboard_widgets = {}
        self.dash_widgets = {}


        if getattr(self, "_refresh_after_id", None):
            try: self.after_cancel(self._refresh_after_id)
            except Exception: pass
        self._refresh_after_id = self.after(150, self.guncelle)

    def save_theme(self, theme):
        theme = theme.lower()
        if theme not in ("light", "dark", "system"):
            theme = "dark"
        update_settings(theme=theme); veri_diske_yaz()
        ctk.set_appearance_mode(theme)

        if getattr(self, "_refresh_after_id", None):
            try: self.after_cancel(self._refresh_after_id)
            except Exception: pass
        self._refresh_after_id = self.after(150, self.guncelle)

    def export_json(self):
        try:
            import tkinter.filedialog as fd
            path=fd.asksaveasfilename(defaultextension=".json",filetypes=[("JSON","*.json")],initialfile="dijital_denge_icon2.json")
            if path:
                with open(path,"w",encoding="utf-8") as f: json.dump(get_db_snapshot(),f,ensure_ascii=False,indent=2)
        except Exception as e: log_error("Dışa aktarma",e)

    def get_available_months(self):
        months = {datetime.now().strftime("%Y-%m")}
        try:
            with data_lock:
                months.update(k[:7] for k in bellek_db.get("days", {}) if isinstance(k, str) and len(k) >= 7)
            archived = _load_archived_days()
            months.update(k[:7] for k in archived if isinstance(k, str) and len(k) >= 7)
        except Exception as e:
            log_error("Ay listesi", e)
        return sorted(months, reverse=True)

    def _rewrite_archive(self, remaining):
        global _archived_days_cache
        try:
            if remaining:
                fd, temp_file = tempfile.mkstemp(prefix="arsiv_", suffix=".tmp", dir=DATA_DIR)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(remaining, f, ensure_ascii=False, separators=(",", ":"))
                        f.flush(); os.fsync(f.fileno())
                    os.replace(temp_file, ARCHIVE_FILE)
                finally:
                    if os.path.exists(temp_file): os.remove(temp_file)
            elif os.path.exists(ARCHIVE_FILE):
                os.remove(ARCHIVE_FILE)
            _archived_days_cache = remaining
            return True
        except Exception as e:
            log_error("Arşiv güncelleme", e)
            return False

    def reset_selected_month(self, month_key):
        if not re.fullmatch(r"\d{4}-\d{2}", str(month_key or "")): return
        from tkinter import messagebox
        if not messagebox.askyesno(translate("Aylık Veri Sıfırlama", self.language), translate("Seçilen ayın tüm kullanım verileri silinecek. Bu işlem geri alınamaz.", self.language)): return
        try:
            with data_lock:
                days = bellek_db.setdefault("days", {})
                for key in list(days.keys()):
                    if key.startswith(month_key + "-"): del days[key]
            archived = _load_archived_days()
            remaining = {k:v for k,v in archived.items() if not (isinstance(k,str) and k.startswith(month_key + "-"))}
            self._rewrite_archive(remaining)
            veri_diske_yaz(); self.guncelle()
        except Exception as e:
            log_error("Aylık veri sıfırlama", e)

    def reset_all_usage_data(self):
        if not double_confirm_reset(
            self,
            translate("Tüm Verileri Sıfırla", self.language),
            translate("Sistemde tutulan TÜM kullanım verileri silinecek.", self.language),
            translate("Tüm kullanım geçmişini kalıcı olarak silmek istediğinizden emin misiniz?", self.language)
        ):
            return

        global _archived_days_cache
        try:
            with data_lock: bellek_db["days"] = {}
            _archived_days_cache = {}
            if os.path.exists(ARCHIVE_FILE):
                try: os.remove(ARCHIVE_FILE)
                except OSError as e: log_error("Arşiv silme", e)
            veri_diske_yaz(); self.guncelle()
        except Exception as e:
            log_error("Tüm kullanım verilerini sıfırlama", e)

    def reset_data(self):
        self.reset_all_usage_data()

    def auto_refresh(self):
        try: self.update_dashboard_live()
        except Exception as e: log_error("Arayüz yenileme",e)
        self.after(5000,self.auto_refresh)
    def guncelle(self):
        self._refresh_after_id = None
        getattr(self,{"dashboard":"show_dashboard","apps":"show_apps","sessions":"show_sessions","stats":"show_stats","settings":"show_settings"}[self.current_page])()
    def kucult_tepsiye(self): self.withdraw()
    def goster(self): self.deiconify(); self.lift(); self.focus_force(); self.guncelle()


# ============================================================
# CUSTOM WINDOWS SYSTEM TRAY + MODERN POPUP
# ============================================================

WM_APP = 0x8000
TRAY_CALLBACK = WM_APP + 101
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WS_EX_TOOLWINDOW = 0x00000080
HWND_MESSAGE = ctypes.c_void_p(-3)
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", wintypes.LPVOID),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class CustomWindowsTray:
    """Win32 tray icon + CustomTkinter popup."""

    def __init__(self, app_instance):
        self.app = app_instance
        self.hwnd = None
        self.hicon = None
        self._class_name = "DijitalDengeTrayWindow"
        self._wndproc = None
        self._thread_id = None
        self._running = True

    def run(self):
        if os.name != "nt":
            return
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32


        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.DefWindowProcW.restype = ctypes.c_longlong
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)
        ]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        user32.LoadIconW.restype = wintypes.HICON
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL

        self._thread_id = kernel32.GetCurrentThreadId()
        hinstance = kernel32.GetModuleHandleW(None)

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM
        )

        def wnd_proc(hwnd, msg, wparam, lparam):

            try:
                if msg == TRAY_CALLBACK:
                    if lparam in (WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK):
                        point = wintypes.POINT()
                        user32.GetCursorPos(ctypes.byref(point))
                        # If the app is already shutting down (or has been
                        # destroyed), don't schedule a popup against a
                        # window that may no longer exist.
                        if self._running:
                            try:
                                self.app.after(0, self.app.show_tray_popup, point.x, point.y)
                            except Exception as e:
                                log_error("Tepsi popup zamanlama", e)
                    return 0
                if msg == WM_DESTROY:
                    return 0
            except Exception as e:
                log_error("Tepsi wnd_proc", e)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wnd_proc)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._wndproc, wintypes.LPVOID)
        wc.hInstance = hinstance
        wc.lpszClassName = self._class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            log_error("Tepsi simgesi", OSError(f"RegisterClassW failed: {ctypes.get_last_error()}"))
            return

        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW,
            self._class_name,
            "Dijital Denge Tray",
            0, 0, 0, 0, 0,
            HWND_MESSAGE, 0, hinstance, None
        )
        if not self.hwnd:
            log_error("Tepsi simgesi", OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}"))
            return

        icon_path = os.path.join(BASE_DIR, "assets", "dijital_denge_icon2.ico")
        if not os.path.exists(icon_path):
            icon_path = os.path.join(BASE_DIR, "assets", "dijital_denge_icon.ico")

        if os.path.exists(icon_path):
            self.hicon = user32.LoadImageW(
                None, icon_path, IMAGE_ICON, 32, 32,
                LR_LOADFROMFILE | LR_DEFAULTSIZE
            )

        if not self.hicon:
            self.hicon = user32.LoadIconW(None, ctypes.c_void_p(32512))

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = TRAY_CALLBACK
        nid.hIcon = self.hicon
        nid.szTip = "Dijital Denge"
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

        msg = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self.remove()

    def notify(self, message, title="Dijital Denge"):
        """Windows bildirim alanında gerçek balon bildirimi gösterir."""
        if os.name != "nt" or not self.hwnd:
            return False
        try:
            shell32 = ctypes.windll.shell32
            shell32.Shell_NotifyIconW.argtypes = [
                wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)
            ]
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL

            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_INFO
            nid.szInfo = str(message)[:255]
            nid.szInfoTitle = str(title)[:63]
            nid.dwInfoFlags = NIIF_INFO

            result = shell32.Shell_NotifyIconW(
                NIM_MODIFY, ctypes.byref(nid)
            )
            return bool(result)
        except Exception as e:
            log_error("Windows bildirimi", e)
            return False

    def remove(self):
        if os.name != "nt" or not self.hwnd:
            return
        try:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            ctypes.windll.user32.DestroyWindow(self.hwnd)
        except Exception as e:
            log_error("Tepsi simgesi kapatma", e)
        finally:
            self.hwnd = None

    def stop(self):
        self._running = False
        try:
            if self._thread_id:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass


def _tepsi_kullanim_metni():
    try:
        with data_lock:
            today = bellek_db.get("days", {}).get(datetime.now().strftime("%Y-%m-%d"), {})
            seconds = int(today.get("total_active_seconds", 0))
        saat = seconds // 3600
        dakika = (seconds % 3600) // 60
        if saat:
            return f"{saat} sa {dakika} dk"
        return f"{dakika} dk"
    except Exception:
        return "0 dk"


def _tray_icon_image():
    icon_path = os.path.join(BASE_DIR, "assets", "dijital_denge_icon2.png")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(BASE_DIR, "assets", "dijital_denge_logo.png")
    try:
        return Image.open(icon_path).convert("RGBA")
    except Exception:
        return None


def _tray_button(parent, text, icon_text, command, icon_color, danger=False):

    row = ctk.CTkButton(
        parent,
        text=f"{icon_text}    {text}",
        command=command,
        anchor="w",
        height=42,
        corner_radius=10,
        border_width=0,
        fg_color="#17283b",
        hover_color="#20364d",
        text_color="#f4f8fc",
        font=(MODERN_FONT_FAMILY, 13, "bold" if danger else "normal"),
    )
    row.pack(fill="x", padx=18, pady=4)
    return row


# Popup'u DashboardApp sınıfına ekliyoruz.
def _show_tray_popup(self, x=None, y=None):

    try:
        if not self.winfo_exists():
            return
    except Exception:
        return

    old = getattr(self, "_tray_popup", None)
    if old is not None:
        try:
            if old.winfo_exists():
                old.destroy()
        except Exception:
            pass

    try:
        popup = ctk.CTkToplevel(self)
    except Exception as e:
        log_error("Tepsi popup açma", e)
        return
    self._tray_popup = popup
    popup.overrideredirect(True)
    popup.attributes("-topmost", True)
    popup.configure(fg_color="#0d1826")
    popup.geometry("320x300")


    popup.update_idletasks()
    sw, sh = popup.winfo_screenwidth(), popup.winfo_screenheight()
    px = int(x if x is not None else sw - 340)
    py = int(y if y is not None else sh - 360)
    px = max(8, min(px - 150, sw - 328))
    py = max(8, min(py - 315, sh - 308))
    popup.geometry(f"320x300+{px}+{py}")


    outer = ctk.CTkFrame(
        popup, corner_radius=18, fg_color="#0f1d2d",
        border_width=1, border_color="#263b52"
    )
    outer.pack(fill="both", expand=True)

    header = ctk.CTkFrame(outer, fg_color="transparent", height=84)
    header.pack(fill="x", padx=18, pady=(14, 0))
    header.grid_columnconfigure(1, weight=1)

    img = _tray_icon_image()
    if img is not None:
        try:
            icon_ctk = ctk.CTkImage(light_image=img, dark_image=img, size=(42, 42))
            ctk.CTkLabel(header, text="", image=icon_ctk).grid(row=0, column=0, rowspan=2, padx=(0, 12), sticky="w")
            popup._tray_icon_ref = icon_ctk
        except Exception:
            pass

    ctk.CTkLabel(
        header, text="Dijital Denge", text_color="#f5f9fd",
        font=(MODERN_FONT_FAMILY, 15, "bold")
    ).grid(row=0, column=1, sticky="sw")

    usage = _tepsi_kullanim_metni()
    ctk.CTkLabel(
        header, text=translate("Bugünkü kullanım", self.language) + ":",
        text_color="#91a5ba", font=(MODERN_FONT_FAMILY, 10)
    ).grid(row=1, column=1, sticky="nw", pady=(2, 0))

    ctk.CTkLabel(
        header, text=usage, text_color="#f5f9fd",
        font=(MODERN_FONT_FAMILY, 14, "bold")
    ).grid(row=1, column=1, sticky="ne", pady=(0, 0))

    close = ctk.CTkButton(
        header, text="×", width=30, height=30, corner_radius=15,
        fg_color="transparent", hover_color="#25374b",
        text_color="#71859a", font=(MODERN_FONT_FAMILY, 20),
        command=popup.destroy
    )
    close.grid(row=0, column=2, rowspan=2, padx=(10, 0), sticky="ne")

    ctk.CTkFrame(outer, height=1, fg_color="#263b52").pack(fill="x", padx=0, pady=(2, 12))

    body = ctk.CTkFrame(outer, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=0, pady=(0, 12))

    def open_panel():
        try: popup.destroy()
        except Exception: pass
        self.goster()

    def open_settings():
        try: popup.destroy()
        except Exception: pass
        self.deiconify()
        self.lift()
        self.focus_force()
        self.current_page = "settings"
        self.guncelle()

    def close_app():
        global calisiyor
        try: popup.destroy()
        except Exception: pass
        calisiyor = False
        shutdown_event.set()
        try:

            tracker_thread = getattr(self, "_tracker_thread", None)
            if tracker_thread is not None:
                tracker_thread.join(timeout=3)
            veri_diske_yaz()
        except Exception as e:
            log_error("Tepsi kapanışı", e)
        tray = globals().get("tray_icon_ref", {}).get("icon")
        if tray is not None:
            try: tray.stop()
            except Exception: pass
        self.after(50, self.destroy)

    _tray_button(body, translate("Paneli Aç", self.language), "▥", open_panel, "#38bdf8")
    _tray_button(body, translate("Ayarlar", self.language), "⚙", open_settings, "#9db2c7")
    _tray_button(body, translate("Tamamen Kapat", self.language), "⏻", close_app, "#fb7185", danger=True)


    def on_focus_out(event=None):
        try:
            if popup.winfo_exists():
                popup.destroy()
        except Exception:
            pass
    popup.bind("<FocusOut>", on_focus_out)
    popup.bind("<Escape>", lambda e: popup.destroy())
    popup.focus_force()


DashboardApp.show_tray_popup = _show_tray_popup


def tepsi_simgesi_olustur(app_instance):

    tray = CustomWindowsTray(app_instance)
    tray_icon_ref["icon"] = tray
    tray.run()


if __name__ == "__main__":
    print("Dijital Denge V3.0 başlatılıyor...")
    veri_baslat()
    archive_old_days()
    t_izleyici = threading.Thread(target=arka_plan_dongusu, daemon=True)
    t_izleyici.start()
    app = DashboardApp()
    app._tracker_thread = t_izleyici
    t_tepsi = threading.Thread(target=tepsi_simgesi_olustur, args=(app,), daemon=True)
    t_tepsi.start()
    app.mainloop()