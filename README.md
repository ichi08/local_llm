# local_llm

ローカルLLMをAPIとして運用するための、調査・実験・セットアップ手順を育てるプロジェクトです。
作業を記事にまとめ、リポジトリを取得した利用者がAIと一緒に自分のPCに合うAPIを構築できることを目指します。

**現在の段階：PCスペック調査と、モデル比較コードの準備。実モデルの性能測定と運用用APIの整備は次の段階です。**

## まず動かす

現時点の調査スクリプトはmacOS向けです。Python 3.10以上が必要です。

```bash
git clone https://github.com/ichi08/local_llm.git
cd local_llm
./setup.sh
```

既に取得済みなら、ローカルの変更を確認してから `git pull --ff-only` で更新してください。

`setup.sh` はPCを調べ、要約を表示して `.local/hardware.json` に保存します。
今の段階ではインストール・モデルダウンロード・API起動は行いません。
必要なコマンドで取得できない項目は `null` と警告で示します。

```bash
./setup.sh --help
./setup.sh --output .local/hardware-before-benchmark.json
```

調査結果は端末ごとに変わるためGit管理の対象外です。公開用の実験記録は `docs/research/` にまとめます。

## AIに依頼する

プロジェクトフォルダをAIのワークスペースとして開き、次のように伝えてください。

> AGENTS.mdとdocs/state.mdを読んで、ローカルLLMのAPIを組みたい。
> まずこのPCのスペックを調べ、速度と回答品質の希望を聞いて、段階ごとに説明して進めて。

現状の `setup.sh` は調査まで実装済みなので、後続のAIは残りの段階を実装・検証しながら進めます。

## 日本語モデルの性能テスト

```bash
sh benchmark.sh
```

実行基盤の準備、既存重みの再利用／不足分取得、GPU／CPUの測定、結果保存まで一括で進みます。
macOSでllama.cppがなければHomebrewで導入します。Python 3.10以上が必要で、Pythonの追加パッケージは不要です。
準備中・ロード中・回答待ちを経過秒とともに表示し、全体の処理済み数をプログレスバーで確認できます。
macOSでは実行中のアイドルスリープを抑止します。

[Google Argonの入力JSON](benchmarks/google_argon.json) に要約・翻訳・解説・自由創作を各2問用意しています。
文字数を一律に制限せず、各質問を5回測定し、中央値・平均・標準偏差と各回の値を保存します。
ロード、モデル切替、warmup、KVキャッシュ消去はAPI応答時間へ含めません。
基本4モデルをGPU、小さい3モデルをCPUで測り、280応答を予定します。

```bash
# 取得・起動せず、入力と計画を確認
sh benchmark.sh --plan

# Qwen3.8-27B xhigh / IQ2_Sも追加（計320応答）
sh benchmark.sh --include-challenge

# CPUだけで測定
sh benchmark.sh --devices cpu

# 取得済みの保存先を追加。回答本文も端末へ表示
sh benchmark.sh --cache-dir /path/to/models --show-answers
```

結果は `.local/benchmarks/実行日時/` の `results.jsonl`、`answers.md`、`summary.json` 等へ逐次保存します。
実行手順・計測範囲・入力の編集方法は [性能テストの設計](docs/benchmark-design.md)、候補の根拠は [モデル選定メモ](docs/research/2026-10-03-model-selection.md) を参照してください。
計測ロジックと進捗・統計は疑似APIで検証しています。実モデルとの結合確認と性能測定はまだ行っていません。

## 目指すセットアップの流れ

1. `setup.sh` を起動する。
2. PCのCPU・GPU・メモリ・空き容量・実行環境を調査する。
3. 用途と、速度・回答品質・文脈長・同時利用数の希望を聞く。
4. 適切なモデルと量子化、実行基盤を選び、配布条件と容量を説明する。
5. 取得済みの重みを優先し、不足分をダウンロードする。
6. ローカルAPIを構築し、応答を確認する。
7. 起動・停止コマンドと、APIの利用例を説明する。

## 構成と読む順番

- `AGENTS.md`：AIが守る作業ルール。
- `docs/state.md`：現在の段階、完了事項、次の作業。
- `docs/project-context.md`：目的、設計方針、将来の実装範囲。
- `docs/research/`：記事の根拠になる調査・実験記録。
- `docs/experiment-template.md`：モデル比較の記録書式。
- `scripts/probe_hardware.py`：識別情報を除外してPCを調べる処理。
- `scripts/benchmark_models.py`：モデル取得、GPU／CPUのAPI測定、結果保存。
- `benchmark.sh`：比較実験を一括実行する入口。
- `benchmarks/google_argon.json`：入力資料、4分類8問、採点で見る点。
- `scripts/watch_benchmark.py`：APIに触らず進捗と待機時間を表示。
- `tests/`：計測範囲とAPIストリームの検証。
- `.agents/skills/create-rule/`：改善したコンテキスト設計スキルの配布用コピー。
- `.local/`：Gitに含めない調査JSON、将来の個人設定・実行ログ。
- `models/`：将来の重み保存先。Gitには含めない。

## 最初の調査

[2026-10-03：M1 Pro / 16GBのPC調査](docs/research/2026-10-03-hardware.md)

## ライセンス

このリポジトリのコードと文書はMITライセンスです。
利用するモデルの重み・ライセンス・利用条件は、各配布元の規定に従います。
