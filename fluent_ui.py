"""仿 Windows 11 Fluent 風格的右鍵選單與互動卡片，用 tkinter Canvas 自繪。

顏色跟著系統的深淺色主題與強調色；視窗圓角與邊框交給 DWM。
兩者都不搶焦點（WS_EX_NOACTIVATE），點擊照樣有效，你正在打字的視窗不受影響。
"""
import ctypes
import ctypes.wintypes as wt
import tkinter as tk
import tkinter.font as tkfont
import winreg

user32 = ctypes.windll.user32
user32.MonitorFromPoint.restype = wt.HMONITOR
user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
user32.GetParent.restype = wt.HWND
dwmapi = ctypes.windll.dwmapi
PERSONALIZE = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
ACCENT = r'Software\Microsoft\Windows\CurrentVersion\Explorer\Accent'


def _reg(path, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def theme():
    dark = _reg(PERSONALIZE, 'AppsUseLightTheme') != 1
    palette = _reg(ACCENT, 'AccentPalette')             # 8 色 RGBA：淺3 淺2 淺1 基準 深1 深2 深3 …
    accent = None
    if isinstance(palette, bytes) and len(palette) >= 20:
        i = 4 if dark else 16                           # 深色主題用「淺2」、淺色主題用「深1」，同系統按鈕
        accent = '#%02x%02x%02x' % tuple(palette[i:i + 3])
    if dark:
        return dict(bg='#2C2C2C', border='#454545', hover='#383838', text='#FFFFFF', sub='#C8C8C8',
                    sep='#3D3D3D', control='#373737', control_hover='#3F3F3F', control_border='#434343',
                    code='#1F1F1F', accent=accent or '#4CC2FF', on_accent='#000000')
    return dict(bg='#F9F9F9', border='#DADADA', hover='#EAEAEA', text='#1B1B1B', sub='#5D5D5D',
                sep='#E5E5E5', control='#FFFFFF', control_hover='#F6F6F6', control_border='#E0E0E0',
                code='#EFEFEF', accent=accent or '#005FB8', on_accent='#FFFFFF')


def colorref(hex_color):
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return r | g << 8 | b << 16


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x0 + r, y0, x1 - r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y0 + r, x1, y1 - r,
           x1, y1 - r, x1, y1, x1 - r, y1, x1 - r, y1, x0 + r, y1, x0 + r, y1, x0, y1, x0, y1 - r,
           x0, y1 - r, x0, y0 + r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT), ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]


def work_area(x, y):
    """(x, y) 所在螢幕（或最近的螢幕）的工作區，多螢幕時選單和卡片才不會被推回主螢幕。"""
    hmon = user32.MonitorFromPoint(wt.POINT(int(x), int(y)), 2)   # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom


class Popup:
    """無邊框、置頂、圓角、不搶焦點的小視窗，內容畫在一張 Canvas 上；按鈕自帶 hover 效果。"""

    def __init__(self, root, scale):
        self.S = scale
        self.t = theme()
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.cv = tk.Canvas(self.win, bg=self.t['bg'], highlightthickness=0, bd=0)
        self.cv.pack()
        self.buttons = []                               # (x0, y0, x1, y1, 底色 item, 一般色, hover 色, 動作)
        self.hovered = None
        self.closed = False
        self.cv.bind('<Motion>', self._on_motion)
        self.cv.bind('<Leave>', lambda e: self._set_hover(None))
        self.cv.bind('<ButtonRelease-1>', self._on_click)
        families = set(tkfont.families(root))
        ui = 'Microsoft JhengHei UI'
        mono = next((f for f in ('Cascadia Mono', 'Consolas') if f in families), 'Courier New')
        icons = next((f for f in ('Segoe Fluent Icons', 'Segoe MDL2 Assets') if f in families), ui)
        px = lambda n: -round(n * scale)
        self.f_body = tkfont.Font(root, family=ui, size=px(14))
        self.f_bold = tkfont.Font(root, family=ui, size=px(14), weight='bold')
        self.f_small = tkfont.Font(root, family=ui, size=px(12))
        self.f_mono = tkfont.Font(root, family=mono, size=px(12))
        self.f_icon = tkfont.Font(root, family=icons, size=px(16))
        self.f_icon_small = tkfont.Font(root, family=icons, size=px(12))

    def add_button(self, x0, y0, x1, y1, action, fill, hover, outline='', radius=4):
        item = round_rect(self.cv, x0, y0, x1, y1, radius * self.S, fill=fill, outline=outline)
        self.buttons.append((x0, y0, x1, y1, item, fill, hover, action))
        return item

    def _hit(self, x, y):
        for b in self.buttons:
            if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                return b
        return None

    def _set_hover(self, b):
        if b is self.hovered:
            return
        if self.hovered:
            self.cv.itemconfigure(self.hovered[4], fill=self.hovered[5])
        if b:
            self.cv.itemconfigure(b[4], fill=b[6])
        self.hovered = b
        self.cv.configure(cursor='hand2' if b else '')

    def _on_motion(self, e):
        self._set_hover(self._hit(e.x, e.y))

    def _on_click(self, e):
        b = self._hit(e.x, e.y)
        if b:
            b[7]()

    def show(self, w, h, x, y):
        """以 (x, y) 為左上角顯示，超出工作區就往內推。"""
        self.w, self.h = round(w), round(h)
        self.cv.configure(width=self.w, height=self.h)
        self.move(x, y)
        self.win.deiconify()
        self.win.update_idletasks()
        self.hwnd = hwnd = user32.GetParent(self.win.winfo_id())
        ex = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x00000080)   # WS_EX_NOACTIVATE | TOOLWINDOW
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)   # 圓角
        dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(ctypes.c_uint(colorref(self.t['border']))), 4)

    def move(self, x, y):
        wa = work_area(x + self.w / 2, y + self.h / 2)
        m = round(8 * self.S)
        x = min(max(round(x), wa[0] + m), wa[2] - self.w - m)
        y = min(max(round(y), wa[1] + m), wa[3] - self.h - m)
        geo = '%dx%d+%d+%d' % (self.w, self.h, x, y)
        if geo != getattr(self, '_geo', None):
            self.win.geometry(geo)
            self._geo = geo

    def close(self):
        if not self.closed:
            self.closed = True
            self.win.destroy()


class FluentMenu(Popup):
    """右鍵選單。items：(圖示字元, 文字, 動作) 或 None（分隔線）。點選單外任何地方就關閉。"""

    def __init__(self, root, scale, items, x, y):
        super().__init__(root, scale)
        S, t = scale, self.t
        pad, row, sep_h = 4 * S, 36 * S, 9 * S
        text_w = max(self.f_body.measure(i[1]) for i in items if i)
        w = max(200 * S, 48 * S + text_w + 28 * S)
        cy = pad
        for item in items:
            if item is None:
                self.cv.create_line(0, cy + sep_h / 2, w, cy + sep_h / 2, fill=t['sep'])
                cy += sep_h
                continue
            glyph, label, action = item
            self.add_button(pad, cy + 2 * S, w - pad, cy + row - 2 * S, self._wrap(action), t['bg'], t['hover'])
            self.cv.create_text(pad + 18 * S, cy + row / 2, text=glyph, font=self.f_icon, fill=t['text'])
            self.cv.create_text(pad + 44 * S, cy + row / 2, text=label, font=self.f_body, fill=t['text'], anchor='w')
            cy += row
        h = cy + pad
        wa = work_area(x, y)
        if y + h > wa[3]:                               # 像系統選單一樣，下方放不下就往上開
            y -= h
        self.show(w, h, x, y)
        self._armed = False
        self._watch()

    def _wrap(self, action):
        def run():
            self.close()
            action()
        return run

    def _watch(self):
        """放開開啟選單的那一下之後，任何一顆滑鼠鍵在選單外按下就關閉。"""
        if self.closed:
            return
        down = any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (1, 2, 4))
        if not self._armed:
            self._armed = not down
        elif down:
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            x0, y0 = self.win.winfo_rootx(), self.win.winfo_rooty()
            if not (x0 <= pt.x < x0 + self.w and y0 <= pt.y < y0 + self.h):
                self.close()
                return
        self.win.after(30, self._watch)


class RequestCard(Popup):
    """Clawd 頭上的互動卡片：回覆權限請求，或回答 Claude 的提問。

    on_answer(dict) 收到 {'decision': 'allow'|'deny'|'release'} 或 {'answers': {問題: 答案}}。
    """

    def __init__(self, root, scale, request, on_answer, anchor):
        super().__init__(root, scale)
        self.req, self.on_answer = request, on_answer
        self.answers = {}
        self.qi = 0
        self.picked = set()
        self.anchor = anchor                            # (中心 x, 底邊 y)
        self.render()

    def place_above(self, cx, bottom):
        self.anchor = (cx, bottom)
        if hasattr(self, 'w'):
            self.move(cx - self.w / 2, bottom - self.h)

    # ───────── 版面 ─────────
    def render(self):
        S, t, cv = self.S, self.t, self.cv
        cv.delete('all')
        self.buttons, self.hovered = [], None
        self.W = W = round(360 * S)
        pad = 16 * S
        inner = W - 2 * pad
        y = pad
        if self.req.get('kind') == 'question':
            y = self._render_question(pad, y, inner)
        elif self.req.get('kind') == 'confirm':
            y = self._render_confirm(pad, y, inner)
        else:
            y = self._render_permission(pad, y, inner)
        h = y + pad
        if not hasattr(self, 'w'):
            cx, bottom = self.anchor or (0, 0)
            self.show(W, h, cx - W / 2, bottom - h)
        else:
            self.w, self.h = W, round(h)
            cv.configure(width=W, height=self.h)
            if self.anchor:
                self.place_above(*self.anchor)

    def _title(self, x, y, text, right=''):
        S, t = self.S, self.t
        round_rect(self.cv, x, y + 5 * S, x + 10 * S, y + 15 * S, 2 * S, fill='#D97757', outline='')
        self.cv.create_text(x + 18 * S, y + 10 * S, text=text, font=self.f_bold, fill=t['text'], anchor='w')
        if right:
            self.cv.create_text(x + self.W - 32 * S, y + 10 * S, text=right, font=self.f_small, fill=t['sub'], anchor='e')
        return y + 26 * S

    def _text(self, x, y, text, font, color, width):
        item = self.cv.create_text(x, y, text=text, font=font, fill=color, anchor='nw', width=width)
        return self.cv.bbox(item)[3]

    def _link(self, x, y, text, action):
        S, t = self.S, self.t
        tw = self.f_small.measure(text + '  ')
        self.add_button(x - 6 * S, y - 3 * S, x + tw + 14 * S, y + 21 * S, action, t['bg'], t['hover'])
        self.cv.create_text(x, y + 9 * S, text=text, font=self.f_small, fill=t['sub'], anchor='w')
        self.cv.create_text(x + tw, y + 9 * S, text='', font=self.f_icon_small, fill=t['sub'], anchor='w')
        return y + 22 * S

    def _render_permission(self, x, y, w):
        S, t, cv = self.S, self.t, self.cv
        y = self._title(x, y, 'Claude 想使用 %s' % self.req.get('tool', '工具'))
        y = self._text(x, y + 2 * S, '要允許這個操作嗎？', self.f_small, t['sub'], w) + 10 * S
        summary = self.req.get('summary', '').strip()
        if summary:
            lines = summary.splitlines()
            shown = '\n'.join(lines[:8])[:600]
            if len(lines) > 8 or len(summary) > len(shown):
                shown += '\n…'
            box = round_rect(cv, x, y, x + w, y + 10, 4 * S, fill=t['code'], outline='')
            bottom = self._text(x + 10 * S, y + 8 * S, shown, self.f_mono, t['text'], w - 20 * S) + 8 * S
            cv.coords(box, *self._rr_points(x, y, x + w, bottom, 4 * S))
            y = bottom + 14 * S
        bw = (w - 8 * S) / 2
        self._button(x, y, x + bw, y + 32 * S, '允許', lambda: self.on_answer({'decision': 'allow'}), accent=True)
        self._button(x + bw + 8 * S, y, x + w, y + 32 * S, '拒絕', lambda: self.on_answer({'decision': 'deny'}))
        y += 32 * S + 12 * S
        return self._link(x, y, '在 Claude 中處理', lambda: self.on_answer({'decision': 'release'}))

    def _render_confirm(self, x, y, w):
        """一般的是／否詢問：req 帶 title、body、yes、no。"""
        S, t = self.S, self.t
        y = self._title(x, y, self.req.get('title', ''))
        y = self._text(x, y + 4 * S, self.req.get('body', ''), self.f_small, t['sub'], w) + 16 * S
        bw = (w - 8 * S) / 2
        self._button(x, y, x + bw, y + 32 * S, self.req.get('yes', '好'), lambda: self.on_answer({'decision': 'allow'}),
                     accent=True)
        self._button(x + bw + 8 * S, y, x + w, y + 32 * S, self.req.get('no', '取消'),
                     lambda: self.on_answer({'decision': 'deny'}))
        return y + 32 * S

    def _render_question(self, x, y, w):
        S, t, cv = self.S, self.t, self.cv
        qs = self.req.get('questions') or []
        q = qs[self.qi]
        multi = bool(q.get('multiSelect'))
        right = '%d / %d' % (self.qi + 1, len(qs)) if len(qs) > 1 else ''
        y = self._title(x, y, q.get('header') or 'Claude 想問你', right)
        y = self._text(x, y + 2 * S, q.get('question', ''), self.f_body, t['text'], w) + 12 * S
        for i, opt in enumerate(q.get('options') or []):
            label, desc = opt.get('label', ''), opt.get('description', '')
            on = i in self.picked
            tx = x + (36 * S if multi else 12 * S)
            # 先量高度再畫底，底色要在文字下面
            probe = cv.create_text(tx, 0, text=desc, font=self.f_small, anchor='nw', width=w - (tx - x) - 12 * S)
            desc_h = (cv.bbox(probe)[3] - cv.bbox(probe)[1]) if desc else 0
            cv.delete(probe)
            h = 14 * S + self.f_body.metrics('linespace') + (desc_h + 2 * S if desc else 0)
            self.add_button(x, y, x + w, y + h, self._pick(i, multi), t['control'], t['control_hover'],
                            outline=t['accent'] if on else t['control_border'])
            if multi:
                cv.create_text(x + 18 * S, y + h / 2, text='' if on else '', font=self.f_icon,
                               fill=t['accent'] if on else t['sub'])
            ly = y + 7 * S
            cv.create_text(tx, ly, text=label, font=self.f_body, fill=t['text'], anchor='nw')
            if desc:
                self._text(tx, ly + self.f_body.metrics('linespace') + 2 * S, desc, self.f_small, t['sub'],
                           w - (tx - x) - 12 * S)
            y += h + 6 * S
        if multi:
            y += 4 * S
            self._button(x, y, x + w, y + 32 * S, '送出', self._submit_multi, accent=True)
            y += 32 * S
        y += 12 * S
        return self._link(x, y, '其他答案：在 Claude 中回答', lambda: self.on_answer({'decision': 'release'}))

    def _rr_points(self, x0, y0, x1, y1, r):
        return [x0 + r, y0, x0 + r, y0, x1 - r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y0 + r, x1, y1 - r,
                x1, y1 - r, x1, y1, x1 - r, y1, x1 - r, y1, x0 + r, y1, x0 + r, y1, x0, y1, x0, y1 - r,
                x0, y1 - r, x0, y0 + r, x0, y0 + r, x0, y0]

    def _button(self, x0, y0, x1, y1, label, action, accent=False):
        t = self.t
        if accent:
            self.add_button(x0, y0, x1, y1, action, t['accent'], t['accent'])
            color = t['on_accent']
        else:
            self.add_button(x0, y0, x1, y1, action, t['control'], t['control_hover'], outline=t['control_border'])
            color = t['text']
        self.cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=label, font=self.f_body, fill=color)

    # ───────── 作答 ─────────
    def _pick(self, i, multi):
        def run():
            if multi:
                self.picked ^= {i}
                self.render()
            else:
                self._answer([i])
        return run

    def _submit_multi(self):
        if self.picked:
            self._answer(sorted(self.picked))

    def _answer(self, indexes):
        q = self.req['questions'][self.qi]
        labels = [q['options'][i].get('label', '') for i in indexes]
        self.answers[q.get('question', '')] = ', '.join(labels)
        self.qi += 1
        self.picked = set()
        if self.qi >= len(self.req['questions']):
            self.on_answer({'answers': self.answers})
        else:
            self.render()
