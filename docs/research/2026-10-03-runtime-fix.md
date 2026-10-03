# benchmark.shの導入修正と実機検証

確認日：2026-10-03。対象はM1 Pro、メモリ16 GiB、macOS 27.0。
依頼はHomebrewの導入失敗を直し、一括実行の準備処理内で解決して、テストで確認すること。
本記録は導入とAPIの結合確認であり、全モデルの速度・品質比較ではない。

## 確認した問題と修正

利用者から提示されたエラーは `brew install llama.cpp` の終了コード1で、Homebrewの詳細な標準エラーは提示されていなかった。
端末にはx86_64の表示があり、既存の仮想環境のPythonも3.9.12／x86_64だった。
同じPCでHomebrewをx86_64で診断すると、Command Line Toolsのarm64ライブラリを読み込めないエラーが出た。
初回のインストール失敗の原因をこの例外だけで断定せず、再現したアーキテクチャ不一致を対処対象にした。

HomebrewはApple Siliconの標準保存先を `/opt/homebrew`、Intel用を `/usr/local` と区別している。
`ensure_runtime()` はARM用Homebrewを `arch -arm64` で起動し、既存の実行ファイルの探索から導入、再探索、バージョン確認まで行う。
PATH外の標準保存先も調べ、見つけた実行ファイルを後続の測定へ引き継ぐ。
導入の標準出力・標準エラーはGit対象外の `.local/runtime/` に保存する。
[Homebrew公式：複数のインストールとアーキテクチャ](https://docs.brew.sh/Common-Issues#multiple-homebrew-installations)、[標準保存先](https://docs.brew.sh/Installation)。

`benchmark.sh` は、有効なPythonが3.10未満ならmacOSの標準保存先にある対応Pythonを選ぶ。
明示した `BENCHMARK_PYTHON` は優先する。arm64で動くPythonはarm64で起動する。
仮想環境を解除したり、シェル設定を変更したりする処理は追加していない。

実モデルの結合確認で、さらに次の問題が見つかった。

- デバイス一覧でCPUのAccelerate／BLASがMetalより先に出るため、最初の候補をGPUとして選ぶとCPU処理になった。BLASをGPU候補から除外した。
- 既定のログレベルではレイヤー配置のログが出ず、実際にGPUを使っていても検証できなかった。verbosity 4を指定して確認する。
- CPUへの切替時にポート確認が前の接続のTIME_WAITで失敗した。確認ソケットで `SO_REUSEADDR` を使う。
- KV消去APIがHTTP 501を返した。現在のサーバー実装はeraseでも `--slot-save-path` を要求するため、セッションごとに専用ディレクトリを指定する。キャッシュの保存／復元はしない。

KV消去の条件は [検証したcommitのサーバー実装](https://github.com/ggml-org/llama.cpp/blob/7fe450e19/tools/server/server-context.cpp) の `post_slots` で確認した。

## 重みと実行条件

- モデル：MiniCPM5-1B、Q4_K_M、公式OpenBMB配布。
- revision：`3d55fac80935ae6456986ad2384b5cbcc4d6c948`。
- ファイル：`MiniCPM5-1B-Q4_K_M.gguf`、688,065,920 bytes。
- 重みSHA-256：`81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa`。
- モデルのライセンス：Apache-2.0。本リポジトリのMITと別に扱う。
- 実行基盤：Homebrew llama.cpp 0.5.0、build 11146、commit `7fe450e19`、Darwin arm64。
- 実行ファイルSHA-256：`f0eaddaf08a91851c283229fa1cc0ad047de1e07b2703d2d46ece65fc3a459c8`。
- 修正の基点commit：`fafbd019fb333a63efa3b5f1c0828107eae1ad9b`。未コミットの修正を含む状態で検証した。
- 修正後の `scripts/benchmark_models.py` SHA-256：`e8c19baf3db0d7e23632a5e54463793a8fec25e7791c2163bb1596542b9f63a3`。
- 修正後の `benchmark.sh` SHA-256：`73c6d59775626b3895a3ac9e223e5497058f397b257c02fa7848bd2170dc77eb`。
- 文脈長4,096、スレッド8、同時利用1件、seed 42。GPUはMTL0、CPUはGPU配置0。
- temperature 0.7、top_p 0.8、top_k 20、repeat_penalty 1.0。

[公式GGUF配布元](https://huggingface.co/openbmb/MiniCPM5-1B-GGUF/tree/3d55fac80935ae6456986ad2384b5cbcc4d6c948)。
重みは `models/` に取得し、サイズとSHA-256を検証した。以降の実行は同じファイルを再利用した。
条件、入力、回答、失敗、セッションの起動コマンドは `.local/` の実行別記録に保存した。

## CPUの生成反復を回避した確認

`translation-limit` を各1回、出力上限2,048、思考offで実行するとGPUは完了し、CPUは訳文の表を繰り返して上限に達した。
既定の思考autoでもCPUの打ち切りが再現した。起動・API・KV消去は成功しており、打ち切りは `truncated` として保存された。
同じCPUの翻訳課題で `--no-repack` だけを追加すると `finish_reason=stop` で完了した。
このPCのrepack経路が結果に影響していると推測し、macOSのCPU測定へ `--no-repack` を追加した。
内部の原因を特定したものではなく、他のモデル・環境でも同じ障害があるとは断定しない。
失敗の記録は残し、repack有効時と無効時の結果を同じ条件の統計へ混ぜない。

## 検証

自動テスト31件、sh構文確認、help、planが成功した。
RosettaのPythonから `ensure_runtime()` の導入経路を実際に実行し、arm64のHomebrewを起動してバージョン確認まで終了コード0で完了した。
その時点ではllama.cpp導入済みだったため、再インストールは行われなかった。初回の実導入はarm64で成功済み。

古い仮想環境をPATHの先頭に置いたRosettaのシェルでも、対応Pythonの選択と計画表示が成功した。
疎通入力 `Reply with the single word OK.`、思考off、出力上限128、各1回で、GPU／CPUとも本文 `OK` と `finish_reason=stop` を取得した。
レイヤー配置はGPUが25、CPUが0、キャッシュ再利用は0。結果保存まで終了コード0だった。
この疎通の入力SHA-256は `5b0db91907cdda91ac1c049e7022626032414715ac3805986a6ee15177f5b615`。
最終修正後にも同じ疎通を再実行し、2応答とも成功、入口の終了コード0、CPUのrepack無効化、arm64、コードcommit、GPU配置と保存ファイルを確認した。
最終疎通の保存先は `.local/verification-benchmarks/20261003T052141-349936/`。起動したサーバーは終了し、測定用ポートに待受プロセスが残っていないことも確認した。

修正後に、古い仮想環境をPATHの先頭に置いたRosettaのシェルから次も実行した。
入口が選んだPythonとllama.cppはいずれもarm64。思考auto、出力上限2,048、各1回、4分類の指定課題をGPU／CPUで測った。

```bash
sh benchmark.sh --models minicpm5-1b \
  --questions summary-overview translation-limit explanation-limit creative-story \
  --repeats 1 --output-dir .local/verification-benchmarks
```

8応答のうち完了3、打ち切り5、起動・APIエラー0、未実行0だった。
GPUは要約と翻訳が完了し、解説と創作が打ち切り。CPUは翻訳が完了し、要約・解説・創作が打ち切りだった。
入力はリポジトリの `benchmarks/google_argon.json` をそのまま使い、モデルや課題の条件を成功させるために変更していない。
打ち切りがあるため、入口の終了コードは仕様どおり1。全件成功とは報告しない。
品質の採点はしていない。起動・応答の成功と、課題としての正しさは別に評価する必要がある。

全モデル280応答の比較、27Bの起動、回答品質の採点は未実施。
疎通成功は、全課題の成功や日本語品質を保証する結果ではない。
