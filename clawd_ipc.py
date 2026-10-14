"""桌寵與 hook 共用：檔案位置、Claude 視窗查詢、行程與 JSON 檔工具。

請求／回應流程：hook 把請求寫進 requests/<id>.json，桌寵顯示卡片，
使用者按下後桌寵寫 responses/<id>.json，hook 讀到就回覆給 Claude Code 並刪掉兩個檔。
狀態：hook 每個 Claude Code 對話寫一個 sessions/<session>.json（見 session_path），對話結束時刪掉。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import time
from pathlib import Path

BASE = Path.home() / '.clawd'
SESS_DIR = BASE / 'sessions'
REQ_DIR = BASE / 'requests'
RESP_DIR = BASE / 'responses'
MUTEX_NAME = 'Local\\ClawdDesktopPet'
CLAUDE_STORE_FAMILY = 'Claude_pzs8sxrjxfjjc'          # Microsoft Store 版 Claude 的套件名稱
CLAUDE_STORE_APP = 'shell:AppsFolder\\%s!Claude' % CLAUDE_STORE_FAMILY

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetForegroundWindow.restype = wt.HWND
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if not n:
        return ''
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def find_claude_window(include_minimized=False):
    """Claude 桌面版主視窗的 hwnd，找不到回傳 None。"""
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and (include_minimized or not user32.IsIconic(hwnd)) \
                and window_title(hwnd) == 'Claude':
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None


def claude_is_foreground():
    hwnd = user32.GetForegroundWindow()
    return bool(hwnd) and window_title(hwnd) == 'Claude'


def focus_claude():
    """把 Claude 視窗叫到前面；視窗關掉了（沒在執行或縮到系統匣）就啟動 Claude，它會自己叫出原本的視窗。
    沒裝 Claude 桌面版時回傳 False。"""
    hwnd = find_claude_window(include_minimized=True)
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)                   # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    exe = local / 'AnthropicClaude' / 'claude.exe'      # 舊版安裝程式裝的
    if exe.is_file():
        target = str(exe)
    elif (local / 'Packages' / CLAUDE_STORE_FAMILY).is_dir():    # Store 版會有自己的資料夾
        target = CLAUDE_STORE_APP
    else:
        return False
    try:
        os.startfile(target)
    except OSError:
        return False
    return True


def pet_running():
    handle = kernel32.OpenMutexW(0x00100000, False, MUTEX_NAME)   # SYNCHRONIZE
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def pid_alive(pid):
    handle = kernel32.OpenProcess(0x1000, False, int(pid))         # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = wt.DWORD()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return code.value == 259                                       # STILL_ACTIVE


def session_path(sid):
    """這個 session 的狀態檔：檔名只留英數、底線與連字號，最多 64 字，空的用 default。"""
    name = re.sub(r'[^A-Za-z0-9_-]', '', str(sid or ''))[:64] or 'default'
    return SESS_DIR / (name + '.json')


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('%s.%d.tmp' % (path.name, os.getpid()))
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
    for _ in range(5):                                  # 對方剛好在讀檔時 replace 會失敗，稍等重試
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.02)
    tmp.unlink(missing_ok=True)
    return False


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
