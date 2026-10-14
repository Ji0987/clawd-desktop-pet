"""Clawd 桌寵的進入點（用官方的 Python 3 執行，需要內建的 tkinter）。

  pythonw Clawd.pyw          啟動桌寵（已經有一隻在跑就直接結束）；裝了 python.org 版 Python 時雙擊即可
  pythonw Clawd.pyw hook     給 Claude Code 的 hook 呼叫：從 stdin 讀事件，必要時把回覆寫到 stdout
  pythonw Clawd.pyw unlink   解除與 Claude Code 的連動、取消開機啟動，之後就可以刪除這個資料夾
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 從別的目錄啟動也找得到旁邊的模組

__version__ = '1.2.0'


def message(text, icon=0x40):
    """Windows 訊息框（0x40 資訊、0x10 錯誤）：pythonw 沒有主控台，使用者只能靠它看到結果。"""
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, text, 'Clawd', icon)


def log_error():
    """例外寫進 ~/.clawd/error.log，格式同 clawd_pet.log_exception；寫不進去就算了。"""
    import time
    import traceback
    try:
        import clawd_ipc as ipc
        ipc.BASE.mkdir(parents=True, exist_ok=True)
        with open(ipc.BASE / 'error.log', 'a', encoding='utf-8') as f:
            f.write(time.strftime('[%Y-%m-%d %H:%M:%S]\n') + traceback.format_exc() + '\n')
    except Exception:
        pass


def unlink():
    """解除連動並取消開機啟動，結果用訊息框告知。"""
    import clawd_setup
    errors = []
    for name, step in (('解除連動', clawd_setup.uninstall), ('取消開機啟動', lambda: clawd_setup.set_autostart(False))):
        try:                                 # 兩件事各做各的：settings.json 壞掉也要能取消開機啟動
            step()
        except Exception as e:
            errors.append('%s失敗：%s' % (name, e))
    if errors:
        message('\n'.join(errors), 0x10)
    else:
        message('已解除 Clawd 與 Claude Code 的連動，也取消了開機啟動。現在可以刪除這個資料夾。')


def main():
    if sys.argv[1:2] == ['hook']:
        import clawd_hook                    # hook 每次工具呼叫都會跑，不載入 tkinter 以求快
        clawd_hook.main()
    elif sys.argv[1:2] == ['unlink']:
        unlink()
    else:
        try:
            import tkinter                   # 先檢查 tkinter，缺的時候給出明確的提示
        except ImportError:
            message('這個 Python 沒有 tkinter，請安裝 python.org 的 Python 3.10 以上（預設選項含 tcl/tk）。', 0x10)
            return
        try:
            import clawd_pet
            clawd_pet.main()
        except Exception:                    # SystemExit 不在這裡攔，照常放行
            log_error()
            message('Clawd 啟動失敗，詳細內容在 %USERPROFILE%\\.clawd\\error.log', 0x10)


if __name__ == '__main__':
    main()
