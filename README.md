# Codex Usage Tray (Win11)

Codex CLIの使用量をWindows 11のタスクトレイに常駐表示するPythonアプリです。
外周は5時間枠、内周は週次枠。中央は5時間枠の使用率です。

![30%](sample_30.png) ![70%](sample_70.png) ![95%](sample_95.png) ![不明](sample_unknown.png)

- 60%以下は緑、85%以下は黄、それより上は赤。しきい値・色は設定できます。
- マウスを乗せると、各枠の使用率・次のリセット時刻・元データの経過時間を表示します。
- 右クリックで「今すぐ更新」「Windows起動時に自動起動」「ログを開く」「終了」。
- 取得失敗時は最後の値を保持し、失敗理由と「何分前の値」かを表示します。再起動後も復元します。
- リセット時刻を過ぎた枠と、リセット時刻が分からない枠は灰色の点線リングと「不明」。0%とは区別します。

## データ源

**ローカルのCodexセッションログのみ**を読みます。
`%USERPROFILE%\.codex\sessions\**\rollout-*.jsonl` 内の
`event_msg → token_count → rate_limits` が対象です。
`window_minutes = 300 / 10080` によって5時間・週次を識別し、
`used_percent` とUnix秒の `resets_at` を取得します。
primary / secondaryの位置だけで枠を判断しません。
`limit_id` が `codex` またはnull/欠損の従来形式に対応します。

同じJSONLを読み直しても、観測時刻は元イベントのままです。**他端末の使用量や、CLIを利用していない間の変化は取得できません。**
新しい使用量イベントが記録されるまでは、最後の観測値として表示します。
5分以上経過した場合は「古いログ」と明示し、リセット経過後はその枠の数値を消します。
表示は毎秒リセット経過を再評価します。

調査の根拠、候補比較、確認済み/未確認の区別は [docs/research.md](docs/research.md) を参照してください。
この環境に実セッションログはなく、公開ソースと合成データで検証しています。

## セットアップ

必要環境: Windows 11、Python 3.10+、ChatGPTでログインしたCodex CLI。
Pythonと本アプリは、Codexを利用するユーザーと同じWindowsユーザーで実行してください。

ZIPを展開し、PowerShellで `codex-usage-tray` フォルダに移動して実行します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# まず使用量とデータ源の状態を確認
.\.venv\Scripts\python.exe usage.py diag
.\.venv\Scripts\python.exe usage.py

# トレイを起動（ログをコンソールにも表示）
.\.venv\Scripts\python.exe tray_app.py
```

コンソールを出さず常駐するには、同じvenvの `pythonw.exe` を使います。

```powershell
.\.venv\Scripts\pythonw.exe tray_app.py
```

タスクトレイの隠れたアイコン欄も確認してください。同じWindowsセッションでの二重起動は防止しています。
使用量イベントがない場合は「不明」となります。Codex CLIを通常利用してから更新してください。

依存は `pystray` と `Pillow` の2つだけです。ネットワーク取得がないため `certifi` は不要と判断しました。
追加のNodeツールやHTTPライブラリは必要ありません。

## 自動起動

右クリック →「Windows起動時に自動起動」をチェック。
ユーザーのStartupフォルダに `CodexUsageTray.lnk` を作り、現在のPython環境の `pythonw.exe` を指定します。
Windowsへのログイン時に起動します。チェックを外すと解除します。
フォルダやvenvを移動した場合は、OFF→ONでショートカットを作り直してください。

```powershell
.\.venv\Scripts\python.exe autostart.py status
.\.venv\Scripts\python.exe autostart.py enable
.\.venv\Scripts\python.exe autostart.py disable
```

COM操作はWindows標準のPowerShell経由です。15秒のタイムアウトを設け、パス内の引用符もエスケープします。

## 設定

アプリと同じフォルダの `config.json` を編集し、アプリを再起動します。
部分設定はdeep mergeで既定値に補完されます。壊れた設定や不正な値は既定値に戻し、ログとツールチップに注意を表示します。

| キー | 既定 | 説明 |
|---|---|---|
| `source` | `local_logs` | 対応する唯一の取得方式 |
| `codex_home` | `null` | nullなら環境変数CODEX_HOME、なければユーザーの `.codex`。認証診断にも使用 |
| `sessions_dir` | `null` | nullならcodex_homeの `sessions`。WSLなど別のログ保存場所も指定可能 |
| `poll_interval_sec` | `30` | 定期取得の秒数。10〜86400。API由来ではなくディスク負荷を抑える下限 |
| `manual_min_interval_sec` | `5` | 手動更新の最小間隔、5〜86400秒。成功/失敗どちらも連打を制限 |
| `stale_after_sec` | `300` | 「古いログ」と表示するまでの秒数、60〜604800 |
| `max_files` | `100` | mtimeが新しい順に読み取るファイル数上限、1〜10000 |
| `max_tail_bytes` | `4194304` | 各JSONLの末尾読取量、1024〜67108864バイト |
| `thresholds.warn` | `0.60` | 緑の上限、0〜1の比率 |
| `thresholds.crit` | `0.85` | 黄の上限。warn < crit ≤ 1が必要 |
| `colors.ok / warn / crit` | 緑 / 黄 / 赤 | 各色のRGB整数配列（各成分0〜255） |
| `colors.track / unknown` | 暗灰 / 灰 | 未使用部と不明な枠の色 |
| `show_center_text` | `true` | 中央に5時間枠の整数%か `?` を表示 |
| `icon_size` | `64` | 内部画像サイズ、32〜256。4倍で描画して縮小 |

下限未満・範囲外の設定は既定値へ戻します。長い設定注意はツールチップに収まらないため、ログで全文を確認してください。
相対パスは起動時の作業ディレクトリ基準です。ログの場所を指定する場合は絶対パスを推奨します。

別のCodex保存場所の設定例（Windowsパスの `\` はJSONでは `\\`）：

```json
{
  "codex_home": "D:\\CodexHome",
  "poll_interval_sec": 60,
  "thresholds": {"warn": 0.50, "crit": 0.80}
}
```

## ログと状態

- ログ: `%LOCALAPPDATA%\CodexUsageTray\tray.log`。512KiBで1世代ローテーションします。
- 状態: 同じディレクトリの `state.json`。使用率・リセット・観測時刻とデータ源のディレクトリだけを原子的に保存します。
- 読み取り失敗は種類と固定の理由を表示します。セッション本文・JSON行・認証値・例外に含まれる入力値は記録しません。
- ログや状態を保存できない場合もメモリ上の最後の値を保持し、UIで保存失敗を知らせます。
- `LOCALAPPDATA` がない診断環境ではユーザーホーム配下を利用します。

`python usage.py diag` はauth.jsonの存在と、許可したキー名・型だけを表示します。
JWT解析・トークン表示・認証更新・認証ファイルへの書き込みは行いません。
通常の取得とトレイ実行ではauth.json自体を読みません。
auth.jsonがないことは、keyring等の認証保存方式を使っている可能性もあり、ログ読取りの失敗とは別です。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `tray_app.py` | Windows入口、メニュー、ポーリング、二重起動防止、リセット再評価 |
| `usage.py` | JSONL読取、最新イベント選択、schema検証、スナップショット、復元、CLI診断 |
| `gauge.py` | 二重リングと中央文字、サンプルPNG生成 |
| `config.py` / `config.json` | 既定値、deep merge、設定検証 |
| `appdata.py` | ローテートするログ、原子的な状態保存 |
| `autostart.py` | Startupショートカット作成・削除・確認 |
| `requirements.txt` / `.gitignore` | 依存と認証・実ログ・実状態等の除外 |
| `test_usage.py` / `test_gauge.py` | 実通信なしの合成データ・モックテスト |
| `sample_30.png` / `sample_70.png` / `sample_95.png` / `sample_unknown.png` | 描画サンプル |
| `docs/research.md` | 実装前の調査、取得方式比較、未確認事項 |
| `docs/validation.md` / `docs/unittest-output.txt` | 検証結果と全テスト出力 |
| `README.md` | 本書 |

## 検証コマンド

venvを有効にしている場合、または同じPython環境に依存を入れている場合：

```powershell
python -m unittest -v
python usage.py
python usage.py diag
python gauge.py
python tray_app.py
```

`usage.py` は使用量イベントを読み取れた場合にexit 0、取得に失敗した場合はexit 1を返します。
読み取れてもリセット経過後の枠は不明になるため、exit 0だけで現在値があるとは判断しないでください。

## トラブルシューティング

| 表示・症状 | 対応 |
|---|---|
| ログ取得失敗 / FileNotFoundError | `usage.py diag` でsessions_dirを確認。Codexの保存場所とWindowsユーザーが一致しているか確認 |
| ログファイルなし / 使用量イベントなし | Codex CLIを通常利用してログを生成。APIキー利用や保存設定によってrate_limitsがない場合もある |
| 片方の枠が不明 / 枠長不明 | プラン・CLI版によって300/10080分の枠がない可能性。推測で枠長を変更せず実際のschemaを確認 |
| リセット経過 / 古いログ | Codexの新しい使用量イベントが必要。手動更新はローカルファイルを読むだけなのでサーバーへ更新を要求しない |
| JSONL破損 / 書込途中 | 次回読取を待つ。完全な正常イベントがあれば表示しつつ注意を出す。エラー内容に会話本文は含めない |
| PermissionError / 状態保存失敗 | sessionsとLOCALAPPDATAへのアクセスを確認。他ユーザーの資格情報で実行しない |
| 設定読込失敗 | JSONの文法とキーの型を確認。`config.json` がなければ既定値で起動 |
| 起動失敗 / ModuleNotFoundError | 起動に使うPython環境で `python -m pip install -r requirements.txt` を実行 |
| 自動起動設定失敗 / TimeoutExpired | Windows標準PowerShellとStartupフォルダの権限を確認。組織の実行制限があれば管理者へ確認 |
| アイコンが見えない | 隠れたアイコン欄を確認。ログも確認。Linux/macOSではトレイ入口はexit 1で終了 |

ログ/状態が破損している場合はアプリを終了し、state.jsonを退避してから再起動できます。
アプリはCodexのセッションログを変更しません。

## 既知の限界

- **Windows 11での起動、DPI別表示、自動起動、実アカウントとの数値一致は未確認**です。LinuxでのモックテストとPNG描画だけを確認しました。
- ログ形式は公開APIの安定性保証がありません。将来のCLIやプランでschemaが変わる場合、不明やschemaの注意を表示します。
- 新しいCLIで導入される圧縮ログ、`.jsonl.zst`、別のthread store、archived_sessionsは読取り対象外です。公式app-server連携は将来の代替候補です。
- 読取り範囲は既定で最新mtimeの100ファイル・末尾4MiB。実際の最新usageが範囲外にあると見逃します。必要に応じて上限を増やしてください。変更なしのファイルは再解析を省きます。
- 枠の最新値は一つのイベントから取り、別イベントの片方の枠を補完しません。最新の片側欠損は不明になります。
- ログはアカウント所有者を識別できないため、アカウント切替を自動検知しません。アカウント別のCODEX_HOMEとsessionsを使い、切替時には旧ログ・stateを分離してください。
- 100%を超える値はツールチップに元の使用率を表示し、リングと中央数値は100までに制限します。マイナス値も注意し、描画は0までに制限します。
- 時刻はUTCで保持し、表示時にWindowsのローカル時刻へ変換します。リセット時刻は月日・時分で表示し、端末時計が正しいことを前提とします。
- Windowsのツールチップは127 UTF-16単位の上限があります。長い注意・理由は切り詰め、全文をtray.logへ保存します。
- 非公開のChatGPT使用量APIは存在を公開ソースで確認しましたが、本アプリは呼びません。直接利用には規約・仕様変更・レート制限・認証の追加調査が必要です。

## 参照実装との差分

参照: https://github.com/sota843/claude-session-show 。設計を踏襲した独自実装です。
OAuth自動更新、トークン再取得メニュー、Claudeのエンドポイント、User-Agent、ccusage推定を削除しました。
リセット経過・欠損を不明にする処理、イベント時刻での鮮度表示、UTF-16のツールチップ制限、設定検証、
原子的な状態保存、二重起動防止、読み取り上限・解析キャッシュを追加しました。
