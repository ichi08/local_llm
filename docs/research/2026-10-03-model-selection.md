# モデル選定メモ

確認日：2026-10-03。対象PCはM1 Pro・メモリ16GB。
この記事の目的は、日本語の実用タスクについて「どのモデルが、どれくらい待てば使えるか」を実測で示すこと。
候補の動作、速度、品質はまだ実測していません。

## ランキングの読み方

[Artificial Analysis Small](https://artificialanalysis.ai/models/open-source/small/) は4B〜40B、[Tiny](https://artificialanalysis.ai/models/open-source/tiny) は4B以下を扱っています。
確認時、SmallではQwen3.8 27Bの推論設定違いが上位、TinyではK2 Horizon 3.7B、MiniCPM5-2Bなどが上位に掲載されています。
上位は調査候補にしつつ、16GBでのメモリ、実行基盤の対応、日本語の用途、CPUでの比較可能性を選定条件にします。
配信サービス側の速度をM1 Proの速度として引用せず、ランキングの総合評価から今回の日本語品質を断定しません。

## 基本の4モデル

- **MiniCPM5-1B**：小さなCPU/GPU比較の基準。Q4_K_Mは約0.69GB。公式GGUFがあり、思考の切り替えが可能。日本語の実用性は今回確認する項目。
  [公式モデル](https://huggingface.co/openbmb/MiniCPM5-1B)、[公式GGUF](https://huggingface.co/openbmb/MiniCPM5-1B-GGUF)。
- **Qwen3.5-2B**：小さな多言語モデルの候補。Q4_K_Mは約1.28GB。CPUとGPUの両方で測定する。
  [公式モデル](https://huggingface.co/Qwen/Qwen3.5-2B)、[Unsloth GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF)。
- **LFM2.5-2.6B**：CPU向けの軽量実行を検証する候補。Q4_K_Mは約1.67GB。公式は日本語を対応言語に含め、GGUFをCPU実行向けに案内している。常時思考モデルなので本文が出るまでの時間も確認する。
  [公式モデル](https://huggingface.co/LiquidAI/LFM2.5-2.6B)、[公式GGUF](https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF)。
- **Qwen3.5-9B**：2Bとのサイズによる速度・品質の変化を見る候補。Q4_K_Mは約5.68GB。まずGPUで試す。
  [公式モデル](https://huggingface.co/Qwen/Qwen3.5-9B)、[Unsloth GGUF](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF)。

すべて重みのファイルサイズで、実行時の必要メモリとは異なります。
基本4モデルはQ4_K_Mで揃えますが、量子化の作成元と詳細は同一ではありません。
MiniCPMとLiquidは公式GGUF、QwenはUnslothの変換です。配布元とrevisionをコードに固定しています。
CPU測定は初期設定で小さい3モデル。9Bも測る場合は `--all-cpu` を使います。

## Qwen3.8-27B xhigh：挑戦枠

利用者の希望により追加する対象。`xhigh` は別の重みではなく、同じモデルの推論設定です。
公式は `xhigh`、`medium`、`low` を案内し、思考と推論深度の制御を提供しています。
[Qwen公式モデルカード](https://huggingface.co/Qwen/Qwen3.8-27B)

Unsloth配布のメタデータで確認したファイルサイズは次のとおり。

- UD-Q4_K_M：約16.46GB（15.33GiB）。
- UD-Q3_K_XL：約13.15GB（12.24GiB）。
- UD-IQ3_XXS：約10.93GB（10.18GiB）。
- UD-IQ2_S：約8.37GB（7.80GiB）。

[Unsloth GGUF配布元](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF)、[ファイルメタデータAPI](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true)。

**推論**：16GiBをOS、他のアプリ、重み、計算用メモリで共有するため、Q4で十分な余裕を持つ運用は厳しい。
Q3でも余裕は小さく、Metalの割り当て制限や推論領域、スワップの影響を確認する必要がある。
初回の候補はIQ2_Sで、余裕があればIQ3_XXSも比較する。起動成功や実用速度は保証できない。

強い量子化と短い文脈・出力上限で測るため、「AA上位モデルをこの条件で試した」という実験として扱う。
AAの評価と同じ品質やスコアを再現したとは説明しない。GPUで起動できない場合もその条件と失敗を記録する。
スクリプトには2種類の直接URLとハッシュを定義し、明示的なモデル指定で選べるようにした。
`xhigh` と公式推奨の思考時サンプリングを送信し、思考の時間もAPI待ち時間へ含める。

## 今回、追加調査の候補にしたもの

K2 Horizon 7Bの公式GGUFは、K2対応のllama.cppを要求し、対応PRが進行中と案内している。
専用forkの導入と対応状況を確認する必要があるので、基本の比較を確立した後の候補とする。
[K2 Horizon公式GGUFの導入条件](https://huggingface.co/IFM/K2-Horizon-7B-GGUF)

Tiny上位のモデルもランキングだけで日本語適性を決めず、対応実行基盤と配布条件を確認して追加する。
実験対象を増やす前に、小さい1モデルで計測経路と質問・採点基準を確認する。

## 記事の組み立て

仮の問いは「16GBのMacで27Bまで試したら、日本語LLMはどこまで使える？」。
実測前に成功や勝者を決めず、速度だけでなく、要約の欠落、JSONの崩れ、答えを捏造しないかを実際の出力で見せる。
CPUで使える最小構成、9Bとの品質差、27Bのメモリ限界は、読者が自分のPCで試す判断材料になる。
反響は予測できないが、再現用コード、条件、回答例、失敗例を揃える方針にする。

## 配布元の確認

2026-10-03にHugging FaceのメタデータAPIでrevision、ファイルサイズ、SHA-256を取得した。
コードに定義した基本4＋挑戦枠2ファイルの直接URLはHEADリクエストでHTTP 200を確認した。
この確認では重み本体をダウンロードしていない。
Liquidのライセンスは独自のLFM Open License v1.0で、他の候補のApache-2.0と区別して扱う。
