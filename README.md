# local_llm

ローカルLLMをAPIとして運用するための、調査・実験・セットアップ手順を育てるプロジェクトです。
作業を記事にまとめ、リポジトリを取得した利用者がAIと一緒に自分のPCに合うAPIを構築できることを目指します。

**現在の段階：PCスペック調査。モデル実行とAPI構築はこれから実装・検証します。**

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

現状は調査まで実装済みなので、後続のAIは残りの段階を実装・検証しながら進めます。

## 目指すセットアップの流れ

1. `setup.sh` を起動する。
2. PCのCPU・GPU・メモリ・空き容量・実行環境を調査する。
3. 用途と、速度・回答品質・文脈長・同時利用数の希望を聞く。
4. 適切なモデルと量子化、実行基盤を選び、配布条件と容量を説明する。
5. モデルの重みをダウンロードする。
6. ローカルAPIを構築し、応答を確認する。
7. 起動・停止コマンドと、APIの利用例を説明する。

## 構成と読む順番

- `AGENTS.md`：AIが守る作業ルール。
- `docs/state.md`：現在の段階、完了事項、次の作業。
- `docs/project-context.md`：目的、設計方針、将来の実装範囲。
- `docs/research/`：記事の根拠になる調査・実験記録。
- `docs/experiment-template.md`：モデル比較の記録書式。
- `scripts/probe_hardware.py`：識別情報を除外してPCを調べる処理。
- `.local/`：Gitに含めない調査JSON、将来の個人設定・実行ログ。
- `models/`：将来の重み保存先。Gitには含めない。

## 最初の調査

[2026-10-03：M1 Pro / 16GBのPC調査](docs/research/2026-10-03-hardware.md)

## ライセンス

このリポジトリのコードと文書はMITライセンスです。
利用するモデルの重み・ライセンス・利用条件は、各配布元の規定に従います。
