"""Clawd 桌寵：在 Windows 桌面上走來走去，並依 Claude Code hook 寫出的狀態切換動作。

狀態檔：~/.clawd/state.json（由 clawd_hook.py 寫入）
  idle 閒晃｜thinking 想事情｜working 跑工具｜alert 等你回應｜done 回覆完成
Claude 在忙時，Clawd 會跑去跳上 Claude 視窗的上緣（視窗最大化時就在工作列上陪著）。
左鍵點牠會跳、拖曳可以抓起來甩出去；右鍵選單可以叫牠睡覺、開啟 Claude 或結束。
Claude 要你批准權限或回答問題時，Clawd 頭上會出現卡片，直接在卡片上回答即可（見 clawd_hook.py）。
"""
import ctypes
import ctypes.wintypes as wt
import math
import random
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

import clawd_ipc as ipc
import clawd_setup
from fluent_ui import FluentMenu, RequestCard

FPS = 30
CONFIG_FILE = ipc.BASE / 'config.json'      # 桌寵自己的設定（例如問過要不要連動了沒）
KEY = '#FF00FE'                       # 透明色鍵：這個顏色的像素會變透明且點擊穿透
CLAY, INK, CREAM = '#D97757', '#1F1D1A', '#FFF8EC'
SIDE, LAPTOP = '#B5634A', '#8E8E8E'          # 側面的深色身體、筆電
STALE = 600                           # 狀態檔超過這麼多秒沒更新就當作閒置
SLEEP_AFTER = 120                     # 閒置這麼多秒後睡覺

# ───────── 吉祥物造型（與 stickers/*/index.html 同一套像素定義） ─────────
ROWS = 7                              # 身體 12 欄 × 7 列，第 0–4 列身體、5–6 列四隻腳
ARMS = {                              # 右手座標，左手鏡射
    'nub': [(11, 3)],
    'out': [(11, 3), (12, 3)],
    'up': [(11, 3), (12, 2), (12, 1)],
    'high': [(11, 3), (12, 2), (12, 1), (12, 0), (12, -1)],
    'high2': [(11, 3), (12, 2), (12, 1), (13, 0), (13, -1)],
}

user32 = ctypes.windll.user32
user32.GetParent.restype = wt.HWND
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
HWND_TOPMOST = wt.HWND(-1)                 # 要宣告成 HWND：直接傳 -1 在 64 位元下會被截成 32 位元而失效


def log_exception(exc, value, tb):
    """打包版沒有主控台，例外寫進 ~/.clawd/error.log（同一個錯誤只記一次）。"""
    import traceback
    text = ''.join(traceback.format_exception(exc, value, tb))
    if text in _logged:
        return
    _logged.add(text)
    try:
        ipc.BASE.mkdir(parents=True, exist_ok=True)
        with open(ipc.BASE / 'error.log', 'a', encoding='utf-8') as f:
            f.write(time.strftime('[%Y-%m-%d %H:%M:%S]\n') + text + '\n')
    except OSError:
        pass


_logged = set()


def keep_on_top(hwnd):
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # NOSIZE|NOMOVE|NOACTIVATE


def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        return user32.GetDpiForSystem() / 96
    except Exception:
        return 1.0


def work_area():
    r = wt.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA（主螢幕）
    return r.left, r.top, r.right, r.bottom


def claude_window_rect():
    """回傳 Claude 桌面版視窗的可見外框 (left, top, right, bottom)，找不到或最小化時回傳 None。"""
    hwnd = ipc.find_claude_window()
    if not hwnd:
        return None
    r = wt.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))     # 9 = DWMWA_EXTENDED_FRAME_BOUNDS
    return r.left, r.top, r.right, r.bottom


def short_tool(name):
    if name.startswith('mcp__'):
        name = name.rsplit('__', 1)[-1]
    return name if len(name) <= 16 else name[:15] + '…'


class Pet:
    def __init__(self, scale):
        self.S = scale
        self.c = max(3, round(6 * scale))                  # 一個像素格的實際邊長
        self.W, self.H = round(200 * scale), round(8 * self.c + 48 * scale)
        self.G = 0.9 * scale                               # 每格重力加速度

        self.root = tk.Tk()
        self.root.report_callback_exception = log_exception
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-transparentcolor', KEY)
        self.root.config(bg=KEY)
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H, bg=KEY, highlightthickness=0)
        self.cv.pack()
        self.font = tkfont.Font(family='Microsoft JhengHei UI', size=-round(13 * scale), weight='bold')

        self.menu = None
        self.cv.bind('<ButtonPress-1>', self.on_press)
        self.cv.bind('<B1-Motion>', self.on_drag)
        self.cv.bind('<ButtonRelease-1>', self.on_release)
        self.cv.bind('<Button-3>', self.open_menu)

        wa = work_area()
        self.x, self.y = random.uniform(wa[0] + 200, wa[2] - 200), wa[3]
        self.vx = self.vy = 0.0
        self.surface = 'floor'                             # 'floor'｜'perch'（Claude 視窗上緣）｜None 在空中
        self.facing = 1
        self.goal = None
        self.walking = False
        self.next_wander = time.time() + 2
        self.land_sq = 0.0
        self.t = 0
        self.blink_until, self.next_blink = 0.0, time.time() + 3
        self.next_hop = 0.0
        self.react_until, self.react_text = 0.0, ''

        self.drag = None                                   # 按下時的 (指標x, 指標y, 寵物x, 寵物y)
        self.dragging = False
        self.drag_hist = []                                # 最近的 (時間, 指標x, 指標y)，放手時算甩出去的速度
        self.rest_y = self.y                               # 最後站穩的高度，卡片以此定位，不跟著跳

        self.card, self.card_id = None, None               # 頭上的權限／提問卡片
        self.answered = set()
        self.local_reqs = []                               # 桌寵自己要問的事：(請求, 收到回答時的動作)
        self.next_req = 0.0

        self.file_state, self.file_ts, self.tool = 'idle', 0.0, ''
        self.last_work_ts, self.done_until = 0.0, 0.0
        self.last_mtime = None
        self.last_activity = time.time()
        self.sleeping = False
        self.forced_sleep = False

        self.claude_rect = None
        self.next_poll = self.next_win = 0.0
        self.last_geo = None
        self.type_start = None                             # 開始敲筆電的時間，用來播拿出筆電的開場

        self.root.update_idletasks()
        self.hwnd = user32.GetParent(self.root.winfo_id())
        ex = user32.GetWindowLongW(self.hwnd, -20)        # 不搶焦點、不出現在工作列
        user32.SetWindowLongW(self.hwnd, -20, ex | 0x08000000 | 0x00000080)
        self.check_link()

    # ───────── 狀態來源 ─────────
    def poll_state(self, now):
        if now < self.next_poll:
            return
        self.next_poll = now + 0.2
        try:
            mtime = ipc.STATE_FILE.stat().st_mtime_ns
        except OSError:
            return
        if mtime == self.last_mtime:
            return
        data = ipc.read_json(ipc.STATE_FILE)
        if not isinstance(data, dict):
            return
        self.last_mtime = mtime
        state, ts = data.get('state', 'idle'), float(data.get('ts', now))
        self.file_state, self.file_ts, self.tool = state, ts, short_tool(data.get('tool', ''))
        if state == 'working':
            self.last_work_ts = ts
        if state == 'done':
            self.done_until = ts + 4
        self.last_activity = now
        self.sleeping = self.forced_sleep = False

    def mode(self, now):
        m = self.file_state
        if now - self.file_ts > STALE:
            m = 'idle'
        if m == 'thinking' and now - self.last_work_ts < 1.5:   # 工具之間的空檔不要閃來閃去
            m = 'working'
        if m == 'done' and now > self.done_until:
            m = 'idle'
        if m == 'idle':
            if not self.sleeping and now - self.last_activity > SLEEP_AFTER:
                self.sleeping = True
            if self.sleeping or self.forced_sleep:
                m = 'sleep'
        return m

    def perch(self, now):
        """Claude 視窗上緣可站立的範圍 (left, right, top)；視窗最大化或找不到時回傳 None。"""
        if now >= self.next_win:
            self.next_win = now + 0.3
            self.claude_rect = claude_window_rect()
        r = self.claude_rect
        wa = work_area()
        if not r or r[1] - 9 * self.c < wa[1] or r[2] - r[0] < 24 * self.c or r[0] > wa[2] or r[2] < wa[0]:
            return None
        return r[0] + 5 * self.c, r[2] - 5 * self.c, r[1]

    # ───────── 互動 ─────────
    def on_press(self, e):
        self.drag = (e.x_root, e.y_root, self.x, self.y)
        self.drag_hist = [(time.time(), e.x_root, e.y_root)]
        self.dragging = False

    def on_drag(self, e):
        if not self.drag:
            return
        px, py, x0, y0 = self.drag
        if not self.dragging and abs(e.x_root - px) + abs(e.y_root - py) > 4:
            self.dragging = True
            self.surface, self.goal = None, None
        if self.dragging:
            now = time.time()
            self.drag_hist = [p for p in self.drag_hist if now - p[0] < 0.25] + [(now, e.x_root, e.y_root)]
            self.x, self.y = x0 + e.x_root - px, y0 + e.y_root - py

    def throw_velocity(self, now):
        """最近約 80ms 的指標位移換算成每格的速度；停住不動再放手就是直接掉下去。"""
        recent = [p for p in self.drag_hist if now - p[0] < 0.08]
        if len(recent) < 2 or now - recent[-1][0] > 0.05:
            return 0.0, 0.0
        dt = max(recent[-1][0] - recent[0][0], 1 / 120)
        vx = (recent[-1][1] - recent[0][1]) / dt / FPS
        vy = (recent[-1][2] - recent[0][2]) / dt / FPS
        cap = 45 * self.S
        k = min(1.0, cap / max(math.hypot(vx, vy), 1e-6))
        return vx * k, vy * k

    def on_release(self, e):
        now = time.time()
        if self.dragging:
            self.dragging = False                          # 放手後帶著甩出去的速度飛出去，以拋物線落下
            self.vx, self.vy = self.throw_velocity(now)
            if abs(self.vx) > 0.5:
                self.facing = 1 if self.vx > 0 else -1
        else:
            self.wake(now)
            self.react_until, self.react_text = now + 1.5, random.choice(['嗨！', '摸摸', '嘿嘿', '♪'])
            self.hop(10)
        self.drag = None

    def wake(self, now):
        self.last_activity = now
        self.sleeping = self.forced_sleep = False

    def toggle_sleep(self):
        if self.sleeping or self.forced_sleep:
            self.wake(time.time())
        else:
            self.forced_sleep = True

    def open_menu(self, e):
        if self.menu:
            self.menu.close()
        asleep = self.sleeping or self.forced_sleep
        items = [('' if asleep else '', '起床' if asleep else '睡覺', self.toggle_sleep),
                 ('', '開啟 Claude', ipc.focus_claude)]
        linked = clawd_setup.status() == 'on'
        items.append(('', '解除 Claude Code 連動' if linked else '連動 Claude Code',
                      lambda: self.set_link(not linked)))
        items += [None, ('', '結束 Clawd', self.root.destroy)]
        self.menu = FluentMenu(self.root, self.S, items, e.x_root, e.y_root)

    # ───────── 與 Claude Code 連動 ─────────
    def check_link(self):
        """搬過資料夾或換了 Python 就默默改成新路徑；從沒問過就問一次要不要連動。"""
        state = clawd_setup.status()
        if state == 'moved':
            self.set_link(True, quiet=True)
            return
        config = ipc.read_json(CONFIG_FILE) or {}
        if state == 'off' and not config.get('link_prompted'):
            ipc.write_json(CONFIG_FILE, dict(config, link_prompted=True))
            self.local_reqs.append(({
                'id': 'local:link', 'kind': 'confirm', 'title': '要跟 Claude Code 連動嗎？',
                'body': '會在 ~/.claude/settings.json 加入 Clawd 的 hook，之後新開的 Claude 對話裡，'
                        'Clawd 會跟著 Claude 工作，也能讓你在這裡批准權限、回答問題。隨時可以從右鍵選單解除。',
                'yes': '連動', 'no': '先不要'}, lambda resp: resp.get('decision') == 'allow' and self.set_link(True)))

    def set_link(self, on, quiet=False):
        try:
            clawd_setup.install() if on else clawd_setup.uninstall()
            text = '已連動！' if on else '已解除連動'
        except Exception:
            text = '設定檔讀不了，沒有修改'
        if not quiet:
            self.react_until, self.react_text = time.time() + 3, text

    # ───────── 權限／提問卡片 ─────────
    def poll_requests(self, now):
        """挑最早的待回應請求顯示成卡片；hook 已結束（被中斷或逾時）的請求直接清掉。"""
        if now < self.next_req:
            return
        self.next_req = now + 0.2
        pending = []
        for path in ipc.REQ_DIR.glob('*.json'):
            req = ipc.read_json(path)
            if not isinstance(req, dict) or req.get('id') in self.answered:
                continue
            if not ipc.pid_alive(req.get('pid', 0)):
                path.unlink(missing_ok=True)
                continue
            pending.append(req)
        req = min(pending, key=lambda r: r.get('ts', 0)) if pending else None
        if req is None and self.local_reqs:                # Claude 的請求優先，空檔才問本機的事
            req = self.local_reqs[0][0]
        if req and req.get('id') == self.card_id:
            return
        self.close_card()
        if req:
            self.card_id = req['id']
            self.card = RequestCard(self.root, self.S, req, lambda resp, rid=req['id']: self.answer(rid, resp),
                                    self.card_anchor())
            self.wake(now)

    def answer(self, rid, resp):
        local = next((r for r in self.local_reqs if r[0]['id'] == rid), None)
        if local:
            self.local_reqs.remove(local)
            self.close_card()
            local[1](resp)
            return
        self.answered.add(rid)
        ipc.write_json(ipc.RESP_DIR / (rid + '.json'), dict(resp, id=rid))
        self.close_card()
        if resp.get('decision') == 'release':
            ipc.focus_claude()
        else:
            self.react_until, self.react_text = time.time() + 1.5, '收到！'

    def close_card(self):
        if self.card:
            self.card.close()
        self.card, self.card_id = None, None

    def card_anchor(self):
        return self.x, self.rest_y - 8 * self.c - 10 * self.S

    def hop(self, power):
        if self.surface is not None:
            self.surface = None
            self.vy = -power * self.S

    # ───────── 行為 ─────────
    def think(self, mode, now):
        self.walking = False
        if self.dragging or self.surface is None:
            return
        if self.card:                                      # 等你回答時原地站好，卡片才不會跟著跑
            self.goal = None
            return
        perch = self.perch(now)
        speed = 1.6 * self.S
        if mode in ('thinking', 'working', 'alert'):
            speed = 4.5 * self.S
            if perch:
                gx = perch[0] + (perch[1] - perch[0]) * 0.25
                if self.surface == 'floor' and abs(self.x - gx) < 8 * self.S:
                    h = self.y - perch[2] + 18 * self.S        # 跳上 Claude 視窗
                    self.surface, self.vx = None, 0.0
                    self.vy = -math.sqrt(2 * self.G * max(h, 10))
                    return
                self.goal = gx
            elif self.claude_rect:
                wa = work_area()
                gx = min(max((self.claude_rect[0] + self.claude_rect[2]) / 2, wa[0] + 80), wa[2] - 80)
                self.goal = gx if abs(self.x - gx) > 4 else None
            else:
                self.goal = None
            if mode == 'alert' and self.goal is None and now > self.next_hop:
                self.next_hop = now + 1.0
                self.hop(8)
        elif mode == 'done':
            self.goal = None
            if now > self.next_hop:
                self.next_hop = now + 0.7
                self.hop(9)
        elif mode == 'sleep':
            self.goal = None
        else:                                              # 閒晃：隨機挑地點走過去，偶爾走下視窗邊
            if self.goal is None and now > self.next_wander:
                wa = work_area()
                self.goal = random.uniform(wa[0] + 60 * self.S, wa[2] - 60 * self.S)
                if self.surface == 'perch' and perch and random.random() < 0.6:
                    self.goal = random.uniform(perch[0], perch[1])
        if self.goal is not None:
            dx = self.goal - self.x
            if abs(dx) <= speed:
                self.x, self.goal = self.goal, None
                self.next_wander = now + random.uniform(2, 7)
            else:
                self.facing = 1 if dx > 0 else -1
                self.x += self.facing * speed
                self.walking = True

    def physics(self, now):
        if self.dragging:
            return
        wa = work_area()
        floor = wa[3]
        perch = self.perch(now)
        if self.surface == 'perch':
            if not perch or not perch[0] <= self.x <= perch[1] or abs(self.y - perch[2]) > 60 * self.S:
                self.surface, self.vy = None, 0.0          # 走出邊緣或視窗移開了：掉下去
            else:
                self.y = perch[2]                          # 跟著視窗移動
        elif self.surface == 'floor':
            self.y = floor
        lo, hi = wa[0] + 7 * self.c, wa[2] - 7 * self.c
        if self.surface is None:
            prev = self.y
            self.vy = min(self.vy + self.G, 40 * self.S)
            self.y += self.vy
            self.x += self.vx
            self.vx *= 0.995                               # 一點空氣阻力，軌跡仍是拋物線
            if not lo <= self.x <= hi:                     # 撞到螢幕邊緣就彈回來
                self.vx = -self.vx * 0.5
            if self.vy > 0:
                if perch and perch[0] <= self.x <= perch[1] and prev <= perch[2] <= self.y:
                    self.land('perch', perch[2])
                elif self.y >= floor:
                    self.land('floor', floor)
        else:
            self.rest_y = self.y
        self.x = min(max(self.x, lo), hi)
        self.land_sq *= 0.75

    def land(self, surface, y):
        self.land_sq = min(0.25, 0.04 + abs(self.vy) / (60 * self.S))
        if self.vy > 12 * self.S:                          # 摔得重就彈一下，順勢再滑一段
            self.y, self.vy, self.vx = y, -self.vy * 0.3, self.vx * 0.6
            return
        self.surface, self.y, self.vx, self.vy = surface, y, 0.0, 0.0

    # ───────── 繪製 ─────────
    def render(self, mode, now):
        t = self.t
        eyes, lx, ly, arm_l, arm_r, run = 'normal', 0.0, 0.0, 'nub', 'nub', None
        sq, bubble = self.land_sq, None
        if self.walking:
            run = (t // (3 if mode in ('thinking', 'working', 'alert') else 5)) % 2 == 0
            lx = 0.3 * self.facing
        if now > self.next_blink:
            self.blink_until, self.next_blink = now + 0.15, now + random.uniform(2.5, 6)
        blink = now < self.blink_until

        if mode == 'sleep':
            eyes, sq, bubble = 'sleep', sq + 0.03 * math.sin(t / 15), 'Z' + 'z' * (1 + (t // 20) % 3)
        elif mode == 'thinking':
            bubble = '.' * (1 + (t // 8) % 3)
            if not self.walking:
                lx, ly, arm_r = -0.3 + 0.15 * math.sin(t / 20), -0.4, 'up'
        elif mode == 'working':
            bubble = self.tool or '工作中'                  # 站定後改畫敲筆電（draw_typing）
        elif mode == 'alert':
            eyes, bubble = 'big', '需要你！'
            arm_l, arm_r = ('high', 'high2') if (t // 6) % 2 else ('high2', 'high')
        elif mode == 'done':
            eyes, arm_l, arm_r, bubble = 'happy', 'up', 'up', '完成！'
        if self.card:                                      # 卡片取代泡泡，Clawd 揮手引起注意
            eyes, bubble = 'big', None
            arm_l, arm_r = ('high', 'high2') if (t // 6) % 2 else ('high2', 'high')
        if blink and eyes == 'normal':
            eyes = 'blink'

        if self.dragging:
            eyes, run = 'big', (t // 4) % 2 == 0
            arm_l, arm_r = ('high', 'high2') if (t // 6) % 2 else ('high2', 'high')
        elif self.surface is None and self.vy > 14 * self.S:         # 從高處掉下來才驚嚇，原地小跳不算
            eyes, arm_l, arm_r = 'big', 'up', 'up'
        if now < self.react_until:
            eyes, bubble = 'happy', self.react_text

        typing = (mode == 'working' and not self.walking and self.surface is not None and not self.dragging
                  and not self.card and now >= self.react_until)
        if not typing:
            self.type_start = None
        elif self.type_start is None:
            self.type_start = now

        cv = self.cv
        cv.delete('all')
        cx, by = self.W / 2, self.H - 1
        if typing:
            self.draw_typing(cx, by, now - self.type_start, t)
        else:
            self.draw_mascot(cx, by, eyes, lx, ly, arm_l, arm_r, run, sq)
        if bubble:
            self.draw_bubble(cx, by - 8 * self.c - 2 * self.S, bubble)

        # 跟視窗的實際位置比，不跟上次設定的值比：Tk 有時會把位置蓋回去（例如剛啟動就開卡片時停在 0,0）
        geo = (round(self.x - self.W / 2), round(self.y - self.H + 1))
        r = wt.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        if (r.left, r.top) != geo:
            self.root.geometry('+%d+%d' % geo)
            if geo == self.last_geo:                       # 上一格就設過了還沒到位：直接請 Windows 移過去
                user32.SetWindowPos(self.hwnd, None, geo[0], geo[1], 0, 0, 0x0001 | 0x0004 | 0x0010)
            self.last_geo = geo
        if self.card:
            self.card.place_above(*self.card_anchor())
        if t % (FPS // 2) == 0:                           # 別的視窗可能蓋過來，每半秒重申一次置頂（不搶焦點）
            keep_on_top(self.hwnd)
            for popup in (self.card, self.menu):          # 卡片與選單要在 Clawd 之上
                if popup and not popup.closed:
                    keep_on_top(popup.hwnd)

    def rect(self, x, y, w, h, color):
        self.cv.create_rectangle(round(x), round(y), round(x + w), round(y + h), fill=color, outline='')

    def draw_mascot(self, cx, by, eyes, lx, ly, arm_l, arm_r, run, sq):
        c = self.c
        sx, sy = 1 + sq, 1 - sq

        def cell(col, row, w=1, h=1):
            self.rect(cx + (col - 6) * c * sx, by + (row - ROWS) * c * sy, w * c * sx, h * c * sy, CLAY)

        cell(1, 0, 10, 5)                                  # 身體整塊畫，避免像素縫
        for col in (1, 3, 8, 10):                          # 四隻腳；run 時兩兩交替抬起
            lift = 0 if run is None else (0.7 if (col in (1, 8)) == run else 0)
            cell(col, 5, 1, 2 - lift)
        for col, row in ARMS[arm_r]:
            cell(col, row)
        for col, row in ARMS[arm_l]:
            cell(11 - col, row)
        cw, ch = c * sx, c * sy
        for col in (3, 8):
            ex, ey = cx + (col - 6 + lx) * cw, by + (1 - ROWS + ly) * ch
            if eyes == 'normal':
                self.rect(ex, ey, cw, 2 * ch, INK)
            elif eyes == 'blink':
                self.rect(ex, ey + 1.3 * ch, cw, 0.4 * ch, INK)
            elif eyes == 'sleep':
                self.rect(ex - 0.25 * cw, ey + 1.25 * ch, 1.5 * cw, 0.4 * ch, INK)
            elif eyes == 'big':
                self.rect(ex - 0.25 * cw, ey - 0.2 * ch, 1.5 * cw, 2.2 * ch, INK)
                self.rect(ex, ey + 0.1 * ch, 0.5 * cw, 0.5 * ch, CREAM)
            elif eyes == 'happy':
                s = 0.78 * c
                self.rect(ex - 0.8 * s, ey + 1.3 * ch, s, s, INK)
                self.rect(ex + cw / 2 - s / 2, ey + 1.3 * ch - s, s, s, INK)
                self.rect(ex + cw - 0.2 * s, ey + 1.3 * ch, s, s, INK)

    def draw_typing(self, cx, by, phase, t):
        """敲筆電，仿 Claude 桌面版的動畫：拿出筆電放到面前地上、打開，轉成 3/4 側面用前手敲鍵盤。

        座標以「面向左、筆電在左邊」來寫，面向右時整個左右鏡射。
        """
        c, mirror = self.c, self.facing > 0

        def xs(col, w=0):
            x0, x1 = (col - 6) * c, (col + w - 6) * c
            return (-x1, -x0) if mirror else (x0, x1)

        def box(col, row, w, h, color):
            x0, x1 = xs(col, w)
            self.rect(cx + x0, by + (row - ROWS) * c, x1 - x0, h * c, color)

        def line(points, color=LAPTOP):
            pts = []
            for col, row in points:
                pts += [cx + xs(col)[0], by + (row - ROWS) * c]
            self.cv.create_line(*pts, fill=color, width=max(2, round(0.45 * c)), capstyle='projecting')

        near = 'arm_r' if mirror else 'arm_l'               # 靠筆電那一側的手
        if phase < 0.25:                                   # 開場一：把闔著的筆電舉在頭旁邊
            self.draw_mascot(cx, by, 'normal', 0, 0, **{'arm_l': 'nub', 'arm_r': 'nub', near: 'up'}, run=None, sq=0)
            line([(-3.6, 0.6), (-0.6, 0.6)])
            return
        if phase < 0.45:                                   # 開場二：放到地上
            self.draw_mascot(cx, by, 'normal', 0, 0.3, **{'arm_l': 'nub', 'arm_r': 'nub', near: 'out'}, run=None,
                             sq=0.04)
            line([(-3.6, 6.8), (-0.6, 6.8)])
            return
        line([(-5.4, 4.2), (-3.6, 6.8), (-0.4, 6.8)])      # 打開的筆電：往後仰的螢幕＋平放的鍵盤
        box(1, 0, 8, 5, CLAY)                              # 正面
        box(9, 0, 2, 5, SIDE)                              # 側面
        for col, color in ((2, CLAY), (4.5, CLAY), (7.5, CLAY), (9.5, SIDE)):
            box(col, 5, 1, 2, color)
            box(col - 0.4, 6.4, 0.5, 0.6, color)           # 腳尖朝前
        box(1, 1.2, 1, 1.6, INK)                           # 眼睛偏向前方
        box(5, 1.2, 1, 1.6, INK)
        box(0, 3, 1, 1, CLAY)                              # 前手：伸出去／壓到鍵盤上，節奏刻意不規律
        if (0b1011010011010110 >> (t // 3 % 16)) & 1:
            box(-1.2, 5.5, 1, 1, CLAY)
        else:
            box(-1, 3, 1, 1, CLAY)

    def draw_bubble(self, cx, bottom, text):
        S = self.S
        pad, tail = 7 * S, 6 * S
        w = min(self.font.measure(text) + 2 * pad, self.W - 4)
        h = self.font.metrics('linespace') + pad * 0.8
        x0, y0 = cx - w / 2, bottom - tail - h
        lw = max(1, round(1.5 * S))
        self.cv.create_rectangle(round(x0), round(y0), round(x0 + w), round(y0 + h), fill=CREAM, outline=INK, width=lw)
        self.cv.create_polygon(cx - 5 * S, y0 + h, cx + 5 * S, y0 + h, cx, y0 + h + tail, fill=CREAM, outline=INK, width=lw)
        self.cv.create_line(cx - 5 * S + lw, y0 + h, cx + 5 * S - lw, y0 + h, fill=CREAM, width=lw + 1)
        self.cv.create_text(cx, y0 + h / 2, text=text, fill=INK, font=self.font)

    # ───────── 主迴圈 ─────────
    def tick(self):
        try:
            now = time.time()
            self.poll_state(now)
            self.poll_requests(now)
            mode = self.mode(now)
            self.think(mode, now)
            self.physics(now)
            self.render(mode, now)
            self.t += 1
        finally:
            self.root.after(1000 // FPS, self.tick)

    def run(self):
        self.tick()
        self.root.mainloop()


def main():
    ctypes.windll.kernel32.CreateMutexW(None, False, ipc.MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS：已經有一隻在跑
        sys.exit(0)
    Pet(set_dpi_aware()).run()


if __name__ == '__main__':
    main()
