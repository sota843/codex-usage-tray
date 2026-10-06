# 使用量取得の調査

調査日: 2026-10-05。実装前に作成。対象は Windows 11 / Python 3.10+。

## 確認できたこと

- 参照実装 https://github.com/sota843/claude-session-show の README と全7個の `.py`（appdata / autostart / config / gauge / tray_app / usage / test_usage）を読んだ。二重リング、pystray、ポーリングスレッド、deep merge、Startupショートカット、ローカル状態保存の設計を踏襲する。Claude固有のOAuth更新・HTTPヘッダー・ccusageは採用しない。参照リポジトリにライセンスファイルが見当たらないため、コードの直接転載は避け、同じ設計を独自実装する。
- シェルの `git clone` は proxy:8080 への接続失敗。代わりに GitHub コネクターで参照ファイルとOpenAI公式リポジトリを取得できた。インターネット調査は部分的に可能。
- 実行環境は Linux。`codex --version` は `codex-cli 0.159.0-alpha.3`。`codex --help` に単独の `usage` サブコマンドはない。
- 初期確認では `/home/agent/.codex` と `/workspace/.codex` に auth.json と sessions ディレクトリはなかった。その後、CLI診断で環境変数CODEX_HOMEの別保存場所にauth.jsonがあることを確認した。許可したキー名・型のみを確認し、値は出力・記録していない。sessionsはなく、実JSONLとWindowsの認証保存方式は **未確認**。
- 以下の公開ソースでローカルJSONL方式の根拠を確認した。ソース確認とWindows実機確認は区別する。

## 公開ソースの根拠

公式: https://github.com/openai/codex 。調査時 main のコミットは `7f892275e31002f0422477c6219189284560e689`。

| ソース（コミット固定URLの末尾パス） | 確認内容 |
|---|---|
| [protocol.rs](https://github.com/openai/codex/blob/7f892275e31002f0422477c6219189284560e689/codex-rs/protocol/src/protocol.rs) | TokenCountEvent.rate_limits は optional。RateLimitSnapshot.primary / secondary は optional。used_percent は f64（0–100）、window_minutes は nullable整数の分、resets_at は nullable整数のUnix秒 |
| [history/lib.rs](https://github.com/openai/codex/blob/7f892275e31002f0422477c6219189284560e689/codex-rs/history/src/lib.rs) / [rollout_payload.rs](https://github.com/openai/codex/blob/7f892275e31002f0422477c6219189284560e689/codex-rs/history/src/rollout_payload.rs) | RolloutLineはtimestampとflattenされたRolloutItem。event_msg / payloadのwire schemaを確認 |
| [rollout/policy.rs](https://github.com/openai/codex/blob/7f892275e31002f0422477c6219189284560e689/codex-rs/rollout/src/policy.rs) | TokenCountイベントは永続化対象 |
| [rollout/recorder.rs](https://github.com/openai/codex/blob/7f892275e31002f0422477c6219189284560e689/codex-rs/rollout/src/recorder.rs) | CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl、timestamp付きJSONL行を追記 |
| [auth/storage.rs](https://github.com/openai/codex/blob/7f892275e31002f0422477c6219189284560e689/codex-rs/login/src/auth/storage.rs) | CODEX_HOME/auth.json。ファイル以外にkeyring等の認証保存方式もある |
| [token_data.rs](https://github.com/openai/codex/blob/7f892275e31002f0422477c6219189284560e689/codex-rs/login/src/token_data.rs) | tokens内のid_token / access_token / refresh_tokenは文字列、account_idはnullable文字列 |
| [rate_limit_resets.rs](https://github.com/openai/codex/blob/7f892275e31002f0422477c6219189284560e689/codex-rs/backend-client/src/client/rate_limit_resets.rs) | ChatGPT APIモードでGET {base_url}/wham/usage を使用。存在はソースで確認、実通信は未確認 |
| [v2/account.rs](https://github.com/openai/codex/blob/7f892275e31002f0422477c6219189284560e689/codex-rs/app-server-protocol/src/protocol/v2/account.rs) | account/rateLimits/readの応答はcamelCase形式のrateLimitsと複数bucketを扱う |

mainの最初の取得とtree取得は同じ調査セッション内だが、mainは変動するため将来の再現時は固定コミットを使う。

## スキーマ（キー名・型のみ）

JSONL実データは未確認。以下は公開ソースに基づく対応範囲。検証段階の診断では、クラウド実機のauth.jsonにOPENAI_API_KEY: null、auth_mode: string、last_refresh: string、tokens: object、tokens内のid_token / access_token / refresh_token: string、account_id: nullを確認した（キーと型だけ）。これはWindowsユーザーの認証方式を確認したことにはならない。

```text
auth.json:
  auth_mode: string | null
  OPENAI_API_KEY: string | null
  tokens: object | null
    id_token: string
    access_token: string
    refresh_token: string
    account_id: string | null
  last_refresh: ISO datetime string | null
  その他の拡張キー: optional（本アプリは利用しない）

rollout JSONL:
  timestamp: timezone付きISO datetime string
  type: string（event_msg）
  payload: object
    type: string（token_count）
    info: object | null（本アプリは利用しない）
    rate_limits: object | null
      limit_id: string | null
      primary / secondary: object | null
        used_percent: number
        window_minutes: integer | null
        resets_at: integer | null（Unix秒）
```

5時間=300分、7日=10080分という要件の枠をwindow_minutesで識別する。primaryが常に5時間、secondaryが常に週次とは決めつけない。分が欠損した枠や別の長さは誤表示防止のため不明とする。limit_idがcodexまたはnull/欠損の従来形式だけを採用し、別モデルのbucketを混ぜない。

## 候補比較

| 候補 | 使用率 / リセット | 鮮度 | 安定性 | 規約・非公開APIリスク |
|---|---|---|---|---|
| A ローカルrolloutログ | rate_limitsがあるイベントでは両方。null/片側のみもあり得る | 最後のイベントの時点。CLIを使わない間や他端末の使用は反映されない | 非公開の永続化形式。JSONL圧縮や保存方針変更に影響される | 外部アクセス・認証利用がなく低い。会話を保存・出力しない |
| B ChatGPT側GET /wham/usage | ソースでは使用率・Unixリセットにマッピング | 呼出時点に近いと考えられるが実通信は未確認 | 非公開のバックエンド契約。認証更新・account routing・複数bucket等の変更あり | 公開APIの保証・利用条件は未確認。採用には仕様と利用条件の追加確認が必要 |
| C codex app-server account/rateLimits/read | 公開ソースに両方の型と操作あり | サーバー取得時点。Windowsでの動作は未確認 | CLIバージョン、stdioハンドシェイク、認証更新に依存 | CLIに認証を任せられるがバックエンド通信・認証更新を伴う。今回は起動しない |
| C codex CLIコマンド | インストール版のhelpにusageサブコマンドなし | — | TUIの画面解析は不安定 | TUIスクレイピング不採用。公式Webドキュメント本文の取得は未確認 |

## 推奨とフォールバック

**Aを採用**。公開ソースで取得できる情報が確認でき、トークンを扱わず、追加のネットワークアクセスが不要。仮説だけに基づくAPI実装はしない。Windows上のJSONL実物がないため、ソースに基づく実装とモックテストを行う。

フォールバックはstate.json内の最後の値のみ。失敗を明示し、元イベントからの経過分を表示する。リセット時刻経過後はリングを灰色・数値を不明にする（0%にはしない）。リセット欠損も現在値としては表示しない。ポーリング成功で観測時刻を更新しない。毎秒表示を再評価してリセットを跨いだ表示を消す。トークン数から使用率や週次リセットを推定しない。

## 未確認と代替案・実機チェック

1. Windows 11上の実JSONLが上記schemaを持つこと。`python usage.py diag` とCodex CLIの使用量画面を照合する。
2. 実アカウントの枠長・bucket・resets_atの欠損率。該当しない場合は不明を表示し、スキーマのキー・型だけを追加調査する。
3. pystrayのDPI別表示、127 UTF-16単位のtooltip、Startupショートカット、pythonwでの起動・ログ。
4. 現行Codexの圧縮JSONLや別のthread storeには未対応。CLIの公式app-server連携を将来の代替にできるが、今回は未実装。
5. ログはaccount_idで所有者を特定できない。アカウント切替を自動判別できないため、専用CODEX_HOMEとsessionsを使い、切替時は旧ログと本アプリのstateを分離する。
6. BのAPIの利用条件・HTTPレート制限・Windows認証・ネットワークエラーは未確認。B/Cは実装しておらず通信を行わない。

読み取り負荷の方針: ローカル方式は既定30秒、下限10秒、手動下限5秒。APIの制限値ではなくディスク負荷と連打を抑えるための製品側の値。最新mtimeの100ファイル、各末尾4MiBを既定上限にし、変更のないファイルは解析キャッシュを使用。範囲外に最新のusageイベントがある場合は見逃す可能性があるため設定とREADMEで説明する。
