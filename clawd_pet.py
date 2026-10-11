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
import os
import random
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

import clawd_ipc as ipc
import clawd_screen as screen
import clawd_setup
from fluent_ui import FluentMenu, RequestCard

FPS = 30
CONFIG_FILE = ipc.BASE / 'config.json'      # 桌寵自己的設定（例如問過要不要連動了沒）
KEY = '#FF00FE'                       # 透明色鍵：這個顏色的像素會變透明且點擊穿透
CLAY, INK, CREAM = '#D97757', '#1F1D1A', '#FFF8EC'
SIDE, LAPTOP = '#B5634A', '#8E8E8E'          # 側面的深色身體、筆電
BIKE, TIRE, STAR = '#3E7CB1', '#5A5A5A', '#F2C14E'   # 腳踏車車架、輪胎（中灰，深淺桌布都看得到）、頭暈的星星
STALE = 600                           # 狀態檔超過這麼多秒沒更新就當作閒置
SLEEP_AFTER = 120                     # 閒置這麼多秒後睡覺
SLEEP_AFTER_NIGHT = 40                # 晚上 10 點到早上 6 點比較愛睡

# ───────── 吉祥物造型（與 stickers/*/index.html 同一套像素定義） ─────────
ROWS = 7                              # 身體 12 欄 × 7 列，第 0–4 列身體、5–6 列四隻腳
ARMS = {                              # 右手座標，左手鏡射；舉手時先往外伸再往上彎，格子邊靠邊才不會像斷掉
    'nub': [(11, 3)],
    'out': [(11, 3), (12, 3)],
    'up': [(11, 3), (12, 3), (12, 2), (12, 1)],
    'high': [(11, 3), (12, 3), (12, 2), (12, 1), (12, 0), (12, -1)],
    'high2': [(11, 3), (12, 3), (12, 2), (12, 1), (13, 1), (13, 0), (13, -1)],
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


def is_night(now):
    hour = time.localtime(now).tm_hour
    return hour >= 22 or hour < 6


def short_tool(name):
    if name.startswith('mcp__'):
        name = name.rsplit('__', 1)[-1]
    return name if len(name) <= 16 else name[:15] + '…'


class Pet:
    def __init__(self, scale):
        self.S = scale
        self.c = max(3, round(6 * scale))                  # 一個像素格的實際邊長
        self.W, self.H = round(200 * scale), round(11.2 * self.c + 48 * scale)   # 多留 3.2 格：騎車時身體抬高，泡泡才放得下
        self.G = 0.9 * scale                               # 每格重力加速度

        prev_fg = user32.GetForegroundWindow()
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

        wa = screen.area_at(1, 1)[2]                       # 從主螢幕開始
        self.x, self.y = random.uniform(wa[0] + 200, wa[2] - 200), wa[3]
        self.vx = self.vy = 0.0
        self.surface = 'floor'                             # 'floor'｜'perch'（站在某個視窗上緣）｜None 在空中
        self.ledge = None                                  # 站著的那個視窗（surface 是 'perch' 時）
        self.climb = None                                  # 走到 goal 之後要跳上去的視窗
        self.left_ledge = None                             # 剛跳離的視窗，這次落下時不算
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
        self.riding = self.want_ride = False               # 騎腳踏車中／下次出門要騎車
        self.chute = False                                 # 從高處掉下來時打開的降落傘
        self.dizzy_until = 0.0                             # 被甩去撞到之後頭暈到這個時間
        self.claude_hwnd = None
        self.cursor = self.last_cursor = (0, 0)
        self.chase_until, self.next_chase = 0.0, time.time() + 10   # 追游標
        self.hiding = False                                # 有全螢幕程式時縮到角落
        self.cur_mode = 'idle'
        self.next_top = 0.0

        self.root.update_idletasks()
        self.hwnd = user32.GetParent(self.root.winfo_id())
        ex = user32.GetWindowLongW(self.hwnd, -20)        # 不搶焦點、不出現在工作列
        user32.SetWindowLongW(self.hwnd, -20, ex | 0x08000000 | 0x00000080)
        # Tk 啟動時會把自己設成前景（就算視窗藏著也一樣），你正在打字的視窗會失去焦點：馬上還回去
        self.root.update()
        fg = user32.GetForegroundWindow()
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if prev_fg and pid.value == os.getpid():
            user32.SetForegroundWindow(prev_fg)
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
            night = is_night(now)
            if not self.sleeping and now - self.last_activity > (SLEEP_AFTER_NIGHT if night else SLEEP_AFTER):
                self.sleeping = True
                if night:
                    self.react_until, self.react_text = now + 2, '呵～好睏'
            if self.sleeping or self.forced_sleep:
                m = 'sleep'
        return m

    # ───────── 可以站的地方 ─────────
    def bounds(self):
        """跟 Clawd 同一排的螢幕合起來的左右邊界（扣掉半個身體）。"""
        lo, hi = screen.span(self.y - 1)
        return lo + 7 * self.c, hi - 7 * self.c

    def ledges(self):
        """可以站上去的視窗上緣，換算成能站的範圍 [(hwnd, 左, 右, 上緣)]。"""
        return [(h, l + 5 * self.c, r - 5 * self.c, top) for h, l, r, top in screen.ledges(16 * self.c, 9 * self.c)]

    def find_ledge(self, hwnd):
        return next((lg for lg in self.ledges() if lg[0] == hwnd), None) if hwnd else None

    def claude_ledge(self, now):
        """Claude 視窗上緣 (hwnd, 左, 右, 上緣)；最大化、太窄、被別的視窗蓋住或找不到時回傳 None。"""
        if now >= self.next_win:
            self.next_win = now + 0.3
            self.claude_hwnd = ipc.find_claude_window()
            self.claude_rect = screen.window_rect(self.claude_hwnd) if self.claude_hwnd else None
        lg = self.find_ledge(self.claude_hwnd)
        if not lg or lg[2] - lg[1] < 14 * self.c or not screen.edge_visible(lg[0], self.ledge_spot(lg), lg[3]):
            return None
        return lg

    @staticmethod
    def ledge_spot(lg):
        return lg[1] + (lg[2] - lg[1]) * 0.25

    def landing(self, prev):
        """這一格往下掉時穿過的視窗上緣裡最高的那個。"""
        hits = [lg for lg in self.ledges() if lg[1] <= self.x <= lg[2] and prev <= lg[3] <= self.y
                and lg[0] != self.left_ledge and screen.edge_visible(lg[0], self.x, lg[3])]
        return min(hits, key=lambda lg: lg[3]) if hits else None

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
            self.surface, self.goal, self.ledge, self.climb, self.left_ledge = None, None, None, None, None
            self.riding = self.chute = False
            self.chase_until = 0.0
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
            text = random.choice(['嗨！', '摸摸', '嘿嘿', '♪'])
            if self.cur_mode in ('done', 'alert') and not self.card and ipc.find_claude_window(True):
                ipc.focus_claude()                         # 說完成或在等你的時候，點牠就回 Claude（桌面版開著時）
                text = '去看看！'
            self.react_until, self.react_text = now + 1.5, text
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

    def open_claude(self):
        if not ipc.focus_claude():
            self.react_until, self.react_text = time.time() + 2.5, '找不到 Claude 桌面版'

    def go_ride(self):
        """下次出門騎腳踏車；在 Claude 視窗上的話會先走下來。"""
        self.wake(time.time())
        self.want_ride, self.goal, self.next_wander = True, None, 0.0

    def open_menu(self, e):
        if self.menu:
            self.menu.close()
        asleep = self.sleeping or self.forced_sleep
        items = [('' if asleep else '', '起床' if asleep else '睡覺', self.toggle_sleep),
                 ('', '騎腳踏車兜風', self.go_ride),
                 ('', '開啟 Claude', self.open_claude)]
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
            self.surface, self.riding = None, False
            self.vy = -power * self.S

    # ───────── 行為 ─────────
    def think(self, mode, now):
        self.walking = False
        if self.dragging or self.surface is None:
            return
        if self.card or now < self.dizzy_until:            # 等你回答時原地站好，卡片才不會跟著跑；頭暈時也站著不動
            self.goal, self.riding, self.climb = None, False, None
            return
        lo, hi = self.bounds()
        speed = 1.6 * self.S
        if mode in ('thinking', 'working', 'alert'):
            self.chase_until = 0.0
            speed = 4.5 * self.S
            claude = self.claude_ledge(now)
            if claude:                                     # 去 Claude 視窗上緣陪著
                self.goal = self.ledge_spot(claude)
                self.climb = None if self.ledge == claude[0] else claude[0]
            elif self.claude_rect:                         # 站不上去（最大化之類）就待在視窗正下方
                gx = min(max((self.claude_rect[0] + self.claude_rect[2]) / 2, lo), hi)
                self.goal, self.climb = (gx if abs(self.x - gx) > 4 else None), None
            else:
                self.goal = self.climb = None
            if mode == 'alert' and self.goal is None and now > self.next_hop:
                self.next_hop = now + 1.0
                self.hop(8)
        elif mode == 'done':
            self.goal = self.climb = None
            if now > self.next_hop:
                self.next_hop = now + 0.7
                self.hop(9)
        elif mode == 'sleep':
            self.goal = self.climb = None
        elif not self.chase(now, lo, hi) and self.goal is None and now > self.next_wander:
            self.wander(lo, hi)
        if self.climb and self.goal is not None and abs(self.x - self.goal) < 8 * self.S:
            target = self.find_ledge(self.climb)
            self.climb = None
            if target and target[1] <= self.x <= target[2]:
                self.jump_to(target)
                return
        if self.goal is not None:
            step = speed
            if self.riding:
                step = max(speed, 4.2 * self.S)
            elif now < self.chase_until:
                step = 3.8 * self.S
            dx = self.goal - self.x
            if abs(dx) <= step:
                self.x, self.goal = self.goal, None
                self.next_wander = now + random.uniform(2, 7)
            else:
                self.facing = 1 if dx > 0 else -1
                self.x += self.facing * step
                self.walking = True
        if self.goal is None:                              # 到了就下車
            self.riding = False

    def wander(self, lo, hi):
        """閒晃時挑下一個目的地：在原本的視窗上走走、走下來、騎車去遠方，或跳上附近的視窗。"""
        self.goal = random.uniform(lo, hi)
        if self.surface == 'perch':
            cur = self.find_ledge(self.ledge)
            if cur and random.random() < 0.6 and not self.want_ride:
                self.goal = random.uniform(cur[1], cur[2])
            return
        if self.want_ride or (abs(self.goal - self.x) > 400 * self.S and random.random() < 0.35):
            if self.want_ride:                             # 叫牠去兜風：騎到比較遠的那一端
                self.goal = lo if self.x - lo > hi - self.x else hi
            self.riding, self.want_ride = True, False
            return
        if random.random() < 0.3:                          # 跳上某個視窗的上緣
            options = [lg for lg in self.ledges() if 40 * self.S < self.y - lg[3] < 500 * self.S
                       and lo <= lg[1] and lg[2] <= hi]
            if options:
                lg = random.choice(options)
                gx = random.uniform(lg[1], lg[2])
                if screen.edge_visible(lg[0], gx, lg[3]):
                    self.goal, self.climb = gx, lg[0]

    def jump_to(self, target):
        """原地往上跳，剛好越過目標視窗上緣一點再落上去。"""
        h = self.y - target[3] + 18 * self.S
        self.left_ledge = self.ledge                       # 目標比腳下的視窗低時，落下要穿過原本站的那個
        self.surface, self.ledge, self.vx, self.riding = None, None, 0.0, False
        self.vy = -math.sqrt(2 * self.G * max(h, 10))

    def chase(self, now, lo, hi):
        """游標在附近晃的時候偶爾追過去，碰到就開心跳一下。追的時候回傳 True。"""
        mx, my = self.cursor
        if now < self.chase_until:
            left, right = lo, hi
            cur = self.find_ledge(self.ledge) if self.surface == 'perch' else None
            if cur:                                        # 在視窗上就只在視窗上追，不會追到掉下去
                left, right = cur[1], cur[2]
            if abs(mx - self.x) < 14 * self.S and abs(my - (self.y - 4 * self.c)) < 120 * self.S:
                self.chase_until, self.next_chase, self.goal = 0.0, now + 25, None
                self.react_until, self.react_text = now + 1.5, '抓到了！'
                self.hop(9)
                return True
            self.goal, self.riding, self.climb = min(max(mx, left), right), False, None
            return True
        if self.chase_until:                               # 時間到了還沒追到，放棄
            self.chase_until, self.next_chase, self.goal = 0.0, now + 25, None
        near = abs(mx - self.x) < 300 * self.S and -350 * self.S < my - self.y < 60 * self.S
        moving = math.hypot(mx - self.last_cursor[0], my - self.last_cursor[1]) > 2 * self.S
        if now > self.next_chase and near and moving and not self.riding and random.random() < 0.03:
            self.chase_until = now + 6
            return True
        return False

    def drop_height(self, floor):
        """離正下方能落腳的地方（視窗上緣或地面）還有多高。"""
        below = [lg[3] for lg in self.ledges() if lg[1] <= self.x <= lg[2] and lg[3] >= self.y
                 and lg[0] != self.left_ledge and screen.edge_visible(lg[0], self.x, lg[3])]
        return min(below + [floor]) - self.y

    def physics(self, now):
        if self.dragging:
            return
        floor = screen.area_at(self.x, self.y - 1)[2][3]  # 腳下那個螢幕的地面
        lo, hi = self.bounds()
        if self.surface == 'perch':
            cur = self.find_ledge(self.ledge)
            if (not cur or not cur[1] <= self.x <= cur[2] or abs(self.y - cur[3]) > 60 * self.S
                    or not screen.edge_visible(cur[0], self.x, cur[3])):
                self.surface, self.vy, self.ledge = None, 0.0, None   # 走出邊緣、視窗移開或被蓋住了：掉下去
            else:
                self.y = cur[3]                            # 跟著視窗移動
        elif self.surface == 'floor':
            if self.y < floor - 2:                         # 工作列收起來、走到比較矮的螢幕：掉下去
                self.surface, self.vy = None, 0.0
            else:
                self.y = floor
        if self.surface is None:
            prev = self.y
            if self.chute:                                 # 降落傘：減速到慢慢飄下來，跟著傘左右擺
                self.vy += (3 * self.S - self.vy) * 0.12
                self.vx *= 0.94
                self.x += 0.8 * self.S * math.sin(self.t / 9)
            else:
                self.vy = min(self.vy + self.G, 40 * self.S)
                if self.vy > 0 and self.drop_height(floor) > 260 * self.S:   # 從高處往下掉才開傘
                    self.chute = True
            self.y += self.vy
            self.x += self.vx
            self.vx *= 0.995                               # 一點空氣阻力，軌跡仍是拋物線
            if not lo <= self.x <= hi:                     # 撞到螢幕邊緣就彈回來，撞得太用力會頭暈
                if abs(self.vx) > 14 * self.S:
                    self.dizzy_until = now + 2.5
                self.vx = -self.vx * 0.5
            ceiling = screen.ceiling_at(self.x) + ROWS * self.c   # 頭頂碰到螢幕頂端就彈回來，一樣撞太用力會頭暈
            if self.y < ceiling:
                self.y = ceiling
                if self.vy < 0:
                    if self.vy < -14 * self.S:
                        self.dizzy_until = now + 2.5
                    self.vy = -self.vy * 0.4
            if self.vy > 0:
                floor = screen.area_at(self.x, self.y - 1)[2][3]
                ledge = self.landing(prev)
                if ledge:
                    self.land('perch', ledge[3], now, ledge[0])
                elif self.y >= floor:
                    self.land('floor', floor, now)
        else:
            self.rest_y = self.y
        self.x = min(max(self.x, lo), hi)
        self.land_sq *= 0.75

    def land(self, surface, y, now, ledge=None):
        self.chute, self.left_ledge = False, None
        self.land_sq = min(0.25, 0.04 + abs(self.vy) / (60 * self.S))
        if self.vy > 20 * self.S:                          # 被用力往下砸：頭暈
            self.dizzy_until = now + 2.5
        if self.vy > 12 * self.S:                          # 摔得重就彈一下，順勢再滑一段
            self.y, self.vy, self.vx = y, -self.vy * 0.3, self.vx * 0.6
            return
        self.surface, self.ledge, self.y, self.vx, self.vy = surface, ledge, y, 0.0, 0.0

    def hide_in_corner(self):
        """前景程式全螢幕時，縮小坐在那個螢幕的右下角，不擋畫面。"""
        mon = screen.area_at(self.x, self.y - 1)[1]
        cs = max(2, round(self.c / 2))
        self.x, self.y = mon[2] - 7 * cs, mon[3]
        self.surface, self.ledge, self.goal, self.climb = 'floor', None, None, None
        self.vx = self.vy = 0.0
        self.riding = self.chute = False
        self.chase_until = 0.0

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
        if mode == 'idle' and not self.walking and self.surface is not None:   # 站著發呆時眼睛跟著游標
            dx, dy = self.cursor[0] - self.x, self.cursor[1] - (self.y - 5 * self.c)
            d = math.hypot(dx, dy)
            if 0 < d < 700 * self.S:
                lx, ly = 0.35 * dx / d, 0.35 * dy / d

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
        if now < self.dizzy_until:
            eyes, bubble, arm_l, arm_r = 'dizzy', None, 'nub', 'nub'
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
        if self.chute and not self.dragging:               # 雙手拉著降落傘的繩子
            arm_l, arm_r, bubble = 'high', 'high', None
            eyes = 'normal' if eyes == 'big' else eyes
        if now < self.react_until:
            eyes, bubble = 'happy', self.react_text
        if self.hiding:                                    # 縮在全螢幕程式的角落：不講話，畫小一號
            bubble = None

        typing = (mode == 'working' and not self.walking and self.surface is not None and not self.dragging
                  and not self.card and now >= self.react_until and now >= self.dizzy_until)
        if not typing:
            self.type_start = None
        elif self.type_start is None:
            self.type_start = now

        cv = self.cv
        cv.delete('all')
        cx, by = self.W / 2, self.H - 1
        lift = 0                                           # 騎車時身體抬高，泡泡跟著往上
        full_c = self.c
        if self.hiding:
            self.c = max(2, round(full_c / 2))
        try:
            if typing:
                self.draw_typing(cx, by, now - self.type_start, t)
            elif self.riding:
                lift = self.draw_bike(cx, by, eyes, t)
            else:
                if self.chute and not self.dragging:
                    self.draw_chute(cx, by, t)
                self.draw_mascot(cx, by, eyes, lx, ly, arm_l, arm_r, run, sq)
            if eyes == 'dizzy':
                self.draw_stars(cx, by, t)
            if bubble:
                self.draw_bubble(cx, by - 8 * self.c - 2 * self.S - lift, bubble)
        finally:
            self.c = full_c

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
        if now >= self.next_top:                           # 別的視窗可能蓋過來，每半秒重申一次置頂（不搶焦點）
            self.next_top = now + 0.5
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
            elif eyes == 'dizzy':                          # 叉叉眼
                w = max(2, round(0.35 * c))
                self.cv.create_line(ex - 0.2 * cw, ey + 0.2 * ch, ex + 1.2 * cw, ey + 1.6 * ch, fill=INK, width=w)
                self.cv.create_line(ex - 0.2 * cw, ey + 1.6 * ch, ex + 1.2 * cw, ey + 0.2 * ch, fill=INK, width=w)

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

        def arm(*steps):                                   # 從肩膀一格一格斜著接下去，相鄰兩格要重疊才不會斷
            box(0, 3, 1, 1, CLAY)
            for col, row, h in steps:
                box(col, row, 1, h, CLAY)

        if phase < 0.25:                                   # 開場一：前手往外伸再往上彎，闔著的筆電平放在手上
            self.draw_mascot(cx, by, 'normal', 0, 0, 'nub', 'nub', None, 0)
            arm((-1, 1, 3))
            line([(-3.4, 0.78), (0.2, 0.78)])
            return
        if phase < 0.45:                                   # 開場二：手肘彎下去，把筆電放到地上
            self.draw_mascot(cx, by, 'normal', 0, 0.3, 'nub', 'nub', None, 0)
            arm((-0.6, 3.6, 1), (-1.1, 4.3, 1), (-1.4, 5.1, 1.5))
            line([(-3.6, 6.8), (-0.4, 6.8)])
            return
        line([(-4.4, 4.2), (-2.6, 6.8), (0.8, 6.8)])      # 打開的筆電擺在腳前：往後仰的螢幕＋平放的鍵盤
        box(1, 0, 8, 5, CLAY)                              # 正面
        box(9, 0, 2, 5, SIDE)                              # 側面
        for col, color in ((2, CLAY), (4.5, CLAY), (7.5, CLAY), (9.5, SIDE)):
            box(col, 5, 1, 2, color)
            box(col - 0.4, 6.4, 0.5, 0.6, color)           # 腳尖朝前
        box(1, 1.2, 1, 1.6, INK)                           # 眼睛偏向前方
        box(5, 1.2, 1, 1.6, INK)
        if (0b1011010011010110 >> (t // 3 % 16)) & 1:      # 前手敲鍵盤，節奏刻意不規律
            arm((-0.6, 3.6, 1), (-1.1, 4.3, 1), (-1.4, 5.1, 1.5))     # 手肘往下，手掌壓在鍵盤上
        else:
            arm((-0.7, 2.7, 1), (-1.3, 3.3, 1), (-1.6, 4.1, 1.1))     # 手肘抬起，手掌離開鍵盤

    def draw_bike(self, cx, by, eyes, t):
        """側身騎腳踏車：3/4 側面的身體坐在座墊上，前手彎著握把手，兩腳跟著踏板一上一下。回傳身體抬高的量。"""
        c, f = self.c, self.facing
        R, lift = 2.2 * c, 3.2 * c
        w = max(2, round(0.45 * c))

        def P(dx, dy):                                     # 以格為單位：dx 往前為正、dy 往上為正
            return cx + f * dx * c, by - dy * c

        def B(dx0, dx1, dy0, dy1, color):                  # 前後 dx0–dx1、上下 dy0–dy1 的方塊
            (x0, y0), (x1, y1) = P(dx0, dy0), P(dx1, dy1)
            self.rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), color)

        def leg(pedal, color):
            px, py = pedal
            B(px - 0.5, px + 0.5, py, 5.4, color)           # 從屁股往下踩到踏板
            B(px + 0.5, px + 0.9, py, py + 0.5, color)      # 腳尖朝前

        a = t * 0.35                                       # 踏板與輪子的角度，往前騎是順著行進方向轉
        crank = (0, 2.6)
        pedals = [(crank[0] + 0.9 * math.cos(-a + k * math.pi), crank[1] + 0.9 * math.sin(-a + k * math.pi))
                  for k in (0, 1)]
        leg(pedals[1], SIDE)                               # 另一邊的腳在車架後面
        for hub in (-4.4, 4.4):                            # 輪子＋轉動的輻條
            hx, hy = P(hub, 2.2)
            self.cv.create_oval(hx - R, hy - R, hx + R, hy + R, outline=TIRE, width=w)
            for k in range(2):
                ang = -a + k * math.pi / 2
                dx, dy = 1.76 * math.cos(ang), 1.76 * math.sin(ang)
                self.cv.create_line(*P(hub - dx, 2.2 - dy), *P(hub + dx, 2.2 + dy), fill=LAPTOP, width=max(1, w // 2))
        rear, mid, seat = P(-4.4, 2.2), P(*crank), P(-1.4, 5.0)
        head, front, grip = P(3.0, 5.0), P(4.4, 2.2), P(4.9, 5.6)
        for p0, p1 in ((rear, mid), (rear, seat), (seat, mid), (seat, head), (mid, head), (head, front), (head, grip)):
            self.cv.create_line(*p0, *p1, fill=BIKE, width=w, capstyle='round')
        self.cv.create_line(*P(4.4, 5.6), *P(5.8, 5.6), fill=INK, width=w, capstyle='round')   # 把手
        B(-2.4, -0.4, 5.0, 5.4, INK)                       # 座墊
        B(-4.5, 3.5, 5.2, 10.2, CLAY)                      # 正面朝前
        B(-6.5, -4.5, 5.2, 10.2, SIDE)                     # 側面
        for ex in (2.5, -1.5):                             # 眼睛偏向前方
            if eyes == 'blink':
                B(ex, ex + 1, 8.0, 8.4, INK)
            else:
                B(ex, ex + 1, 7.4, 9.0, INK)
        self.cv.create_line(*P(*pedals[0]), *P(*pedals[1]), fill=INK, width=w)   # 曲柄
        leg(pedals[0], CLAY)                               # 這一邊的腳在最前面
        B(3.5, 4.3, 6.2, 7.2, CLAY)                        # 前手：從肩膀往前下彎到把手
        B(4.0, 5.0, 5.7, 6.7, CLAY)
        B(4.5, 5.5, 5.2, 6.2, CLAY)
        return lift

    def draw_chute(self, cx, by, t):
        """降落傘：奶油色與陶土色相間的傘面，四條繩子拉到兩隻高舉的手。"""
        c, S = self.c, self.S
        x0 = cx + 0.5 * c * math.sin(t / 9)                # 傘面比身體多晃一點
        hw, base, h = 7 * c, by - 10.2 * c, 3.8 * c
        lw = max(1, round(S))
        hands = (cx - 6.5 * c, by - 8 * c), (cx + 6.5 * c, by - 8 * c)
        for ex, hand in ((-1, hands[0]), (-0.35, hands[0]), (0.35, hands[1]), (1, hands[1])):
            self.cv.create_line(x0 + ex * hw, base, *hand, fill=LAPTOP, width=lw)
        for i in range(6):
            self.cv.create_arc(x0 - hw, base - h, x0 + hw, base + h, start=i * 30, extent=30,
                               fill=CREAM if i % 2 else CLAY, outline=INK, width=lw)

    def draw_stars(self, cx, by, t):
        """頭暈時在頭上繞圈的星星。"""
        c = self.c
        for k in range(3):
            a = t / 6 + k * 2 * math.pi / 3
            x, y, r = cx + 4.5 * c * math.cos(a), by - 7.6 * c + 0.9 * c * math.sin(a), 0.7 * c
            q = 0.3 * r
            self.cv.create_polygon(x, y - r, x + q, y - q, x + r, y, x + q, y + q, x, y + r, x - q, y + q, x - r, y,
                                   x - q, y - q, fill=STAR, outline='')

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
        slow = False
        try:
            now = time.time()
            self.poll_state(now)
            self.poll_requests(now)
            mode = self.cur_mode = self.mode(now)
            self.cursor = screen.cursor()
            hmon = screen.area_at(self.x, self.y - 1)[0]
            menu_open = self.menu is not None and not self.menu.closed
            self.hiding = (screen.fullscreen_monitor() == hmon and not ipc.claude_is_foreground()   # Claude 自己全螢幕不算
                           and not self.card and not self.drag and not menu_open)
            if self.hiding:
                self.hide_in_corner()
            else:
                self.think(mode, now)
                self.physics(now)
            self.render(mode, now)
            self.last_cursor = self.cursor
            # 睡覺或縮在角落時沒什麼在動：每秒只畫 10 次省電，動畫時間照樣往前推
            slow = ((mode == 'sleep' or self.hiding) and self.surface is not None and not self.drag
                    and not self.card and not menu_open)
            self.t += 3 if slow else 1
        finally:
            self.root.after(100 if slow else 1000 // FPS, self.tick)

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
