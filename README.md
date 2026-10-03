# local_llm

個人PCのローカルLLMを、ノイズを含むページ全文で比較する実験用リポジトリです。現在の利用手順はmacOS向けです。運用用APIの常駐・起動停止は今後の段階です。

## 使い方

Python 3.10以上とHomebrewを用意して、次の順に実行してください。モデルは容量が大きいため、先に計画を確認できます。

```bash
git clone https://github.com/ichi08/local_llm.git
cd local_llm
./setup.sh
./benchmark.sh --plan
./benchmark.sh
```

`setup.sh` はPC調査、専用 `.venv` の作成、llama.cppの導入・固定を行います。`benchmark.sh` はその仮想環境と実行基盤を自動で使います。activateは不要で、別の仮想環境やCondaを有効にした端末、Rosettaの端末、別の作業フォルダからの実行にも対応します。

最初は取得済みの小さいモデルで確認することもできます。

```bash
./benchmark.sh --models minicpm5-1b --devices gpu --questions summary-overview --repeats 1
```

通常実行は基本4モデルをGPU、小さい3モデルをCPUで、8問×5回＝280応答測定します。既存の重みはサイズ・SHA-256を検証して再利用し、不足分だけ取得します。27Bは追加指定が必要です。

```bash
./benchmark.sh --include-challenge
./benchmark.sh --devices cpu
./benchmark.sh --cache-dir /path/to/models
```

## ログと結果

端末は「実行環境 → モデル取得 → 起動 → 入力確認 → 回答待ち → 結果」の工程を表示します。
ダウンロードはモデル名・取得量／総量・割合・速度、回答待ちは課題・GPU／CPU・繰り返し・待機時間を表示します。TTYでは待機行を更新し、ファイルへ出力するときは上書き文字を使わず、節目と30秒ごとの経過を残します。

```bash
./benchmark.sh > .local/benchmark.log 2>&1
```

結果は `.local/benchmarks/実行日時/` へ保存します。`report.html` をブラウザーで開くと、課題やモデルで絞り込み、全文入力・本文・思考・途中回答を確認できます。`results.jsonl` は過去の試行も保存し、`summary.json` は各課題の最新の試行から中央値・平均・標準偏差を計算します。完成していない回答を完成回答の速度統計へ混ぜません。

「自然終了」はAPIの完了を表し、回答品質の合格ではありません。必要語・数値の欠落候補・過剰な反復等の自動確認と、人間の評価を分けています。人間の評価は `quality-review.json` の未記入欄へ記入し、レポートを更新してください。

```bash
.venv/bin/python scripts/render_report.py .local/benchmarks/実行日時
```

## 中断と再開

Ctrl+C、時間制限、通信切断でも、受信済みの本文・思考・計測情報を保存します。停止時に表示される実行フォルダを指定すると、完了済みをスキップして未完了を再試行します。

```bash
./benchmark.sh --resume .local/benchmarks/実行日時
```

再開時の設定変更は受け付けません。入力・重み・実行基盤と依存ライブラリ・コード・測定設定が一致することを確認します。変更して比較する場合は、新しい実行として開始してください。過去の失敗は残り、集計には最新の試行を使います。会話課題は未完了の会話全体を最初から再試行します。

## ページ全文と制限

既定は [Google Argonの8問](benchmarks/google_argon.json) です。URL文字列ではなく、そのページ全体の文章を渡します。ナビゲーション、重複、関連記事、脚注、著者、フッター、画像のaltを保持し、本文の選別・要約・長さによる切り詰めはしません。script・style・templateのコードと画像内の文字は対象外です。

取得したHTML・全文・取得日時・方式・SHA-256を `.local/inputs/` へ保存し、以後は同じ入力を再利用します。各実行へ正確なプロンプトとページのコピーを保存します。`--plan` はページ取得とモデル起動を行いません。

生成トークン数の既定上限はありません（`--max-tokens -1`）。文脈長は16,384、応答の総時間は300秒、接続待ちは15秒、無通信は120秒です。計測前にモデルのテンプレートとtokenizerで実際の入力長・実効文脈長・出力に使える残りを確認し、全文が収まらないときは明示的に失敗として記録します。

```bash
./benchmark.sh --context-size 32768 --timeout 600 --idle-timeout 180
```

文脈・生成数・時間の制約で終了した応答は自然終了と分けます。時間制限は、ストリームが止まっている間にも適用します。

## 追加の実運用課題

日本語の記事・FAQ・技術文書、日→英の全文翻訳を別の入力セットに用意しています。英→日の全文翻訳は既定セットにあります。

```bash
./benchmark.sh --questions-file benchmarks/realistic_pages.json
./benchmark.sh --questions-file benchmarks/conversation.json
```

会話セットは全文入力への要約から、訂正・書き直しへ進む3ターンです。履歴を毎回全文送信し、各ターンでKVキャッシュを消去します。会話と独立課題は別の集計となり、異なるターンの時間を混ぜません。

JavaScript実行後のページを使う場合は、追加のブラウザーを導入し、入力JSONの `material_sources` に `"capture_method": "browser"` を設定してください。staticとbrowserには別の `cache_dir` を使います。

```bash
./setup.sh --browser
.venv/bin/python scripts/page_inputs.py https://example.com/page \
  --cache-dir .local/inputs/rendered-page --method browser
```

browserはChromiumで読み込み、待機とページ最下部へのスクロール後のDOMを保存します。ブラウザーのバージョン・取得条件も記録します。ログイン操作・無限スクロールの全件収集・別iframe・OCRは行いません。保存した時点の文書全体が比較入力です。

既存の取得結果を更新する場合だけ `--refresh` を指定します。過去のスナップショットは残ります。

## 実行環境の固定

`.local/environment.json` にPythonのバージョン・アーキテクチャ、llama.cppのバージョン、実行ファイルと依存dylibのSHA-256を保存します。Macではネイティブ実行を使い、最初に導入されたバージョンと依存ライブラリをプロジェクト内へコピーして以後固定します。OS・Metalドライバー・Python本体まで隔離する仕組みではありません。異なるPCでも完全に同一の実行環境になる保証はありません。

固定済みの実行基盤を更新するときだけ、次を使います。更新後の条件は別の実験になります。

```bash
./setup.sh --update-runtime
# Pythonの選択はsetup時のみ。benchmarkは常に.venvを使う
SETUP_PYTHON=/path/to/python3 ./setup.sh
# 導入済みの実行基盤を使う場合
./setup.sh --server-bin /path/to/llama-server
```

現在はMacのMetalを使うネイティブ構成です。Docker構成は実装していません。[Docker Model Runnerの公式説明](https://docs.docker.com/ai/model-runner/)でもMacの推論エンジンはコンテナ外のsandboxで動作するとされており、通常のLinuxコンテナのCPU測定とMacのGPU測定は区別する必要があります。

起動時間と、別プロセスによる2秒ごとのRSS・PC全体のswap・メモリ圧迫を記録します。RSSはGPU／共有メモリの全使用量ではありません。速度比較はPCの他の負荷も揃えて行ってください。

## 検証と記録

```bash
.venv/bin/python -m unittest discover -s tests -v
./benchmark.sh --help
```

実機での確認範囲と品質上の問題は [セットアップと表示の改善](docs/research/2026-10-03-setup-and-logging.md)、測定仕様は [性能テスト設計](docs/benchmark-design.md)、候補の根拠は [モデル選定](docs/research/2026-10-03-model-selection.md) を参照してください。全モデルの性能・品質比較は未完了です。

AIで作業を続ける場合は `AGENTS.md` と `docs/state.md` から読み始めてください。コード・手順・公開用の実験記録はGitへ、重みは `models/`、生成物と生ログは `.local/` へ置きます。

## ライセンス

コードと文書はMITライセンスです。モデルの重み・ライセンス・利用条件は各配布元の規定に従います。
