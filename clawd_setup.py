"""把 Clawd 的 hook 加進／移出 Claude Code 的使用者設定（~/.claude/settings.json），也管開機自動啟動（登錄檔）。

hook 的指令是「開著 Clawd 的那個 Python 的 pythonw.exe」加上參數 [Clawd.pyw 的路徑, "hook"]。
只動自己加的項目（參數的檔名是 Clawd.pyw 或舊版的 clawd_hook.py），其他設定與別人的 hook 原封不動。
"""
import json
import os
import sys
import winreg
from pathlib import Path

SETTINGS = Path.home() / '.claude' / 'settings.json'
ENTRY = Path(__file__).resolve().with_name('Clawd.pyw')
# 事件 → (matcher, timeout 秒)。等你在卡片上作答的兩個事件要給足時間（hook 自己最多等 580 秒）
EVENTS = {
    'SessionStart': (None, 10),
    'UserPromptSubmit': (None, 10),
    'PreToolUse': ('*', 600),
    'PostToolUse': ('*', 10),
    'PostToolUseFailure': ('*', 10),
    'PermissionRequest': ('*', 600),
    'Notification': (None, 10),
    'Stop': (None, 10),
    'SessionEnd': (None, 5),
}
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'   # 開機自動啟動的登錄位置
RUN_NAME = 'Clawd'


def launcher():
    """(pythonw.exe 路徑, Clawd.pyw 路徑)：用目前這個 Python，沒有 pythonw.exe 就退回 python.exe。"""
    exe = Path(sys.executable)
    pythonw = exe.with_name('pythonw.exe')
    return str(pythonw if os.path.lexists(pythonw) else exe), str(ENTRY)


def _is_ours(hook):
    args = hook.get('args') if isinstance(hook.get('args'), list) else []
    return any(os.path.basename(str(a)).lower() in ('clawd.pyw', 'clawd_hook.py') for a in args)


def _load():
    if not SETTINGS.exists():
        return {}
    text = SETTINGS.read_text(encoding='utf-8-sig')      # 可能帶 BOM；寫回時不加
    if not text.strip():
        return {}
    data = json.loads(text)                              # 格式壞掉就丟例外，不去覆寫使用者的檔案
    if not isinstance(data, dict):
        raise ValueError('settings.json 的最外層不是物件')
    return data


def _save(data):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_name(SETTINGS.name + '.clawd.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, SETTINGS)


def _strip(data):
    """拿掉所有 Clawd 的 hook，清掉因此變空的群組與事件；回傳拿掉了幾個。"""
    hooks = data.get('hooks')
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        groups = hooks[event] if isinstance(hooks[event], list) else []
        kept_groups = []
        for g in groups:
            inner = g.get('hooks', []) if isinstance(g, dict) else []
            kept = [h for h in inner if not (isinstance(h, dict) and _is_ours(h))]
            removed += len(inner) - len(kept)
            if kept or not inner:
                kept_groups.append(dict(g, hooks=kept) if inner else g)
        if kept_groups:
            hooks[event] = kept_groups
        elif isinstance(hooks[event], list):
            del hooks[event]
    if not hooks:
        del data['hooks']
    return removed


def status():
    """'on'：已連動到目前這份 Clawd；'moved'：連動的是別的位置或別的 Python，或缺了某些事件（舊版裝的）；'off'：沒連動。"""
    try:
        data = _load()
    except (OSError, ValueError):
        return 'off'
    found = [(event, h) for event, groups in (data.get('hooks') or {}).items() if isinstance(groups, list)
             for g in groups if isinstance(g, dict) for h in g.get('hooks', []) if isinstance(h, dict) and _is_ours(h)]
    if not found:
        return 'off'
    py, entry = launcher()
    same = lambda a, b: os.path.normcase(str(a)) == os.path.normcase(str(b))
    ok = all(same(h.get('command'), py) and len(h['args']) == 2 and same(h['args'][0], entry) and h['args'][1] == 'hook'
             for _, h in found)
    return 'on' if ok and {event for event, _ in found} == set(EVENTS) else 'moved'


def install():
    py, entry = launcher()
    data = _load()
    _strip(data)
    hooks = data.setdefault('hooks', {})
    for event, (matcher, timeout) in EVENTS.items():
        group = {'hooks': [{'type': 'command', 'command': py, 'args': [entry, 'hook'], 'timeout': timeout}]}
        if matcher:
            group = {'matcher': matcher, **group}
        hooks.setdefault(event, []).append(group)
    _save(data)


def uninstall():
    data = _load()
    if _strip(data):
        _save(data)


def _run_command():
    py, entry = launcher()
    return '"%s" "%s"' % (py, entry)


def autostart_status():
    """'on'：開機自動啟動已設成目前這份 Clawd；'moved'：值還在但內容不同（例如搬了資料夾）；'off'：沒設定。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_NAME)
    except OSError:
        return 'off'
    return 'on' if value == _run_command() else 'moved'


def set_autostart(on):
    """on 為 True 就寫入啟動指令，False 就刪掉（本來就沒有也不算錯）。"""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, _run_command())
            return
        try:
            winreg.DeleteValue(key, RUN_NAME)
        except FileNotFoundError:
            pass
