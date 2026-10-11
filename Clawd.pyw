"""Clawd 桌寵的進入點（用官方的 Python 3 執行，需要內建的 tkinter）。

  pythonw Clawd.pyw          啟動桌寵（已經有一隻在跑就直接結束）；裝了 python.org 版 Python 時雙擊即可
  pythonw Clawd.pyw hook     給 Claude Code 的 hook 呼叫：從 stdin 讀事件，必要時把回覆寫到 stdout
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 從別的目錄啟動也找得到旁邊的模組

__version__ = '1.1.0'


def main():
    if sys.argv[1:2] == ['hook']:
        import clawd_hook                    # hook 每次工具呼叫都會跑，不載入 tkinter 以求快
        clawd_hook.main()
    else:
        import clawd_pet
        clawd_pet.main()


if __name__ == '__main__':
    main()
