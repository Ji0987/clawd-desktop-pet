"""Clawd 桌寵：在 Windows 桌面上走來走去，並依 Claude Code hook 寫出的狀態切換動作。

狀態檔：~/.clawd/sessions/<session>.json，每個 Claude Code 對話一個（由 clawd_hook.py 寫入）
  thinking 想事情｜working 跑工具｜alert 等你回應｜done 回覆完成；沒有進行中的對話就是 idle 閒晃
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

import clawd_art as art
import clawd_ipc as ipc
import clawd_screen as screen
import clawd_sprites as sprites
import clawd_setup
from fluent_ui import FluentMenu, RequestCard

FPS = 30
CONFIG_FILE = ipc.BASE / 'config.json'      # 桌寵自己的設定（例如問過要不要連動了沒）
KEY = '#FF00FE'                       # 透明色鍵：這個顏色的像素會變透明且點擊穿透
CLAY, INK, CREAM, STRING = '#D97757', '#1F1D1A', '#FFF8EC', '#8E8E8E'   # 泡泡、降落傘的顏色（Clawd 本身的顏色在 clawd_sprites.py）
STAR = '#F2C14E'                           # 頭暈的星星
PRIORITY = {'alert': 4, 'working': 3, 'thinking': 2, 'done': 1}   # 多個 session 同時進行時，顯示最需要注意的那個
# 狀態超過這麼多秒沒更新就不算：工具可能跑很久；完成的泡泡最多等你 10 分鐘
LIFETIME = {'alert': 600, 'working': 1800, 'thinking': 600, 'done': 600}
CHEER = 3                             # 完成後開心跳的秒數；之後安靜地帶著「完成」泡泡，直到你回 Claude 或點牠
SESSION_GC = 6 * 3600                 # 這麼久沒更新的 session 檔（多半是被強制關掉的）直接清掉
SLEEP_AFTER = 120                     # 閒置這麼多秒後睡覺
SLEEP_AFTER_NIGHT = 40                # 晚上 10 點到早上 6 點比較愛睡

ROWS = 7                              # 站著時身體加腳的高度（格），撞天花板用
SIDE_POSES = {'side', 'turn', 'walk', 'run', 'type', 'sleep', 'sit', 'sip', 'read', 'bike'}   # 側面的動作（美術缺圖時的替代）
FIDGETS = {'look': 1.6, 'stretch': 1.6, 'yawn': 1.8, 'sit': 7, 'sip': 7, 'read': 9}   # 發呆的小動作與長度（秒）
CONFETTI = ('#F2C14E', '#E05A47', '#5BBF7A', '#8E7AE0', '#3E7CB1', '#FFF8EC')

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
        path = ipc.BASE / 'error.log'
        if path.exists() and path.stat().st_size > 256 * 1024:   # 太大就換一份新的，舊的留一份
            os.replace(path, path.with_suffix('.log.1'))
        with open(path, 'a', encoding='utf-8') as f:
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


def duration_text(sec):
    """完成泡泡上的耗時：一分鐘以內不顯示。"""
    sec = int(sec)
    if sec < 60:
        return ''
    if sec < 3600:
        return '%d 分 %d 秒' % (sec // 60, sec % 60)
    return '%d 小時 %d 分' % (sec // 3600, sec % 3600 // 60)


class Pet:
    def __init__(self, scale):
        self.S = scale
        self.c = max(3, round(6 * scale))                  # 一個像素格的實際邊長
        self.W, self.H = round(200 * scale), round(19 * self.c + 10 * scale)   # 降落傘、騎車加泡泡都放得下
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
        self.react_until, self.react_text, self.react_pose = 0.0, '', None

        self.drag = None                                   # 按下時的 (指標x, 指標y, 寵物x, 寵物y)
        self.dragging = False
        self.drag_hist = []                                # 最近的 (時間, 指標x, 指標y)，放手時算甩出去的速度
        self.rest_y = self.y                               # 最後站穩的高度，卡片以此定位，不跟著跳

        self.card, self.card_id = None, None               # 頭上的權限／提問卡片
        self.answered = set()
        self.local_reqs = []                               # 桌寵自己要問的事：(請求, 收到回答時的動作)
        self.next_req = self.next_sweep = 0.0

        self.sessions, self.sess_mtimes = {}, {}           # 各 Claude Code 對話的狀態檔內容與修改時間
        self.tool, self.done_text = '', ''
        self.last_work_ts = 0.0
        self.done_ts, self.done_seen = 0.0, 0.0            # 最近一次完成的時間；你已經看過的完成（不再提醒）
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
        self.chase_until, self.next_chase = 0.0, time.time() + 60   # 追游標
        self.fidget, self.fidget_until, self.next_fidget = None, 0.0, time.time() + 8   # 站著發呆時的小動作
        self.next_wave = 0.0                               # 游標靠近時揮手打招呼
        self.hang_plan, self.hang_until = None, 0.0        # 要去吊的 (視窗, x)：走到視窗邊緣吊在上緣晃一晃
        self.was_walking, self.turn_until = False, 0.0     # 起步與停下時插一格轉身
        self.close_until = 0.0                             # 打完字闔上筆電
        self.done_long = False                             # 這次的工作很久（完成時舉獎盃）
        self.confetti = []                                 # 完成時的彩帶 [dx, dy, vx, vy, 顏色]
        self.sprite_cache = {}                             # 畫好的像素圖（PhotoImage），依最近使用排序
        self.hiding = False                                # 有全螢幕程式時縮到角落
        self.cur_mode = 'idle'
        self.next_top = 0.0
        self.last_tick = 0.0

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
        """重讀有變動的 session 狀態檔；被刪掉（對話結束）的就移除。"""
        if now < self.next_poll:
            return
        self.next_poll = now + 0.2
        mtimes = {}
        for path in ipc.SESS_DIR.glob('*.json'):
            try:
                mtimes[path.name] = path.stat().st_mtime_ns
            except OSError:                                # 剛好被刪掉：只略過這一個，其他對話照常
                pass
        # 檔案時間的精度只有十幾毫秒：一秒內剛改過的檔案每次都重讀，才不會漏掉緊接著的第二次寫入
        recent = lambda m: m is None or now - m / 1e9 < 1
        if mtimes == self.sess_mtimes and not any(recent(m) for m in mtimes.values()):
            return
        for name in set(self.sessions) - set(mtimes):
            del self.sessions[name]
        changed = False
        for name, mtime in mtimes.items():
            if self.sess_mtimes.get(name) == mtime and not recent(mtime):
                continue
            data = ipc.read_json(ipc.SESS_DIR / name)
            try:
                ts = float(data.get('ts', 0)) if isinstance(data, dict) else 0.0
            except (TypeError, ValueError):
                ts = 0.0
            if not ts:
                mtimes[name] = None                        # 讀不到（正在寫入之類），下次再試
                continue
            if now - ts > SESSION_GC:
                (ipc.SESS_DIR / name).unlink(missing_ok=True)
                continue
            data = dict(data, ts=ts)
            if data == self.sessions.get(name):
                continue
            self.sessions[name] = data
            if now - ts < 5:                               # 剛剛發生的事才算活動，啟動時讀到的舊檔不算
                changed = True
                if data.get('state') == 'working':
                    self.last_work_ts = ts
        self.sess_mtimes = mtimes
        if changed:
            self.last_activity = now
            self.sleeping = self.forced_sleep = False

    def merged_state(self, now):
        """所有對話合起來要顯示的 (狀態, 那個對話的資料)；沒有進行中的就是 ('idle', None)。"""
        best, pick = None, ('idle', None)
        for data in self.sessions.values():
            state, ts = data.get('state'), data['ts']
            if state not in PRIORITY or now - ts > LIFETIME[state]:
                continue
            if state == 'done' and ts <= self.done_seen:   # 你已經看過的完成不再提醒
                continue
            key = (PRIORITY[state], ts)
            if best is None or key > best:
                best, pick = key, (state, data)
        return pick

    def mode(self, now):
        m, data = self.merged_state(now)
        self.tool = str(data.get('tool') or '') if data else ''
        if m == 'thinking' and now - self.last_work_ts < 1.5:   # 工具之間的空檔不要閃來閃去
            m = 'working'
        if m == 'done':
            if data['ts'] != self.done_ts:                 # 新的完成：記下耗時，開心跳幾下
                self.done_ts, self.next_hop = data['ts'], 0.0
                start = data.get('start')
                took = duration_text(self.done_ts - start) if isinstance(start, (int, float)) else ''
                self.done_text = '完成 · ' + took if took else '完成！'
                self.done_long = isinstance(start, (int, float)) and self.done_ts - start > 300
                self.confetti = [[0.0, -9.0 * self.c, random.uniform(-3, 3) * self.S, random.uniform(-8, -4) * self.S,
                                  random.choice(CONFETTI)] for _ in range(18)]
            if now - self.done_ts > CHEER and ipc.claude_is_foreground():   # 你回到 Claude 看過了
                self.done_seen = self.done_ts
                m = 'idle'
        if m == 'idle':
            night = is_night(now)
            if not self.sleeping and now - self.last_activity > (SLEEP_AFTER_NIGHT if night else SLEEP_AFTER):
                self.sleeping = True
                if night:
                    self.say('呵～好睏', now + 2, 'yawn')
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

    def live_ledge(self, hwnd):
        """站著的那個視窗現在的上緣（不經過快取，拖動視窗時腳底才跟得上）。"""
        lg = screen.ledge_of(hwnd, 16 * self.c, 9 * self.c)
        return (lg[0], lg[1] + 5 * self.c, lg[2] - 5 * self.c, lg[3]) if lg else None

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
            self.hang_plan = None
            self.chase_until = self.fidget_until = 0.0
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
            if self.cur_mode == 'done':                    # 點過就算看到了，不再帶著完成泡泡
                self.done_seen = self.done_ts
            self.say(text, now + 1.5, random.choice(['heart', 'front_up', None]))
            self.hop(10)
        self.drag = None

    def say(self, text, until, pose=None):
        """頭上冒一句話，可以順便擺個姿勢（姿勢名稱見 clawd_art.py）。"""
        self.react_text, self.react_until, self.react_pose = text, until, pose

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
            self.say('找不到 Claude 桌面版', time.time() + 2.5, 'front_shrug')

    def go_ride(self):
        """下次出門騎腳踏車；在 Claude 視窗上的話會先走下來。"""
        self.wake(time.time())
        self.want_ride, self.goal, self.next_wander = True, None, 0.0

    def toggle_autostart(self, on):
        try:
            clawd_setup.set_autostart(on)
            text = '開機會自動出現！' if on else '開機不會自動出現了'
        except OSError:
            log_exception(*sys.exc_info())
            text = '設定失敗（見 error.log）'
        self.say(text, time.time() + 2.5)

    def quit(self):
        """結束前把還在等的卡片交回 Claude，不讓 hook 白等。"""
        try:
            for path in ipc.REQ_DIR.glob('*.json'):
                req = ipc.read_json(path)
                if (isinstance(req, dict) and req.get('id') and req['id'] not in self.answered
                        and ipc.pid_alive(req.get('pid', 0))):
                    try:
                        ipc.write_json(ipc.RESP_DIR / (req['id'] + '.json'), {'id': req['id'], 'decision': 'release'})
                    except OSError:
                        pass
        finally:                                           # 交不回去也一定要結束
            self.root.destroy()

    def open_menu(self, e):
        if self.menu:
            self.menu.close()
        asleep = self.sleeping or self.forced_sleep
        linked = clawd_setup.status() == 'on'
        try:
            auto = clawd_setup.autostart_status() == 'on'
        except OSError:
            auto = False
        check = lambda on: '' if on else ''         # 開關類的項目：開著時在圖示欄打勾
        items = [('' if asleep else '', '起床' if asleep else '睡覺', self.toggle_sleep),
                 ('', '騎腳踏車兜風', self.go_ride),
                 None,
                 ('', '開啟 Claude', self.open_claude),
                 (check(linked), '與 Claude Code 連動', lambda: self.set_link(not linked)),
                 (check(auto), '開機時自動啟動', lambda: self.toggle_autostart(not auto)),
                 None,
                 ('', '結束 Clawd', self.quit)]
        self.menu = FluentMenu(self.root, self.S, items, e.x_root, e.y_root)

    # ───────── 與 Claude Code 連動 ─────────
    def check_link(self):
        """搬過資料夾或換了 Python 就默默改成新路徑；從沒問過就問一次要不要連動。"""
        try:
            if clawd_setup.autostart_status() == 'moved':  # 開機啟動也跟著換成新路徑
                clawd_setup.set_autostart(True)
        except OSError:
            pass
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
        except Exception:                                  # 多半是 settings.json 格式有誤：不去覆寫，原因記進 error.log
            log_exception(*sys.exc_info())
            text = '設定檔有誤，見 error.log'
        if not quiet:
            self.say(text, time.time() + 3)

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
        if now > self.next_sweep:                          # hook 先結束才作答之類，留下沒人讀的回答：一分鐘後清掉
            self.next_sweep = now + 30
            for path in ipc.RESP_DIR.glob('*.json'):
                try:
                    if not (ipc.REQ_DIR / path.name).exists() and now - path.stat().st_mtime > 60:
                        path.unlink()
                except OSError:
                    pass
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
            self.say('收到！', time.time() + 1.5)

    def close_card(self):
        if self.card:
            self.card.close()
        self.card, self.card_id = None, None

    def card_anchor(self):
        return self.x, self.rest_y - 11 * self.c - 6 * self.S   # 站著的姿勢最高約 11 格（舉高的手、驚嘆號）

    def hop(self, power):
        if self.surface is not None:
            self.surface, self.riding = None, False
            self.vy = -power * self.S

    # ───────── 行為 ─────────
    def think(self, mode, now):
        self.walking = False
        if self.dragging or self.surface is None:
            return
        if self.surface == 'hang':                         # 吊在視窗上緣：時間到、有事要忙就放手
            if now > self.hang_until or mode != 'idle' or self.card:
                self.surface, self.ledge, self.vy = None, None, 0.0
            return
        if now < self.close_until:                         # 先把筆電收好再走
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
            if claude and max(lo, claude[1]) > min(hi, claude[2]):   # 視窗上緣整段都在走不到的地方
                claude = None
            if claude:                                     # 去 Claude 視窗上緣陪著
                self.goal = min(max(self.ledge_spot(claude), lo, claude[1]), hi, claude[2])
                self.climb = None if self.ledge == claude[0] else claude[0]
            elif self.claude_rect:                         # 站不上去（最大化之類）就待在視窗正下方
                gx = min(max((self.claude_rect[0] + self.claude_rect[2]) / 2, lo), hi)
                self.goal, self.climb = (gx if abs(self.x - gx) > 4 else None), None
            else:
                self.goal = self.climb = None
            if mode == 'alert' and self.goal is None and now > self.next_hop:
                self.next_hop = now + 1.0
                self.hop(8)
        elif mode == 'done' and now - self.done_ts < CHEER:   # 剛完成先原地開心跳，之後帶著泡泡照常閒晃
            self.goal = self.climb = None
            if now > self.next_hop:
                self.next_hop = now + 0.7
                self.hop(9)
        elif mode == 'sleep':
            self.goal = self.climb = None
        elif not self.chase(now, lo, hi) and self.goal is None:
            mx, my = self.cursor
            near = abs(mx - self.x) < 160 * self.S and -250 * self.S < my - self.y < 40 * self.S
            moving = math.hypot(mx - self.last_cursor[0], my - self.last_cursor[1]) > 3 * self.S
            if near and moving and now > self.next_wave and now > self.fidget_until:   # 游標靠過來就揮手打招呼
                self.fidget, self.fidget_until, self.next_wave = 'wave', now + 1.4, now + 45
            elif now < self.fidget_until:                  # 正在坐著喝咖啡、看書之類，做完再走
                pass
            elif now > self.next_wander:
                self.wander(lo, hi)
            elif now > self.next_fidget and now > self.react_until:   # 站著發呆時偶爾找點事做
                self.fidget = random.choice(['look', 'look', 'stretch', 'yawn', 'sit', 'sip', 'read'])
                self.fidget_until, self.next_fidget = now + FIDGETS[self.fidget], now + random.uniform(10, 25)
                self.next_wander = max(self.next_wander, self.fidget_until + 1)
        if self.goal is not None:                          # 目的地一律限制在走得到的範圍，不會對著牆原地踏步
            self.goal = min(max(self.goal, lo), hi)
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
                if self.hang_plan == (self.ledge, self.x) and self.surface == 'perch' and mode == 'idle':
                    self.start_hang(now)
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
            exits = [g for g in (cur[1] - 3 * self.c, cur[2] + 3 * self.c) if lo <= g <= hi] if cur else []
            if exits and self.want_ride:                   # 要去騎車：從比較近的那一邊走下去，落地後馬上出發
                self.goal = min(exits, key=lambda g: abs(g - self.x))
            elif cur and random.random() < 0.15 and 'hang_a' in art.ART:   # 走到邊緣吊在上緣晃一晃
                self.goal = random.choice((cur[1], cur[2]))
                self.hang_plan = (self.ledge, self.goal)
            elif cur and random.random() < 0.6:
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

    def hang_drop(self):
        """吊著時，手（錨點）到圖底邊的距離：桌寵的 y 是圖的底邊，手要對齊視窗上緣。"""
        w, h = sprites.size('hang_a')
        return (h - art.ART['hang_a'].get('anchor', (w / 2, h))[1]) * max(1, round(self.c / 2))

    def start_hang(self, now):
        self.hang_plan = None
        cur = self.live_ledge(self.ledge)
        if cur:
            self.surface, self.hang_until = 'hang', now + random.uniform(3, 6)
            self.y = cur[3] + self.hang_drop()

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
                self.chase_until, self.next_chase, self.goal = 0.0, now + 90, None
                self.say('抓到了！', now + 1.5, 'front_up')
                self.hop(9)
                return True
            self.goal, self.riding, self.climb = min(max(mx, left), right), False, None
            return True
        if self.chase_until:                               # 時間到了還沒追到，放棄
            self.chase_until, self.next_chase, self.goal = 0.0, now + 90, None
        near = abs(mx - self.x) < 300 * self.S and -350 * self.S < my - self.y < 60 * self.S
        moving = math.hypot(mx - self.last_cursor[0], my - self.last_cursor[1]) > 2 * self.S
        # 游標在附近晃平均十秒左右才追一次，追完休息一分半，不會一直干擾你操作
        if now > self.next_chase and near and moving and not self.riding and random.random() < 1 / (10 * FPS):
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
            cur = self.live_ledge(self.ledge)
            if (not cur or not cur[1] <= self.x <= cur[2] or abs(self.y - cur[3]) > 60 * self.S
                    or not screen.edge_visible(cur[0], self.x, cur[3])):
                self.surface, self.vy, self.ledge = None, 0.0, None   # 走出邊緣、視窗移開或被蓋住了：掉下去
            else:
                self.y = cur[3]                            # 跟著視窗移動
        elif self.surface == 'hang':
            cur = self.live_ledge(self.ledge)
            if not cur or not screen.edge_visible(cur[0], self.x, cur[3]):
                self.surface, self.vy, self.ledge = None, 0.0, None
            else:
                self.y = cur[3] + self.hang_drop()
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
        if self.want_ride:                                 # 從視窗上走下來準備去騎車：落地就出發
            self.goal, self.next_wander = None, 0.0

    def hide_in_corner(self):
        """前景程式全螢幕時，縮小坐在那個螢幕的右下角，不擋畫面。"""
        mon = screen.area_at(self.x, self.y - 1)[1]
        p = max(1, round(max(2, round(self.c / 2)) / 2))   # 縮小一半後的小格大小（同 sprite() 的算法）
        self.x, self.y = mon[2] - sprites.size('peek')[0] / 2 * p - 2 * self.S, mon[3]
        self.surface, self.ledge, self.goal, self.climb, self.left_ledge = 'floor', None, None, None, None
        self.vx = self.vy = 0.0
        self.riding = self.chute = self.walking = False
        self.chase_until = 0.0

    # ───────── 繪製 ─────────
    def pose(self, mode, now):
        """這一格要畫哪張像素圖：(圖名, 眼睛, 眼睛看的方向, 壓扁拉長, 泡泡文字)。"""
        t = self.t
        name, eyes, look, bubble = 'front', 'normal', (0, 0), None
        sq = self.land_sq
        if self.surface is None and not self.dragging and not self.chute and self.land_sq < 0.02:
            sq = -min(0.12, abs(self.vy) / (90 * self.S))  # 在空中依速度拉長一點，落地再壓扁
        if now > self.next_blink:
            self.blink_until, self.next_blink = now + 0.15, now + random.uniform(2.5, 6)
        standing = self.surface in ('floor', 'perch') and not self.dragging

        if mode == 'sleep':
            name, eyes = ('sleep_a', 'sleep_b')[(t // 25) % 2], 'sleep'
            bubble = 'Z' + 'z' * (1 + (t // 20) % 3)
        elif mode == 'thinking':
            name, look, bubble = 'front_think', (self.facing, -1), '.' * (1 + (t // 8) % 3)
        elif mode == 'working':
            bubble = self.tool or '工作中'
        elif mode == 'alert':
            eyes, bubble = 'big', '需要你！'
            name = 'point' if (t // 45) % 3 == 2 else ('front_high_a', 'front_high_b')[(t // 6) % 2]
        elif mode == 'done':
            bubble = self.done_text or '完成！'
            k = now - self.done_ts
            if k < CHEER:                                  # 跳起來是 V 字，落地蹲一下
                name, eyes = ('cheer_a' if self.surface is None else 'cheer_b'), 'happy'
            elif k < CHEER + 3:                            # 接著跳支舞；很久的工作就舉獎盃
                name = 'trophy' if self.done_long else 'dance_%d' % (1 + (t // 5) % 4)
                eyes = 'happy'
        elif mode == 'idle' and standing and not self.walking:
            act = self.fidget if now < self.fidget_until else None
            k = 1 - (self.fidget_until - now) / FIDGETS.get(act, 1.4) if act else 0
            if act == 'look':                              # 先看左邊再看右邊
                look = (-1 if k < 0.5 else 1, 0)
            elif act == 'stretch':
                name, eyes = 'front_stretch', 'happy'
            elif act == 'yawn':
                name, eyes = 'yawn', 'squint'
            elif act == 'sit':
                name = 'sit'
            elif act == 'sip':                             # 隔一陣子舉杯喝一口
                name = 'sip_b' if (t // 30) % 3 == 1 else 'sip_a'
            elif act == 'read':                            # 看書，偶爾翻頁
                name, look = ('read_b' if (t // 45) % 4 == 3 else 'read_a'), (0, 1)
            elif act == 'wave':
                name, eyes = ('front_wave_a', 'front_wave_b')[(t // 6) % 2], 'happy'
            else:                                          # 眼睛跟著游標
                dx, dy = self.cursor[0] - self.x, self.cursor[1] - (self.y - 5 * self.c)
                if math.hypot(dx, dy) < 700 * self.S:
                    look = (max(-1, min(1, round(dx / (120 * self.S)))), max(-1, min(1, round(dy / (120 * self.S)))))

        if now < self.dizzy_until:
            name, eyes, bubble = 'front', 'dizzy', None
        if self.card:                                      # 卡片取代泡泡，Clawd 揮手引起注意
            name, eyes, bubble = ('front_high_a', 'front_high_b')[(t // 6) % 2], 'big', None
        if self.walking or now < self.turn_until:          # 起步、停下各插一格轉身
            busy = mode in ('thinking', 'working', 'alert') or now < self.chase_until
            name = 'turn' if now < self.turn_until else ('run_%d' if busy else 'walk_%d') % (1 + (t // (2 if busy else 4)) % 4)
            look = (0, 0)
        if self.dragging:
            name, eyes = ('drag_a', 'drag_b')[(t // 4) % 2], 'big'
        elif self.surface == 'hang':
            name = ('hang_a', 'hang_b')[(t // 12) % 2]
        elif self.surface is None:
            if self.chute:
                name, bubble = 'chute', None
            elif mode == 'done' and now - self.done_ts < CHEER:
                name = 'cheer_a'
            elif self.vy > 14 * self.S:                    # 從高處掉下來才驚嚇，原地小跳不算
                name, eyes = 'fall', 'big'
            else:
                name = 'jump'
        if now < self.react_until:
            eyes, bubble = 'happy', self.react_text
            if self.react_pose and standing and not self.walking:
                name = self.react_pose
        if eyes == 'normal' and now < self.blink_until:
            eyes = 'blink'
        if self.hiding:                                    # 躲在全螢幕程式的角落：只探出頭，不講話
            name, bubble = 'peek', None
        return name, eyes, look, sq, bubble

    def typing_frame(self, now, t):
        """敲筆電：拿出筆電舉高、放到地上打開、轉成側面跳一下，接著兩手輪流敲。"""
        phase = now - self.type_start
        if phase < 0.4:
            return 'type_take'
        if phase < 0.8:
            return 'type_place'
        if phase < 1.0:
            return 'type_hop'
        beat = (0b1011010011010110 >> (t // 3 % 16)) & 1   # 節奏刻意不規律，像真的在打字
        return 'type_%d' % (1 + (t // 3) % 4 if beat else 2)

    def render(self, mode, now):
        t = self.t
        name, eyes, look, sq, bubble = self.pose(mode, now)
        if self.walking != self.was_walking:
            self.was_walking, self.turn_until = self.walking, now + 0.12

        typing = (mode == 'working' and not self.walking and self.surface in ('floor', 'perch') and not self.dragging
                  and not self.hiding
                  and not self.card and now >= self.react_until and now >= self.dizzy_until)
        if typing and self.type_start is None:
            self.type_start = now
        elif not typing and self.type_start is not None:
            if now - self.type_start > 1.2 and not self.dragging and self.surface is not None:   # 打了一陣子：闔上筆電再走
                self.close_until = now + 0.45
            self.type_start = None
        if typing:
            name, eyes, look = self.typing_frame(now, t), ('blink' if eyes == 'blink' else 'normal'), (0, 1)
        elif now < self.close_until and self.surface is not None and not self.hiding:
            name = 'type_close'
        elif self.riding and not self.hiding:
            name, look = 'bike_%d' % (1 + (t // 3) % 4), (0, 0)

        cv = self.cv
        cv.delete('all')
        cx, by = self.W / 2, self.H - 1
        full_c = self.c
        if self.hiding:
            self.c = max(2, round(full_c / 2))
        try:
            draw_by = by - self.hang_drop() if self.surface == 'hang' else by
            if name == 'chute':
                self.draw_chute(cx, draw_by, t, self.hand_points(cx, draw_by, name))
            top = self.sprite(cx, draw_by, name, eyes, look, sq)
            if eyes == 'dizzy':
                self.draw_stars(cx, by, t)
            self.draw_confetti(cx, by)
            if bubble:
                self.draw_bubble(cx, top - 2 * self.S, bubble)
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

    def sprite(self, cx, by, name, eyes='normal', look=(0, 0), sq=0.0):
        """畫一張像素圖（clawd_art.py），錨點對在 (cx, by)；回傳圖的上緣 y，泡泡放在它上面。

        畫好的圖依（圖名、眼睛、方向、壓扁量、大小）快取成 PhotoImage，之後每格只要貼上去。
        """
        if name not in art.ART:                            # 美術還沒畫的動作先用基本姿勢頂著
            name = 'side' if name.split('_')[0] in SIDE_POSES else 'front'
        p = max(1, round(self.c / 2))
        sq = round(sq / 0.03) * 0.03
        key = (name, tuple(eyes) if isinstance(eyes, list) else eyes, look, self.facing, sq, p)
        hit = self.sprite_cache.pop(key, None)
        if hit is None:
            grid, ax, ay = sprites.compose(name, eyes, look, self.facing)
            rs = sprites.rects(grid, ax, ay, p, 1 + sq, 1 - sq)
            x0, y0 = min(r[0] for r in rs), min(r[1] for r in rs)
            img = tk.PhotoImage(master=self.root, width=max(r[2] for r in rs) - x0, height=max(r[3] for r in rs) - y0)
            for r in rs:
                img.put(r[4], to=(r[0] - x0, r[1] - y0, r[2] - x0, r[3] - y0))
            hit = (img, x0, y0)
            if len(self.sprite_cache) >= 240:              # 最久沒用的丟掉
                self.sprite_cache.pop(next(iter(self.sprite_cache)))
        self.sprite_cache[key] = hit
        img, x0, y0 = hit
        self.cv.create_image(round(cx + x0), round(by + y0), image=img, anchor='nw')
        return by + y0

    def hand_points(self, cx, by, name):
        """像素圖標記的兩隻手（降落傘繩子綁的地方）在畫布上的位置。"""
        spr = art.ART.get(name, {})
        w, h = sprites.size(name) if name in art.ART else (0, 0)
        ax, ay = spr.get('anchor', (w / 2, h))
        p = max(1, round(self.c / 2))
        pts = [((hx + 0.5 - ax) * (-1 if self.facing > 0 else 1), hy - ay) for hx, hy in spr.get('hands', [])]
        return [(cx + dx * p, by + dy * p) for dx, dy in pts] or [(cx - 6.5 * self.c, by - 8 * self.c),
                                                                    (cx + 6.5 * self.c, by - 8 * self.c)]

    def draw_chute(self, cx, by, t, hands):
        """降落傘：奶油色與陶土色相間的傘面，四條繩子拉到兩隻高舉的手。"""
        c, S = self.c, self.S
        x0 = cx + 0.5 * c * math.sin(t / 9)                # 傘面比身體多晃一點
        hw, h = 7 * c, 3.8 * c
        base = min(y for _, y in hands) - 2.2 * c
        lw = max(1, round(S))
        left, right = sorted(hands)[0], sorted(hands)[-1]
        for ex, hand in ((-1, left), (-0.35, left), (0.35, right), (1, right)):
            self.cv.create_line(x0 + ex * hw, base, *hand, fill=STRING, width=lw)
        for i in range(6):
            self.cv.create_arc(x0 - hw, base - h, x0 + hw, base + h, start=i * 30, extent=30,
                               fill=CREAM if i % 2 else CLAY, outline=INK, width=lw)

    def draw_confetti(self, cx, by):
        """完成時從頭上噴出來的彩帶，飄一下就落地消失。"""
        keep = []
        size = max(2, round(self.c / 2))
        for f in self.confetti:
            f[0] += f[2]
            f[1] += f[3]
            f[2] *= 0.97
            f[3] += 0.6 * self.S
            if f[1] < 0:
                keep.append(f)
                w = size if (self.t + int(f[0])) % 6 < 3 else max(1, size // 2)   # 翻轉時變窄，看起來在飄
                self.cv.create_rectangle(round(cx + f[0]), round(by + f[1]), round(cx + f[0]) + w,
                                         round(by + f[1]) + size, fill=f[4], outline='')
        self.confetti = keep

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
        room = self.W - 4 - 2 * pad
        if self.font.measure(text) > room:                 # 太長就截斷，不讓字跑出泡泡
            while text and self.font.measure(text + '…') > room:
                text = text[:-1]
            text = text.rstrip() + '…'
        w = self.font.measure(text) + 2 * pad
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
            # 依實際經過的時間補上這段期間該走的格數：電腦忙、計時器不準或省電模式時，動作速度照樣一致
            steps = min(4, max(1, round((now - self.last_tick) * FPS))) if self.last_tick else 1
            self.last_tick = now
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
                for _ in range(steps):
                    self.think(mode, now)
                    self.physics(now)
            self.t += steps
            self.render(mode, now)
            self.last_cursor = self.cursor
            # 睡覺或縮在角落時沒什麼在動：每秒只畫 10 次省電
            slow = ((mode == 'sleep' or self.hiding) and self.surface is not None and not self.drag
                    and not self.card and not menu_open)
        finally:
            self.root.after(100 if slow else 1000 // FPS, self.tick)

    def run(self):
        self.root.after(0, self.tick)                      # 第一格也走 Tk 的回呼，出錯才會寫進 error.log
        self.root.mainloop()


def main():
    ctypes.windll.kernel32.CreateMutexW(None, False, ipc.MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS：已經有一隻在跑
        sys.exit(0)
    Pet(set_dpi_aware()).run()


if __name__ == '__main__':
    main()
