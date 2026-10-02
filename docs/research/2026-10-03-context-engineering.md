# create-ruleの改善と、このプロジェクトへの適用

確認日：2026-10-03。目的はAIが必要な文脈を取得し、測定条件を守って継続できるようにすること。
AIの成功率やコストが改善したかは未測定。

## 調べた一次資料

- [Anthropic：Effective context engineering for AI agents（2025-09-29）](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)。必要な情報を選び、詳細を必要時に取得し、長い作業では構造化したメモを残す考え方。
- [Cursor：Rules](https://prod.cursor.com/help/customization/rules)。AGENTS.mdを入口にし、特定ファイル向けのルールを条件付きで適用できる。具体的な必要に応じてルールを育てる。
- [Evaluating AGENTS.md（2026-02-12）](https://arxiv.org/abs/2602.11988)。評価対象では不要な要件を含むコンテキストが成功率やコストに悪影響を与える場合があった。
- [Do Context Files Help Coding Agents?（2026-07-28）](https://arxiv.org/abs/2607.27250)。2種類のエージェントと限られた実タスクで、コンテキスト戦略による正確さの改善を検出しなかった。一般化には限界がある。

資料全体から、ファイルを増やすことだけを改善と扱わず、必要な情報と作業中の判断に役立つ指示を優先する方針を採った。
これは資料からの設計上の推論で、このプロジェクトで効果を実測した結論ではない。

## スキルへの変更

- `.cursor/rules` だけに固定せず、既存のエージェントと用途に合う `AGENTS.md` と条件付きルールを選ぶ。
- スコープが依頼やファイルから分かる場合は推定し、結果を変える不明点がある場合に質問する。
- 常時読む制約、必要時に読む詳細、変化する進捗を分ける。
- 指示の正本を1か所に置き、重複・古い状態・一般論を減らす。
- 実際のコマンド・参照先・globの適合と、依頼の範囲を確認する。
- 構造を改善したことと、AIの性能改善を実測したことを区別する。

更新したスキルはこのリポジトリの `.agents/skills/create-rule/` にも保存する。
利用者自身のエージェントでのスキル配置先は、そのエージェントの仕様に従う。

## このプロジェクトへの適用

ルートの `AGENTS.md` を短い共通ルールと入口へ整理した。
ベンチマークの不変条件は `.cursor/rules/benchmark.mdc` に置き、`**/*benchmark*.py` の作業時に適用する。
詳しい計測設計は `docs/benchmark-design.md`、候補の根拠はモデル選定メモ、現在地は `docs/state.md` に置く。
記事の目的は記録しつつ、結果を先に決める指示は作らない。

## 確認

スキルのfrontmatterと参照構成はskill-creatorのvalidatorで確認した。
ベンチマークの計測条件は疑似APIで確認した。スキルそのもののAI性能への影響は未測定。
