# ローカルAPIの性能テスト

2026-10-03更新。要約・翻訳・解説・自由創作を各2問、同じ質問を5回測定します。
テスト関数、一括実行用sh、進捗監視を実装済みです。実モデルの性能測定は未実施です。
候補と根拠は [モデル選定メモ](research/2026-10-03-model-selection.md) を参照。

## 一括実行

Python 3.10以上が必要です。プロジェクトのフォルダで次を実行します。

```bash
sh benchmark.sh
```

基本4モデルをGPU、小さい3モデルをCPUで測り、8問×5回×7通り＝280応答を予定します。
macOSでllama.cppが未導入の場合、Homebrewがあれば自動で `brew install llama.cpp` を実行します。
Homebrewがない場合、またはmacOS以外では、llama.cppを用意する手順を案内して終了します。
Pythonの追加パッケージは不要です。重みの取得にはcurlを使います。

一括実行の流れ：実行基盤の準備 → 取得済み重みの探索・不足分取得 → PC状態を保存 → モデルを順次ロードして測定 → PC状態と結果を保存。
macOSでは `caffeinate -i` を使い、実行中のアイドルスリープを抑止します。
`setup.sh` は従来どおりPC調査の入口です。比較実験は `benchmark.sh` を使います。

```bash
# 取得も起動もせず、モデル・資料・質問・設定・応答予定数を見る
sh benchmark.sh --plan

# 27B xhigh / IQ2_SをGPUの挑戦枠に追加（計320応答を予定）
sh benchmark.sh --include-challenge

# 小さい1モデルで4分類を1回ずつ、GPU／CPUで確認
sh benchmark.sh --models minicpm5-1b --questions summary-overview translation-limit explanation-limit creative-story --repeats 1

# GPUのない環境では、小さいモデルだけCPUで測る
sh benchmark.sh --devices cpu

# 別の取得済み保存先を追加し、回答本文も端末へ表示
sh benchmark.sh --cache-dir /path/to/existing/models --show-answers
```

`--models` は指定した順で実行します。9Bや27BのCPU測定は `--all-cpu` で明示します。
IQ3_XXS版を試す場合は `--models qwen3.8-27b-iq3-xxs --devices gpu` を指定します。
Qwen3.8は強い量子化とxhighの組み合わせで、基本4モデルのQ4と異なる条件です。
起動できない場合も失敗を保存し、次のセッションへ進みます。
MacのCPU測定はこのMacの結果であり、別のサーバーの速度として扱いません。

## ダウンロード済みモデルの再利用

`models/`、Hugging Face、llama.cpp、LM Studioの一般的なキャッシュ保存先を調べます。
Hugging Faceの保存先は `HF_HUB_CACHE`、`HF_HOME`、`XDG_CACHE_HOME` の設定を反映します。
別の保存先は `--cache-dir` を複数回指定して追加できます。
ファイル名だけでなく、予定した重みのサイズとSHA-256が一致したものを、その場所からロードします。
見つかった重みの再ダウンロードやコピーは行いません。別の量子化・内容は別条件として扱います。
検証と探索は計測外です。

[Hugging Faceのキャッシュ構成](https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache)、[保存先の環境変数](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables)。

## 関数と入力の構成

`scripts/benchmark_models.py` に `MODEL_URLS` と、入力JSONを読む `QUESTIONS` を定義します。
重みはHugging FaceのGGUF直接URLで、revision・サイズ・SHA-256を固定します。

- `load_question_suite()`：資料と依頼文から質問リストを作り、ID等を検証する。
- `find_local_model()` / `download_models()`：既存の重みを優先し、不足分だけ取得する。
- `load_model()`：モデルごとにAPIを起動し、ロード完了とGPU配置を確認する。
- `measure_response()`：ロード済みAPIへ質問し、回答・思考・時間を取得する。
- `benchmark_loaded_model()`：warmup、KVキャッシュ消去、5回の測定を管理する。
- `run_benchmark()`：モデル・デバイスを切り替え、結果と未実行分を保存する。
- `ProgressReporter` / `scripts/watch_benchmark.py`：測定の間に進捗を書き、別プロセスで表示する。

入力は [Google Argonの資料と8問](../benchmarks/google_argon.json) を編集します。
`materials` に資料、`questions` に依頼文・分類・採点で見る点を置きます。
`--questions-file path/to/questions.json` で別の入力へ変更できます。
独自の入力では `material_key` と `instruction` の代わりに、完成した `prompt` を直接書くこともできます。

## Google Argonを題材にする理由

[GoogleのGemini 4 Argon公式発表](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/) を基に、2026-09-30の発表時点の内容を固定しました。
提供の段階、出力上限、入力・出力の価格と条件を保てるかを評価します。
モデルに題材の知識を暗記していることを要求せず、必要な資料を各質問に含めます。
実行時に記事を再取得しないので、後日の更新で入力は変わりません。
資料と英語の文章はテスト用に作成した言い換えで、Google原文の直接引用ではありません。
自由創作には、製品仕様と区別した架空の職場設定を使います。

- **要約**：概要を3項目に圧縮する課題と、提供状況だけを抜き出す課題。
- **翻訳**：出力上限の説明と、価格の条件を英語から日本語へ訳す課題。
- **解説**：入力と出力の違いを初心者へ説明する課題と、条件を使って料金を説明する課題。
- **自由創作**：指定語句を含む短い物語と、ユーモアのある投稿文を作る課題。

一律の120字・140字制限はありません。課題ごとに箇条書きの件数や段落数、訳文のみ等を指定します。
記事の公開先や反響の目標は、これらの入力や出力条件へ使いません。

## 計測の範囲

ロード完了 → warmup → KV消去 → **HTTP送信：計測開始 → 入力処理 → 思考と生成 → SSE完了通知：計測終了** → 保存・表示 → モデル停止。

- `ttft_seconds`：最初の生成テキストまで。思考が先なら思考を含む。
- `ttfa_seconds`：最初の回答本文まで。利用者が答えを読み始める時点。
- `total_seconds`：送信からSSEの `[DONE]` まで。
- `generation_tokens_per_second`：サーバーが報告する生成速度。

HTTP接続、入力の処理、思考と出力生成をAPI待ち時間に含めます。
インストール、ダウンロード、重みの検証、ロード、warmup、KV消去、停止、保存と本文表示は含めません。
各質問は独立した会話で、前の質問や同じ質問の繰り返しからKVキャッシュを使いません。
OSのファイルキャッシュを消去する実験ではありません。ロード済みAPIの応答を比較します。
モデルごとにトークナイザーが違うため、tokens/sだけで日本語の速さを判断しません。

## 監視と失敗の扱い

プログレスバーに処理済み数／予定数、モデル・デバイス・質問・何回目か、工程の経過秒、成功・失敗・未実行の数を表示します。
工程が変わったときと、待機中は5秒ごとに表示します。準備中は応答の完了数は増えません。
ロードや回答生成の進捗率を推定せず、工程名と経過時間を表示します。
監視プロセスはAPIやモデルへアクセスしません。測定プログラムが計測の前後に書く進捗JSONだけを読みます。
生成中の本文は表示せず、`--show-answers` がある場合は完了後に表示します。
ロードの詳細はモデルごとのログ、回答は逐次保存される `results.jsonl` と `answers.md` で確認できます。

`finish_reason=length` は `truncated`、本文なしは `no_answer` と記録します。
エラーでセッションを止めた場合、残りは `not_run` として記録して次のモデル・デバイスへ進みます。
起動失敗は `setup_error` と、対応する未実行の質問を残します。
バーの処理済み件数には未実行と確定した分も含めるので、100%は全件成功を意味しません。
Ctrl+Cでは自身が起動したモデル・監視プロセスを停止し、保存済みファイルを残します。

## 条件と統計

同じGGUFとllama.cppをCPUとGPUで使います。GPU配置を確認し、CPUフォールバックをGPU測定として保存しません。
同時利用1件、文脈長4,096、CPUスレッド8、出力上限2,048トークンを既定とします。
出力上限は思考も含み、文字数とは異なります。上限を変更した再測定は条件を記録します。
同じモデルのCPU／GPU間では、質問と生成設定を統一します。

基本4モデルは既定の思考設定 `--thinking auto` と共通のサンプリング設定を使います。
LFM2.5-2.6Bは常時思考なので、含める場合は `auto` が必要です。
Qwen3.8はxhighと、公式推奨の思考時サンプリングを明示します。
設定は各行へ保存します。AAのスコアを再現した試験とは扱いません。
[llama.cpp公式サーバー仕様（実装時の参照commit）](https://github.com/ggml-org/llama.cpp/blob/4ebdf2c74acce30883d8e34b7c70b3eb8146f2fe/tools/server/README.md)

モデル・デバイス・質問ごとに完成した応答の中央値・平均・標準偏差と、各回の値を保存します。
標準偏差は `statistics.stdev`、分母は有効件数−1、時間の単位は秒です。分散の出力はありません。
有効件数が2未満なら標準偏差は `null`、成功が0なら中央値・平均も `null` とします。
失敗や打ち切りの時間を完成回答の統計へ混ぜず、有効件数、失敗件数、未実行件数を併記します。
異なる質問の時間を混ぜてばらつきを出しません。5回すべて同じseed 42なので、主に時間のばらつきを見る比較です。
品質は1回目を事前の採点対象とし、課題の品質・日本語・指示への適合を各0〜2点で人間が評価する案です。
自動の品質採点は実装していません。評価観点と全回答を残すので、人間が確認できます。

## 保存先と確認

`.local/benchmarks/実行日時/` に次を保存します。

- `results.jsonl`：各回の本文・思考・計測値・設定・エラー・未実行。
- `answers.md`：回答を人間が読みやすい形式で保存。
- `summary.json`：中央値・平均・標準偏差・各回の値・件数。
- `metadata.json`：重みとruntimeの情報、入力ファイルのSHA-256、コードcommit、設定、セッション。
- `input-suite.json`：使った入力JSONのコピー。
- `hardware-before.json` / `hardware-after.json`：一括実行時のmacOSの前後状態。
- モデル・デバイスごとの `.log`：起動とGPU配置の詳細。

進捗JSONは `.local/progress/`。生成物にはローカルパスも含み得るためGit対象外です。
起動・取得のエラー、打ち切り等があれば終了コード1、Ctrl+Cは130を返します。

```bash
python3 -m unittest discover -s tests -v
sh benchmark.sh --plan
```

疑似APIで計測範囲、思考／本文、キャッシュ、打ち切り、進捗、統計を確認します。
疑似テストの秒数はLLMの性能データではありません。実モデルとの結合確認は次の段階です。
