"""Claude Code hook：把 Claude 目前在做什麼寫進 ~/.clawd/state.json，給桌寵（clawd_pet.py）讀。

由 Clawd.pyw hook 呼叫；要掛哪些事件見 clawd_setup.py。SessionStart 時順便把桌寵叫出來。

權限請求（PermissionRequest）與 Claude 的提問（PreToolUse 的 AskUserQuestion）會先交給桌寵：
Clawd 頭上出現卡片讓你直接回答。只在桌面版、桌寵在跑、而且你不在 Claude 視窗時才這樣做；
等待中只要切回 Claude 視窗，就立刻放手交給 Claude 原本的對話框。
除了上述回答之外，任何錯誤都吞掉、不輸出任何東西，絕不影響 Claude 本身。
"""
import json
import os
import subprocess
import sys
import time
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
        return ('alert' if tool in WAITING_TOOLS else 'working'), tool
    if event in ('PostToolUse', 'PostToolUseFailure'):
        return 'thinking', ''
    if event == 'PermissionRequest':
        return 'alert', tool
    if event == 'Notification':
        kind = data.get('notification_type', '')
        msg = data.get('message', '').lower()
        if kind == 'idle_prompt' or 'waiting for your input' in msg:
            return None                                 # 回覆完閒置提醒，不算需要你處理
        return 'alert', ''
    if event == 'Stop':
        return 'done', ''
    if event == 'SessionEnd':
        return 'idle', ''
    return None


def write_state(state, tool, session):
    ipc.write_json(ipc.STATE_FILE, {'state': state, 'tool': tool, 'ts': time.time(), 'session': session})


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
    ipc.write_json(req_path, dict(request, id=rid, pid=os.getpid(), ts=time.time()))
    try:
        deadline = time.time() + WAIT_LIMIT
        while time.time() < deadline:
            resp = ipc.read_json(resp_path)
            if resp is not None:
                return None if resp.get('decision') == 'release' else resp
            if ipc.claude_is_foreground():              # 你回到 Claude 視窗了：交給原本的對話框
                return None
            time.sleep(0.15)
        return None
    finally:
        req_path.unlink(missing_ok=True)
        resp_path.unlink(missing_ok=True)


def summarize(tool, tool_input):
    """卡片上顯示的操作內容：指令、檔案路徑或網址，其餘工具就列出參數。"""
    if not isinstance(tool_input, dict):
        return ''
    for key in ('command', 'file_path', 'notebook_path', 'url', 'pattern', 'query'):
        if isinstance(tool_input.get(key), str):
            return tool_input[key]
    return json.dumps(tool_input, ensure_ascii=False, indent=1)


def handle_permission(data):
    tool = data.get('tool_name', '')
    if tool in WAITING_TOOLS:                           # 提問在 PreToolUse 處理；計畫審核留給 Claude
        return None
    sid = data.get('session_id', '')
    resp = ask_pet({'kind': 'permission', 'tool': tool, 'session': sid,
                    'summary': summarize(tool, data.get('tool_input'))[:2000]})
    if not resp:
        return None
    write_state('thinking', '', sid)
    if resp.get('decision') == 'allow':
        decision = {'behavior': 'allow'}
    else:
        decision = {'behavior': 'deny', 'message': '使用者在桌寵 Clawd 上拒絕了這個操作。'}
    return {'hookSpecificOutput': {'hookEventName': 'PermissionRequest', 'decision': decision}}


def handle_question(data):
    tool_input = data.get('tool_input') or {}
    questions = tool_input.get('questions') or []
    if not questions:
        return None
    sid = data.get('session_id', '')
    resp = ask_pet({'kind': 'question', 'session': sid, 'questions': questions})
    if not resp or not isinstance(resp.get('answers'), dict):
        return None
    write_state('thinking', '', sid)
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
        result = to_state(data)
        if result:
            write_state(*result, data.get('session_id', ''))
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
