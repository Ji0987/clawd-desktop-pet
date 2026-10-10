"""桌寵與 hook 共用：檔案位置、Claude 視窗查詢、行程與 JSON 檔工具。

請求／回應流程：hook 把請求寫進 requests/<id>.json，桌寵顯示卡片，
使用者按下後桌寵寫 responses/<id>.json，hook 讀到就回覆給 Claude Code 並刪掉兩個檔。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import time
from pathlib import Path

BASE = Path.home() / '.clawd'
STATE_FILE = BASE / 'state.json'
REQ_DIR = BASE / 'requests'
RESP_DIR = BASE / 'responses'
MUTEX_NAME = 'Local\\ClawdDesktopPet'

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
    hwnd = find_claude_window(include_minimized=True)
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)                   # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


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


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('%s.%d.tmp' % (path.name, os.getpid()))
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
    for _ in range(5):                                  # 對方剛好在讀檔時 replace 會失敗，稍等重試
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
    tmp.unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
