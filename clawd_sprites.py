"""Clawd 的像素圖引擎：把 clawd_art.py 的字元畫組成畫面（鏡射、眼睛、描邊、壓扁拉長）。

桌寵與美術預覽工具共用同一套，畫出來的東西完全一樣。
字元畫一律「面向左」畫，facing=1 時左右鏡射；一個字元是一個「小格」，邊長是桌寵格子的一半。
"""
from clawd_art import ART

PALETTE = {
    'C': '#D97757',   # 陶土色身體
    'H': '#E8936F',   # 亮面（受光的邊）
    'S': '#B5634A',   # 側面、遠側的手腳
    'D': '#8F4632',   # 深陰影（身體底部、縫隙）
    'K': '#1F1D1A',   # 眼睛、墨色
    'W': '#FFF8EC',   # 奶油白（眼睛反光、紙、杯子）
    'L': '#A3A3A3',   # 筆電外殼（淺灰）
    'G': '#5E5E5E',   # 深灰（鍵盤、筆電底、輪胎鋼圈）
    'E': '#8FD3F5',   # 螢幕的光
    'B': '#3E7CB1',   # 腳踏車車架
    'T': '#3A3A3A',   # 輪胎
    'Y': '#F2C14E',   # 黃色（星星、彩帶）
    'R': '#E05A47',   # 紅色
    'P': '#F4A38C',   # 腮紅粉
    'N': '#7A4E36',   # 咖啡、木頭
    'A': '#5BBF7A',   # 綠色（彩帶）
    'V': '#8E7AE0',   # 紫色（彩帶）
}
BODY = set('CHSD')                 # 身體的顏色，描邊用固定的深陶土色
OUTLINE = '#6E3323'

# 眼睛：(相對眼睛槽左上角的 dx, dy, 圖案)。眼睛槽是 2 寬 × 4 高的小格，美術圖裡那裡塗身體色即可
EYES = {
    'normal': (0, 0, ['KK', 'KK', 'KK', 'KK']),
    'blink': (0, 3, ['KK']),
    'sleep': (-1, 3, ['KKKK']),
    'happy': (-1, 1, ['.KK.', 'K..K']),
    'big': (0, -1, ['WK', 'KK', 'KK', 'KK', 'KK', 'KK']),
    'dizzy': (-1, 1, ['K.K', '.K.', 'K.K']),
    'squint': (-1, 2, ['KKK.', '..KK']),       # 用力、瞇眼
    'wide': (0, 0, ['KK', 'KK', 'KK', 'KK']),  # 保留名稱，給 3/4 側面用（同 normal）
}


def darken(color, k):
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return '#%02X%02X%02X' % (round(r * k), round(g * k), round(b * k))


def size(name):
    rows = ART[name]['rows']
    return max(len(r) for r in rows), len(rows)


def compose(name, eyes='normal', look=(0, 0), facing=-1):
    """回傳 (grid, ax, ay)：grid[y][x] 是調色盤字元或 None，(ax, ay) 是腳底中心（桌寵的 x, y 對到這一點）。

    eyes：一個樣式套用到所有眼睛槽，或依序給每個槽一個樣式（None 表示不畫）。
    look：眼睛往哪看，(dx, dy) 以小格為單位，已經是螢幕方向（不再鏡射）。
    """
    spr = ART[name]
    w, h = size(name)
    grid = [[None if ch == '.' else ch for ch in row.ljust(w, '.')] for row in spr['rows']]
    ax, ay = spr.get('anchor', (w / 2, h))
    slots = list(spr.get('eyes', []))
    if facing > 0:
        grid = [row[::-1] for row in grid]
        ax = w - ax
        slots = [(w - x - 2, y) for x, y in slots]
    styles = list(eyes) if isinstance(eyes, (list, tuple)) else [eyes] * len(slots)
    for (x, y), style in zip(slots, styles):
        if not style:
            continue
        dx, dy, pat = EYES[style]
        if facing > 0:
            pat = [row[::-1] for row in pat]
            dx = 2 - dx - len(pat[0])
        for j, row in enumerate(pat):
            for i, ch in enumerate(row):
                X, Y = x + dx + i + look[0], y + dy + j + look[1]
                if ch != '.' and 0 <= X < w and 0 <= Y < h and grid[Y][X] in BODY:   # 只畫在身體上，不會浮在外面
                    grid[Y][X] = ch
    return grid, ax, ay


def rects(grid, ax, ay, p, sx=1.0, sy=1.0, outline=True):
    """把 grid 換成要填色的矩形 [(x0, y0, x1, y1, 顏色)]，座標以像素計、相對腳底中心。

    p 是一個小格的像素數；sx、sy 是壓扁拉長（以腳底中心為基準）。outline 會沿著外輪廓描一圈細的深色邊。
    """
    h = len(grid)
    w = len(grid[0]) if h else 0
    X = lambda x: round((x - ax) * p * sx)
    Y = lambda y: round((y - ay) * p * sy)
    out = []
    for y, row in enumerate(grid):
        x = 0
        while x < w:
            ch = row[x]
            if ch is None:
                x += 1
                continue
            x1 = x
            while x1 < w and row[x1] == ch:
                x1 += 1
            out.append((X(x), Y(y), X(x1), Y(y + 1), PALETTE[ch]))
            x = x1
    if outline:
        t = max(1, round(p / 4))
        empty = lambda x, y: not (0 <= x < w and 0 <= y < h) or grid[y][x] is None
        for y in range(h):
            for x in range(w):
                ch = grid[y][x]
                if ch is None or ch in 'KW':
                    continue
                col = OUTLINE if ch in BODY else darken(PALETTE[ch], 0.6)
                x0, x1, y0, y1 = X(x), X(x + 1), Y(y), Y(y + 1)
                if empty(x - 1, y):
                    out.append((x0, y0, x0 + t, y1, col))
                if empty(x + 1, y):
                    out.append((x1 - t, y0, x1, y1, col))
                if empty(x, y - 1):
                    out.append((x0, y0, x1, y0 + t, col))
                if empty(x, y + 1):
                    out.append((x0, y1 - t, x1, y1, col))
    return [r for r in out if r[2] > r[0] and r[3] > r[1]]
