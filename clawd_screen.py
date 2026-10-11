"""桌面幾何：多螢幕各自的活動範圍、全螢幕偵測、可以站上去的視窗上緣。

都是 Win32 查詢；桌寵每一格會問好幾次，所以結果短暫快取。座標一律是實體像素（桌寵有開 DPI 感知）。
"""
import ctypes
import ctypes.wintypes as wt
import os
import time

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
user32.GetForegroundWindow.restype = wt.HWND
user32.FindWindowW.restype = wt.HWND
user32.WindowFromPoint.restype = wt.HWND
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
SHELL_CLASSES = ('Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd')


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT), ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('hWnd', wt.HWND), ('uCallbackMessage', wt.UINT), ('uEdge', wt.UINT),
                ('rc', wt.RECT), ('lParam', wt.LPARAM)]


_cache = {}


def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def _tuple(r):
    return r.left, r.top, r.right, r.bottom


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


def window_rect(hwnd):
    """視窗看得到的外框（不含 Windows 10/11 透明的縮放邊框）。"""
    r = wt.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:   # DWMWA_EXTENDED_FRAME_BOUNDS
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return _tuple(r)


# ───────── 螢幕 ─────────
def _monitors():
    found = []

    def cb(hmon, hdc, rect, lparam):
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        found.append((hmon, _tuple(mi.rcMonitor), _tuple(mi.rcWork)))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    if not found:                                          # 查不到就當成只有主螢幕
        full = (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        found.append((0, full, full))
    return found


def monitors():
    """[(hmonitor, 螢幕範圍, 工作區)]。接上或拔掉螢幕後兩秒內會更新。"""
    return _cached('monitors', 2.0, _monitors)


def _taskbar_autohide():
    ab = APPBARDATA(cbSize=ctypes.sizeof(APPBARDATA))
    if ctypes.windll.shell32.SHAppBarMessage(4, ctypes.byref(ab)) & 1:   # ABM_GETSTATE：ABS_AUTOHIDE
        return True
    tray = user32.FindWindowW('Shell_TrayWnd', None)
    return not tray or not user32.IsWindowVisible(tray)


def _fullscreen_monitor():
    fg = user32.GetForegroundWindow()
    if not fg or _class_name(fg) in SHELL_CLASSES:         # 桌面本身也蓋滿整個螢幕，不算全螢幕程式
        return None
    r = window_rect(fg)
    for hmon, mon, work in monitors():
        if r[0] <= mon[0] and r[1] <= mon[1] and r[2] >= mon[2] and r[3] >= mon[3]:
            # 一般最大化只佔工作區；連工作列都蓋住才是全螢幕。只有沒工作列的螢幕分不出來，才看是不是最大化
            # （Electron 之類從最大化進全螢幕時 IsZoomed 仍是 True，所以不能一律排除最大化）
            if work == mon and user32.IsZoomed(fg):
                return None
            return hmon
    return None


def fullscreen_monitor():
    """前景視窗全螢幕時，回傳它所在螢幕的 hmonitor；沒有就回傳 None。"""
    return _cached('fullscreen', 0.5, _fullscreen_monitor)


def areas():
    """[(hmonitor, 螢幕範圍, 可活動範圍)]。工作列自動隱藏、或被全螢幕視窗蓋住時，可活動範圍就是整個螢幕。"""
    def build():
        autohide, full = _taskbar_autohide(), fullscreen_monitor()
        return [(h, mon, mon if autohide or h == full else work) for h, mon, work in monitors()]
    return _cached('areas', 0.5, build)


def area_at(x, y):
    """(x, y) 所在螢幕的 (hmonitor, 螢幕範圍, 可活動範圍)；不在任何螢幕上時，先找水平方向涵蓋 x 的，再找最近的。"""
    def dist(item):
        mon = item[1]
        return max(mon[0] - x, 0, x - mon[2] + 1), max(mon[1] - y, 0, y - mon[3] + 1)
    return min(areas(), key=dist)


def span(y):
    """跟高度 y 同一排的螢幕合起來的左右邊界。螢幕上下錯開時，沒有畫面的地方走不過去，要用甩的。"""
    row = [m[1] for m in areas() if m[1][1] <= y < m[1][3]]
    if not row:
        row = [min(areas(), key=lambda m: max(m[1][1] - y, 0, y - m[1][3] + 1))[1]]
    return min(m[0] for m in row), max(m[2] for m in row)


def ceiling_at(x):
    """x 這一欄最上面那個螢幕的頂端（上下疊放的螢幕可以一路往上飛）。"""
    tops = [m[2][1] for m in areas() if m[1][0] <= x < m[1][2]]
    return min(tops) if tops else area_at(x, 0)[2][1]


# ───────── 可以站的視窗上緣 ─────────
def _ledges(min_width, headroom):
    pid, found = os.getpid(), []
    own = wt.DWORD()

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or not user32.GetWindowTextLengthW(hwnd):
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x80:          # WS_EX_TOOLWINDOW：浮動工具列、通知之類
            return True
        cloaked = ctypes.c_int(0)
        dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)   # DWMWA_CLOAKED：其他虛擬桌面等
        if cloaked.value:
            return True
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(own))
        if own.value == pid or _class_name(hwnd) in SHELL_CLASSES:
            return True
        left, top, right, bottom = window_rect(hwnd)
        area = area_at((left + right) / 2, top)[2]
        if right - left >= min_width and top - headroom >= area[1] and top < area[3]:   # 最大化的視窗上面站不下
            found.append((hwnd, left, right, top))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def ledges(min_width, headroom):
    """可以站上去的視窗上緣 [(hwnd, 左, 右, 上緣 y)]，z-order 由上到下。"""
    return _cached('ledges', 0.3, lambda: _ledges(min_width, headroom))


def edge_visible(hwnd, x, top):
    """視窗上緣在 x 這一點沒有被別的視窗蓋住。"""
    h = user32.WindowFromPoint(wt.POINT(int(x), int(top) + 3))
    return bool(h) and user32.GetAncestor(h, 2) == hwnd     # GA_ROOT


def cursor():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y
