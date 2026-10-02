# ローカルAPIの性能テスト

現段階では実装と計測ロジックの確認まで。モデル実機測定は未実施です。
候補と根拠は [モデル選定メモ](research/2026-10-03-model-selection.md) を参照。

## 関数の構成

`scripts/benchmark_models.py` は、最初に `MODEL_URLS`、次に `QUESTIONS` を定義しています。
モデルの配布ページではなく、revisionを固定したGGUFファイルの直接URLです。
ファイルサイズとSHA-256も記録し、ダウンロード後と実験開始前に確認します。

- `download_models()`：重みを取得する。実験とは別の操作。
- `load_model()`：モデルごとにllama.cppのAPIプロセスを起動し、ロード完了を待つ。
- `measure_response()`：質問をHTTPで送り、返答と時間を取得する。
- `benchmark_loaded_model()`：warmup、各質問のキャッシュ消去、繰り返しを管理する。
- `run_benchmark()`：モデルとGPU／CPUを順番に切り替え、結果を保存する。

GPUとCPUには同じGGUFとllama.cppを使います。MLXとの基盤差による影響を避けられる設計です。
同時利用は1件、文脈長4,096、CPUスレッド8を初期値とします。
GPUは実デバイスとレイヤー配置を確認し、CPUへフォールバックした測定をGPUとして保存しません。

## 計測の範囲

ロード完了 → warmup → KVキャッシュ消去 → **計測開始：HTTP送信 → 入力処理 → 思考と生成 → 完了通知：計測終了** → 保存 → モデル停止。

- `ttft_seconds`：最初の生成テキストまで。思考が先に出る場合は思考を含む。
- `ttfa_seconds`：最初の回答本文まで。利用者が答えを読み始められる時点。
- `total_seconds`：リクエスト送信からSSEの `[DONE]` まで。
- `generation_tokens_per_second`：サーバーが報告する生成速度。HTTP全体の速度とは異なる。

HTTP接続、入力のトークン処理、推論、出力生成はAPI応答時間へ含めます。
重みの取得・検証、モデルロード、warmup、KV消去、プロセス停止、ファイル保存は含めません。
測定中は出力をコンソールへ逐次表示せず、完了後に表示・保存します。
各質問は新しい会話です。前の回答を引き継がず、同じ質問の繰り返しにもキャッシュを使いません。
OSのファイルキャッシュを一律消去する操作は行いません。モデルをロード済みのAPIを測る実験です。

## 質問と評価

8問は、説明、資料の要約、JSON抽出、計算、メールの書き直し、根拠のない回答の回避、Pythonコード、X投稿です。
日本語の実用タスクを比較する小規模な検証で、モデルの一般的な賢さを決める試験ではありません。

各回答を次の3軸で0〜2点ずつ評価すると、記事で根拠を示しやすくなります。

- 正確さ・根拠：資料や計算と一致し、情報を追加しないか。
- 指示への適合：文字数、件数、JSONなどの出力指定を守るか。
- 日本語：自然で理解しやすいか。

JSON課題の期待値は `{"name":"青空会","date":"2026-10-12","attendees":18,"location":null}`。
計算課題の答えは20件（10＋6＋4）。コード課題は時刻の書式が未指定なので、整数ステータスの集計と空行・不正行の扱いを評価します。
モデル名を伏せて人間が評価し、採点基準と実際の回答を一緒に公開する方法を推奨します。
生成されたコードの実行はこのスクリプトでは行いません。

## 思考モードと条件

`--thinking auto` は各モデルの既定値、`off` / `on` は切り替え可能なモデルへ明示します。
LFM2.5-2.6Bは公式に常時思考モデルとされているため、含める場合は `auto` を使います。
Qwen3.8の挑戦枠は `reasoning_effort=xhigh` と思考有効を明示し、公式推奨のサンプリングを指定します。
その他の基本モデルは固定の共通設定（temperature 0.7、top_p 0.8、top_k 20、repeat_penalty 1.0）です。
これは今回の比較用設定で、各モデルの最適設定を検証したものではありません。
実際に送った設定を各行の `request_settings` に保存します。

出力上限は思考と本文を合わせて数えます。512トークンは短い動作確認の初期値です。
`finish_reason=length` は `truncated`、本文なしは `no_answer` と記録し、完成した回答の中央値から除外します。
失敗件数は併記します。途中打ち切りの速さを成功として比較しないためです。
思考が長いモデルは上限を増やして再測定します。xhighを指定しても、強い量子化と短い文脈・出力上限ではAAのスコアを再現した検証にはなりません。

同じモデルのCPU／GPU比較では設定を統一してください。
モデル間ではトークナイザーが異なるため、tokens/sだけで日本語の速さを判断せず、同じ質問の待ち時間と回答内容を比較します。
初期の3回測定は中央値を出します。少数の標本からp95を主張しません。
記事用には質問と機種を明記し、モデルの実行順を変えた追加測定で熱や他のアプリの影響を確認します。

## 実行手順

Python 3.10以上、curl、llama.cppのサーバーが必要です。
Pythonの追加パッケージは不要です。macOSなら実行基盤の導入候補は次のコマンドです。

```bash
brew install llama.cpp
llama-server --version
llama-server --list-devices
```

`llama-server` がなく `llama` が入る配布なら、スクリプトは `llama serve` を使います。
現在の公式CLI仕様を参照して実装していますが、このPCでの実モデルとの結合確認は次の段階です。
バージョンによる非対応フラグは、起動ログで確認して調整します。
[llama.cpp公式サーバー仕様（参照commit）](https://github.com/ggml-org/llama.cpp/blob/4ebdf2c74acce30883d8e34b7c70b3eb8146f2fe/tools/server/README.md)

まず計画を表示し、小さい1モデルだけで疎通します。

```bash
python3 scripts/benchmark_models.py plan
python3 scripts/benchmark_models.py download --models minicpm5-1b
./setup.sh --output .local/hardware-before-benchmark.json
python3 scripts/benchmark_models.py run --models minicpm5-1b --questions explain json --devices gpu cpu --thinking off --repeats 1
```

基本4モデルのGPUと、小さい3モデルのCPUを比較する場合：

```bash
python3 scripts/benchmark_models.py download
python3 scripts/benchmark_models.py run --max-tokens 2048
```

GPUのないサーバーでは `--devices cpu`。実行基盤とCPUの違いを含む別実験として記録します。
MacのCPU測定から、任意のサーバーの速度は推定できません。

Qwen3.8-27Bは既定のダウンロード・測定対象に含めず、挑戦枠を明示して実行します。

```bash
python3 scripts/benchmark_models.py plan --models qwen3.8-27b-iq2-s
python3 scripts/benchmark_models.py download --models qwen3.8-27b-iq2-s
python3 scripts/benchmark_models.py run --models qwen3.8-27b-iq2-s --questions reasoning --devices gpu --max-tokens 2048 --repeats 1
```

まずメモリとスワップを確認して起動・短い応答を試し、余裕があればIQ3_XXS版を選んで比較します。
Qwen3.8もCPUで試す場合は `--devices cpu --all-cpu` を明示します。
モデル順を変える場合は `--models` の順番を使います。

## 保存されるもの

`.local/benchmarks/実行日時/` に保存します。

- `results.jsonl`：各モデル、デバイス、質問、繰り返しごとの応答・思考・時間・失敗。
- `summary.json`：モデル・デバイス・質問ごとの完成回答の中央値と失敗数。
- `metadata.json`：重みURLとSHA-256、runtimeのバージョンとハッシュ、設定、コードcommitと未コミット状態。
- モデルごとの `.log`：起動やGPU配置の調査用ログ。

実測ファイルにはローカルの絶対パスも含み得るので、生成物はGit対象外です。
記事用の記録には必要な値を抽出し、初回PC情報や電源状態、アプリ状態、メモリとスワップの前後も記入します。
CLIは失敗・打ち切りがあれば終了コード1を返します。結果ファイルは残します。

## 実装を確認する

```bash
python3 -m unittest discover -s tests -v
```

疑似APIで計測範囲、思考と本文の区別、打ち切り、キャッシュ、GPUフォールバックを確認します。
これらのテストの秒数は模擬値で、LLMの性能データではありません。
