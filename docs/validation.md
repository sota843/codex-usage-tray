# 検証記録

2026-10-05。Linux、Python 3.12.14、Pillow 12.3.0。
Windows、Python 3.10、pystrayのWindowsバックエンドは実行していない。

## 自動テスト

実行: `python -m unittest -v`（実通信なし、全て合成データ）。
全出力: [unittest-output.txt](unittest-output.txt)。

```text
----------------------------------------------------------------------
Ran 48 tests in 0.035s

OK
```

確認範囲: 5時間/週次の枠長識別、位置逆転、Unix秒、UTC/+09:00、0/100/100超/負数、
null/欠損/片側、リセット経過/欠損、イベントの鮮度、未来時刻、別bucket除外、
破損JSON/想定外schema/数値、ファイルなし/アクセス拒否/読み取り中の消失、
書込途中の行、末尾読取り、解析キャッシュ、再起動復元、保存失敗時の値保持、
ディレクトリ別stateの分離、秘密を出さない診断、設定deep merge/検証、
色の境界、未知と0%の区別、UTF-16のツールチップ上限、手動更新の連打制限。

## 描画とCLI

`python gauge.py` はexit 0：

```text
wrote sample_30.png
wrote sample_70.png
wrote sample_95.png
wrote sample_unknown.png
```

4枚を画像ビューアで目視。30の外周は緑、70は黄、95は赤、unknownは灰色の点線。
中央の30 / 70 / 95 / ?を確認。100の中央文字は内周内に収まるよう幅を制限している。
Windowsでの16/24px表示、高DPI、フォントの見え方は未確認。

実ログがない環境で `python usage.py` はexit 1となり、不明とFileNotFoundErrorを表示。
クラッシュせずローカル状態を保存した。検証用LOCALAPPDATAをプロジェクト外に指定した。
`python usage.py diag` はexit 0、データ源・認証の存在・許可したキー名と型だけを表示。
環境変数CODEX_HOMEの認証ファイルは存在するが、sessionsはないことを確認。
値をログ/文書に記録しなかった。取得方式はネットワークを利用しない。

`python tray_app.py` はLinux上でWindows専用と表示しexit 1。
Windows上で動いたという検証結果ではない。以下は実機で確認が必要：

1. 実ログのschemaとCodex側表示との使用率・リセットの一致。
2. `python` / `pythonw`のトレイ起動、メニュー、ログを開く操作。
3. 自動起動ショートカットの作成・実際のログイン起動・削除。
4. 二重起動防止、127 UTF-16単位のtooltip、高DPI。

## diff・構文・秘密情報確認

新規リポジトリの `git diff --cached` を読み直し、単位（分/秒/%/比率）、
しきい値の包含、範囲外のclamp、時刻変換、リセット境界を要件と照合した。
`python -m compileall -q .` と `git diff --cached --check` はexit 0。

以下をGitのステージ済みファイルに対して実施した。

```bash
git grep --cached -I -l -E 'sk-[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.|gh[pousr]_[A-Za-z0-9]{20,}'
git ls-files -- auth.json '**/auth.json' '*.jsonl' '*.jsonl.zst' state.json '*.log' '*.lnk'
```

結果: 秘密らしい形式の一致 **0件**、認証・実セッション・状態・ログファイルの登録 **0件**。
正規表現は全ての秘密を検出する保証ではない。データ源の値を転載せず、許可した型情報のみを扱い、
文書・コード・テストも目視確認した。ZIPにはGit登録対象のソース・文書・合成画像だけを含めた。

## 確信度

| 区分 | 評価 |
|---|---|
| 確実 | 上記環境で48テスト通過、4枚のPNG生成、CLIの失敗表示、構文検証、scan結果 |
| おそらく | 公開Codexソースのschemaに一致するJSONLを持つWindows環境で使用量読取りができる |
| 要確認 | Windowsトレイ、自動起動、実プラン/アカウントのログ形式と数値、DPI別表示 |

採用したlocal_logsは認証利用と非公開API直呼出を避けられる。
API・app-serverは実通信・認証競合・版依存の追加調査が必要なため今回不採用。
失敗時は保存済み値の表示に限定し、リセット経過後の値は不明とする。
参照実装との差分と作成ファイル一覧はREADMEに記載した。
