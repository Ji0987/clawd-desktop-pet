# Clawd 桌寵

![Clawd 的幾種狀態，右邊是在桌寵上批准權限的卡片](docs/showcase.png)

一隻會在 Windows 桌面上走來走去的像素 Clawd，會跟著 Claude Code 的工作狀態切換動作。Claude 要你批准權限或回答問題時，可以直接在牠頭上的卡片作答，不用切回 Claude 視窗。

### 👉 [下載最新版](https://github.com/Ji0987/clawd-desktop-pet/releases/latest)

## 安裝

1. 安裝 [Python](https://www.python.org/downloads/windows/) 3.10 以上（預設選項即可，要包含 tcl/tk）
2. 下載 `Clawd-v1.2.0.zip` 並解壓縮，會得到一個 `Clawd` 資料夾；把它放到固定的位置，例如 `文件\Clawd`
3. 雙擊資料夾裡的 `Clawd.pyw`
4. 第一次執行時，Clawd 頭上會問「要跟 Claude Code 連動嗎？」，按「連動」

連動之後，每次開新的 Claude Code 對話，Clawd 都會自動出現。想要開機就出現，在右鍵選單勾選「開機時自動啟動」。

> 從 Microsoft Store 安裝的 Python 不會自動開啟 `.pyw` 檔，請在該資料夾開終端機執行 `pythonw Clawd.pyw`。
>
> Clawd 只用 Python 標準函式庫，不需要另外安裝套件；以原始碼發佈，所以「智慧型應用程式控制」不會擋。

## 牠會做什麼

| Claude 的狀態 | Clawd 的反應 |
|---|---|
| 閒置 | 散步、跳上其他視窗的上緣、眼睛跟著游標轉，偶爾追游標；要走遠時騎腳踏車；兩分鐘沒事就睡覺（晚上 10 點到早上 6 點只要 40 秒） |
| 在思考 | 跑到 Claude 視窗旁邊，手扶著頭 |
| 在執行工具 | 拿出筆電放在地上，側身用兩隻手輪流敲鍵盤，頭上寫著正在做的事，例如「改 app.py」「跑 npm test」 |
| 需要你批准或回答 | 揮手，頭上出現卡片 |
| 回覆完成 | 撒彩帶、開心地跳起來再跳支舞（花超過五分鐘的工作會舉起獎盃），然後帶著「完成」泡泡等你最多 10 分鐘（花超過一分鐘的會寫出耗時）；回到 Claude 或點牠就收起來，點牠還會直接叫出 Claude |

同時開好幾個 Claude Code 對話時，Clawd 會顯示最需要注意的那個：等你回答 > 執行工具 > 思考 > 完成。

閒著的時候牠也不會呆站著：會走來走去、東張西望、伸懶腰、打哈欠，或是坐下來喝杯咖啡、看本書；游標靠近時會揮手打招呼，站在視窗上時偶爾會走到邊緣吊著晃一晃再跳下來。

- Claude 視窗沒有最大化時，Clawd 會跳到視窗上緣坐著
- 支援多螢幕，可以走到、甩到別的螢幕；工作列自動隱藏時站在螢幕底端
- 看影片、玩遊戲等**全螢幕**時，會縮小躲到那個螢幕的右下角，只探出頭來（Claude 自己全螢幕不算）
- 睡覺或躲在角落時降低畫面更新頻率，比較省電
- **點一下**會跳；**拖曳**可以把牠甩出去
- 甩到高處或從高的視窗上緣走下來，會打開**降落傘**慢慢飄下來
- 往下砸或甩去撞螢幕邊緣太用力，會**頭暈**一下
- **右鍵選單**：睡覺／起床、騎腳踏車兜風、開啟 Claude、與 Claude Code 連動、開機時自動啟動、結束

## 在 Clawd 上批准權限、回答問題

只在 **Claude 桌面版**、而且你**不在 Claude 視窗**時才會出現卡片：

- **權限請求**：顯示專案名稱、要執行的指令；改檔案時會用紅綠色列出要刪掉和加入的內容。按「允許」或「拒絕」；Claude 有提供「不再詢問」的規則時，還可以按「總是允許」
- **Claude 的提問**：直接點選答案，多題時可以回上一題修改；要自己打字回答就點「在 Claude 中回答」

卡片等待中只要切回 Claude 視窗，就會改用 Claude 原本的對話框，兩邊不會打架。等待中結束 Clawd 也一樣會交回 Claude。

## 連動做了什麼

連動會在 `%USERPROFILE%\.claude\settings.json` 的 `hooks` 加入以 `pythonw.exe Clawd.pyw hook` 執行的項目，你原本的設定和其他 hook 都不會被動到。

搬移 Clawd 資料夾或換了 Python 之後，重新開啟 `Clawd.pyw`，連動與開機啟動的路徑會自動更新。

## 隱私

Clawd 完全在你的電腦上運作，**不連網、不傳送任何資料**。hook 會讀到 Claude Code 傳來的事件內容，只把下面這些寫進 `%USERPROFILE%\.clawd\`：

| 檔案 | 內容 | 存放多久 |
|---|---|---|
| `sessions\*.json` | 每個對話目前的狀態、正在做的事的簡短描述（例如「改 app.py」）、專案資料夾名稱 | 對話結束就刪除 |
| `requests\`、`responses\` | 卡片上要顯示的指令、檔案路徑與修改內容，以及你的回答 | 回答後立刻刪除 |
| `config.json` | 是否問過要不要連動 | 一直保留 |
| `error.log` | 程式出錯時的紀錄 | 超過 256KB 會換新檔 |

## 移除

1. 在 Clawd 上按右鍵，取消勾選「與 Claude Code 連動」和「開機時自動啟動」，再按「結束 Clawd」
2. 刪除 Clawd 資料夾和 `%USERPROFILE%\.clawd\`

**請先做第 1 步再刪資料夾**，不然 Claude Code 每次都會試著執行已經不存在的 hook。Clawd 打不開時，在 Clawd 資料夾開終端機執行下面這行，一樣會解除連動和開機啟動（資料夾已經刪了的話，重新下載解壓縮後再執行）：

```
pythonw Clawd.pyw unlink
```

## 疑難排解

- **雙擊沒反應**：可能已經有一隻在跑了（同時只會有一隻），看看螢幕底部或其他螢幕。也可能是 Python 沒有關聯到 `.pyw`，改在資料夾裡開終端機執行 `pythonw Clawd.pyw`
- **出現錯誤訊息或牠突然不見**：詳細內容在 `%USERPROFILE%\.clawd\error.log`，回報問題時請附上
- **想看到即時的錯誤輸出**：在終端機用 `python Clawd.pyw`（不是 `pythonw`）啟動

## 開發

直接改資料夾裡的 `.py` 檔，結束 Clawd 再重新開啟即可。各檔案的分工：

| 檔案 | 內容 |
|---|---|
| `Clawd.pyw` | 進入點：不帶參數啟動桌寵，帶 `hook` 參數處理 Claude Code 的 hook，帶 `unlink` 參數解除連動 |
| `clawd_pet.py` | 桌寵本體：行為、物理、繪製 |
| `clawd_art.py` | 每個動作的像素畫（字元畫，一律面向左） |
| `clawd_sprites.py` | 把像素畫組成畫面：鏡射、眼睛、描邊、壓扁拉長 |
| `clawd_hook.py` | hook：寫入狀態，並把權限請求與提問轉給桌寵 |
| `fluent_ui.py` | Windows 11 風格的右鍵選單與卡片 |
| `clawd_setup.py` | 在 `settings.json` 加入／移除 hook，以及開機啟動 |
| `clawd_ipc.py` | 共用的檔案位置與視窗查詢 |
| `clawd_screen.py` | 多螢幕範圍、全螢幕偵測、可以站上去的視窗上緣 |

## 系統需求

- Windows 10／11
- Python 3.10 以上（含 tkinter）
- [Claude Code](https://claude.com/claude-code)（桌面版或終端機版都能連動；在 Clawd 上作答只支援桌面版）

---

本程式為個人非官方創作，與 Anthropic 無關。
