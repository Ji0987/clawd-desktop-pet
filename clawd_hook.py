"""Claude Code hook：把 Claude 目前在做什麼寫進 ~/.clawd/sessions/<session>.json，給桌寵（clawd_pet.py）讀。

每個 Claude Code 對話一個檔，欄位：state、tool（泡泡上的「在做什麼」）、ts、start（這一輪開始的時間）、project（資料夾名稱）；
對話結束（SessionEnd）時刪掉。由 Clawd.pyw hook 呼叫；要掛哪些事件見 clawd_setup.py。SessionStart 時順便把桌寵叫出來。

權限請求（PermissionRequest）與 Claude 的提問（PreToolUse 的 AskUserQuestion）會先交給桌寵：
Clawd 頭上出現卡片讓你直接回答。只在桌面版、桌寵在跑、而且你不在 Claude 視窗時才這樣做；
等待中只要切回 Claude 視窗，或桌寵關掉了，就立刻放手交給 Claude 原本的對話框。
除了上述回答之外，任何錯誤都吞掉、不輸出任何東西，絕不影響 Claude 本身。
"""
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

import clawd_ipc as ipc
import clawd_setup

WAITING_TOOLS = {'AskUserQuestion', 'ExitPlanMode'}   # 這些工具代表 Claude 在等你回答
WAIT_LIMIT = 580                                      # 秒；settings.json 裡這條 hook 的 timeout 要比它長


def to_state(data):
    """hook 輸入 → (state, tool)；回傳 None 表示這個事件不更新狀態。"""
    event = data.get('hook_event_name', '')
    tool = data.get('tool_name', '')
    if event == 'UserPromptSubmit':
        return 'thinking', ''
    if event == 'PreToolUse':
        if tool in WAITING_TOOLS:
            return 'alert', tool
        return 'working', brief(tool, data.get('tool_input'))
    if event in ('PostToolUse', 'PostToolUseFailure'):
        return 'thinking', ''
    if event == 'PermissionRequest':
        return 'alert', brief(tool, data.get('tool_input'))
    if event == 'Notification':
        kind = data.get('notification_type', '')
        msg = data.get('message', '').lower()
        if kind in ('permission_prompt', 'elicitation_dialog'):
            return 'alert', ''
        if kind or 'waiting for your input' in msg:    # 閒置提醒、登入成功等不算需要你處理；舊版沒有類型才看訊息
            return None
        return 'alert', ''
    if event == 'Stop':
        return 'done', ''
    return None


def brief(tool, tool_input):
    """泡泡上的「在做什麼」，例如「跑 git status」「改 app.py」，最多 40 字；認不得的就只給工具名稱。"""
    inp = tool_input if isinstance(tool_input, dict) else {}
    verb, obj = '', ''
    if tool == 'Bash':
        line = str(inp.get('command') or '').split('\n')[0].split('&&')[-1]
        try:
            words = shlex.split(line, posix=False)       # 引號裡有空白的路徑（Program Files）算一個詞
        except ValueError:
            words = line.split()
        while words and '=' in words[0] and not words[0].startswith(('"', "'")):   # 跳過 FOO=bar 這種環境變數
            words = words[1:]
        if words:                                        # 執行檔只留名稱：「"C:/…/python.exe" x.py」→「python x.py」
            exe = os.path.basename(words[0].strip('"\'').replace('\\', '/'))
            words[0] = exe[:-4] if exe.lower().endswith('.exe') else exe
        verb, obj = '跑', ' '.join(w.strip('"\'') for w in words[:2])
    elif tool in ('Edit', 'MultiEdit', 'NotebookEdit', 'Write', 'Read'):
        verb = {'Write': '寫', 'Read': '看'}.get(tool, '改')
        obj = os.path.basename(str(inp.get('file_path') or inp.get('notebook_path') or ''))
    elif tool in ('Grep', 'Glob'):
        verb, obj = {'Grep': '搜尋', 'Glob': '找'}[tool], str(inp.get('pattern') or '')
    elif tool == 'WebFetch':
        try:
            verb, obj = '讀', urllib.parse.urlparse(str(inp.get('url') or '')).hostname or ''
        except ValueError:                               # 網址格式怪：只顯示工具名稱
            pass
    elif tool == 'WebSearch':
        verb, obj = '查', str(inp.get('query') or '')
    elif tool == 'Skill':
        verb, obj = '用技能', str(inp.get('skill') or '')
    elif tool in ('Task', 'Agent'):
        return '派小幫手'
    elif tool == 'TodoWrite':
        return '整理待辦'
    elif tool.startswith('mcp__'):
        return tool.rsplit('__', 1)[-1][:40]
    return (verb + ' ' + obj)[:40] if obj else tool


def project_name(cwd):
    """工作目錄的資料夾名稱，狀態檔與卡片上顯示用。"""
    return os.path.basename(os.path.normpath(cwd)) if cwd else ''


def write_state(state, tool, data):
    """寫進這個 session 自己的狀態檔。start 只在送出提示時重設；沒有 cwd 的事件沿用舊的 project。"""
    path = ipc.session_path(data.get('session_id', ''))
    old = ipc.read_json(path)
    if not isinstance(old, dict):
        old = {}
    now = time.time()
    start = now if data.get('hook_event_name') == 'UserPromptSubmit' else old.get('start', now)
    ipc.write_json(path, {'state': state, 'tool': tool, 'ts': now, 'start': start,
                          'project': project_name(data.get('cwd')) or old.get('project', '')})


def launch_pet():
    if ipc.pet_running():
        return
    subprocess.Popen(list(clawd_setup.launcher()), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def ask_pet(request):
    """把請求交給桌寵並等使用者回應；回傳回應 dict，不適用或改由 Claude 處理時回傳 None。"""
    if os.environ.get('CLAUDE_CODE_ENTRYPOINT') != 'claude-desktop' \
            or not ipc.pet_running() or ipc.claude_is_foreground():
        return None
    rid = uuid.uuid4().hex
    req_path, resp_path = ipc.REQ_DIR / (rid + '.json'), ipc.RESP_DIR / (rid + '.json')
    if not ipc.write_json(req_path, dict(request, id=rid, pid=os.getpid(), ts=time.time())):
        return None
    try:
        deadline, next_pet_check = time.time() + WAIT_LIMIT, time.time() + 1
        while time.time() < deadline:
            resp = ipc.read_json(resp_path)
            if resp is not None:
                return None if resp.get('decision') == 'release' else resp
            if ipc.claude_is_foreground():              # 你回到 Claude 視窗了：交給原本的對話框
                return None
            if time.time() >= next_pet_check:           # 每秒看一次桌寵還在不在；關掉了就沒人會回答，放手
                if not ipc.pet_running():
                    return None
                next_pet_check = time.time() + 1
            time.sleep(0.15)
        return None
    finally:
        for path in (req_path, resp_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def summarize(tool, tool_input):
    """卡片上顯示的操作內容：Bash 附上說明、改檔就給完整路徑，其餘工具就列出參數。"""
    if not isinstance(tool_input, dict):
        return ''
    if tool == 'Bash' and tool_input.get('description'):
        return '# %s\n%s' % (tool_input['description'], tool_input.get('command') or '')
    path = tool_input.get('file_path') or tool_input.get('notebook_path')
    if tool in ('Edit', 'MultiEdit', 'Write', 'NotebookEdit') and isinstance(path, str) and path:
        return path
    for key in ('command', 'file_path', 'notebook_path', 'url', 'pattern', 'query'):
        if isinstance(tool_input.get(key), str):
            return tool_input[key]
    return json.dumps(tool_input, ensure_ascii=False, indent=1)


def diff_lines(tool, tool_input):
    """Edit／MultiEdit／Write 的修改預覽：舊的行前面加 '-'、新的行前面加 '+'，最多 40 行、每行 300 字。"""
    inp = tool_input if isinstance(tool_input, dict) else {}
    if tool == 'Write':
        pairs = [('', inp.get('content'))]
    elif tool == 'Edit':
        pairs = [(inp.get('old_string'), inp.get('new_string'))]
    else:                                                # MultiEdit：只取前 3 組
        edits = inp.get('edits') if isinstance(inp.get('edits'), list) else []
        pairs = [(e.get('old_string'), e.get('new_string')) for e in edits[:3] if isinstance(e, dict)]
    lines = []
    for old, new in pairs:
        lines += ['-' + s for s in str(old or '').splitlines()]
        lines += ['+' + s for s in str(new or '').splitlines()]
    return [s[:300] for s in lines[:40]]


def suggest_label(suggestions):
    """Claude 建議的永久規則，給卡片上顯示，例如「Bash(npm test)、Read；切換成 acceptEdits」，最多 80 字。"""
    groups = []
    for s in suggestions:
        kind = s.get('type') if isinstance(s, dict) else ''
        if kind == 'addRules':
            rules = [r for r in s.get('rules') or [] if isinstance(r, dict) and r.get('toolName')]
            groups.append('、'.join(r['toolName'] + ('(%s)' % r['ruleContent'] if r.get('ruleContent') else '')
                                 for r in rules))
        elif kind == 'setMode':
            groups.append('切換成 ' + str(s.get('mode', '')))
        elif kind == 'addDirectories':
            groups.append('允許資料夾 ' + '、'.join(os.path.basename(str(d)) for d in s.get('directories') or []))
    return '；'.join(g for g in groups if g)[:80]


def handle_permission(data):
    tool = data.get('tool_name', '')
    if tool in WAITING_TOOLS:                           # 提問在 PreToolUse 處理；計畫審核留給 Claude
        return None
    sid = data.get('session_id', '')
    tool_input = data.get('tool_input')
    request = {'kind': 'permission', 'tool': tool, 'session': sid, 'project': project_name(data.get('cwd')),
               'summary': summarize(tool, tool_input)[:2000]}
    if tool in ('Edit', 'MultiEdit', 'Write'):
        request['diff'] = diff_lines(tool, tool_input)
    suggestions = data.get('permission_suggestions')
    if isinstance(suggestions, list) and suggestions:
        request['suggestions'] = suggestions
        request['suggest_label'] = suggest_label(suggestions)
    resp = ask_pet(request)
    if not resp:
        return None
    if ipc.session_path(data.get('session_id', '')).exists():   # 等待中對話已經結束了就別寫回去
        write_state('thinking', '', data)
    if resp.get('decision') == 'allow':
        decision = {'behavior': 'allow'}
    elif resp.get('decision') == 'always':              # 永久允許：連 Claude 建議的規則一起寫回設定
        decision = {'behavior': 'allow'}
        if request.get('suggestions'):
            decision['updatedPermissions'] = request['suggestions']
    else:
        decision = {'behavior': 'deny', 'message': '使用者在桌寵 Clawd 上拒絕了這個操作。'}
    return {'hookSpecificOutput': {'hookEventName': 'PermissionRequest', 'decision': decision}}


def handle_question(data):
    tool_input = data.get('tool_input') or {}
    questions = tool_input.get('questions') or []
    if not questions:
        return None
    sid = data.get('session_id', '')
    resp = ask_pet({'kind': 'question', 'session': sid, 'project': project_name(data.get('cwd')),
                    'questions': questions})
    if not resp or not isinstance(resp.get('answers'), dict):
        return None
    if ipc.session_path(data.get('session_id', '')).exists():   # 等待中對話已經結束了就別寫回去
        write_state('thinking', '', data)
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                   'updatedInput': dict(tool_input, answers=resp['answers'])}}


def main():
    try:
        data = json.loads(read_stdin().decode('utf-8-sig') or '{}')
    except Exception:
        data = {}
    out = None
    try:
        event = data.get('hook_event_name')
        if event == 'SessionStart':
            launch_pet()
            return
        path = ipc.session_path(data.get('session_id', ''))
        # 對話結束、或你按了中斷（中斷不會觸發 Stop）：狀態檔刪掉，桌寵就不再顯示它
        if event == 'SessionEnd' or (event == 'PostToolUseFailure' and data.get('is_interrupt')):
            path.unlink(missing_ok=True)
            return
        if event == 'Notification' and data.get('notification_type') == 'idle_prompt':
            old = ipc.read_json(path)                    # Claude 在等你輸入，狀態卻還停在工作中：多半是想事情時被中斷了
            if isinstance(old, dict) and old.get('state') in ('thinking', 'working'):
                path.unlink(missing_ok=True)
            return
        result = to_state(data)
        if result:
            write_state(*result, data)
        if event == 'PermissionRequest':
            out = handle_permission(data)
        elif event == 'PreToolUse' and data.get('tool_name') == 'AskUserQuestion':
            out = handle_question(data)
    except Exception:
        out = None
    if out:
        emit(json.dumps(out))                            # ensure_ascii：不受主控台編碼影響


def read_stdin():
    """讀完 stdin。pythonw 在某些啟動方式下 sys.stdin 是 None，改直接讀 Windows 的標準輸入 handle。"""
    if sys.stdin is not None:
        return sys.stdin.buffer.read()
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = ctypes.c_void_p
    handle = ctypes.c_void_p(k32.GetStdHandle(-10))     # STD_INPUT_HANDLE
    chunks, buf, got = [], ctypes.create_string_buffer(65536), ctypes.c_ulong()
    while k32.ReadFile(handle, buf, len(buf), ctypes.byref(got), None) and got.value:
        chunks.append(buf.raw[:got.value])
    return b''.join(chunks)


def emit(text):
    """寫到 stdout。pythonw 在某些啟動方式下 sys.stdout 是 None，改直接寫 Windows 的標準輸出 handle。"""
    if sys.stdout is not None:
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = ctypes.c_void_p
    handle = k32.GetStdHandle(-11)                       # STD_OUTPUT_HANDLE
    data = text.encode('utf-8')
    written = ctypes.c_ulong()
    k32.WriteFile(ctypes.c_void_p(handle), data, len(data), ctypes.byref(written), None)


if __name__ == '__main__':
    main()
