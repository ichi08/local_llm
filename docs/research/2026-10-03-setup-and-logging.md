# setup → benchmarkとログの改善

日付：2026-10-03。利用者の実装・Push・独立ログレビューの依頼に基づく。PCの識別情報・生ログ・ページ全文は公開記録に含めない。

## 変更の理由と内容

従来はPC調査だけのsetup、実行時に探したPython、curlの進捗、本体print、5秒ごとの0/280バーが混ざっていた。現在はsetupで専用venvとネイティブllama.cppの依存dylibを固定し、benchmarkがその環境を使用する。モデル取得はbenchmarkが担当する。

ログは単一プロセスが表示し、節目は追記イベント、待機は状態ファイルを参照する。ダウンロード中に応答数のバーを出さず、取得量／総量・割合・速度を表示する。TTYでは日本語の表示幅も考慮して1行更新、非TTYではCR／ANSIを出さず30秒heartbeatと節目を残す。再試行は課題キーの古い件数を置き換え、処理済み数が総数を超えない。

テンプレート適用後の入力を実tokenizerで確認し、全文が実効文脈長へ収まらない場合は失敗として記録する。POSIXタイマーでブロック中のSSEにも総時間・無通信の制限を適用し、本文・思考・timingsを途中保存する。入力・モデル・runtimeと依存・コード・設定の一致で再開でき、過去のattemptを保持する。

追加の全文資料、JS実行後DOM取得、会話課題、客観的な品質確認、人間の評価欄、HTMLレポート、起動時間と別プロセスによるRSS／swap／メモリ圧迫の保存を追加した。意味の正確さや翻訳網羅性を自動合格としない。Docker構成は追加していない。

## 条件と実機確認

M1 Pro、CPU10コア、GPU16コア、メモリ16GiB、macOS27.0。Python3.13の専用venv、llama.cpp 0.5.0 build11146 commit7fe450e19。実行ファイルと依存dylibのSHAはローカルのenvironmentと各実行のmetadataに保存する。最初に導入されたruntimeを固定する方式で、OSやPython本体まで隔離する方式ではない。

モデルはMiniCPM5-1B Q4_K_M、OpenBMB配布、revision `3d55fac80935ae6456986ad2384b5cbcc4d6c948`、サイズ688,065,920 bytes、SHA-256 `81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa`、Apache-2.0。取得済みを再検証して再利用した。

全文の元資料・抽出方式・入力SHAは [全文入力の記録](2026-10-03-full-page-input.md) と同じ。15,920文字、text SHA-256 `c64011d39b53af2d72efcb2945fbd0445ca12a27db2ed9036fc36ddd95d942e1`。

起動はparallel1、文脈長16,384、threads8、batch512／ubatch128、KV f16、fit off、context shift無効。CPUはGPU／KV／演算offload無効とno-repack。GPUはMTL0に25層、CPUは0層。各課題前のtokenize・warmup・KV消去・ログと保存は応答時間の外。

全文要約summary-overviewを各1回、thinking off、生成数-1、seed42、総時間45秒、無通信30秒で確認した。テンプレートを含む入力3,596 tokensが実行基盤のprompt_nと一致した。GPUは45.003秒、CPUは45.001秒で時間制限。途中本文6,324文字／2,577文字を保存し、自然終了0件として扱った。内容に反復が見られるため、これを回答品質の成功や完成回答の速度として報告しない。

起動はGPU0.616秒、CPU0.426秒。2秒間隔・各23標本の最大RSSは約1.14GiB／1.19GiB。GPUの共有メモリ全使用量ではない。PCには他の作業負荷と既存swapがあり、初回の環境準備も同時間帯に動いていた。今回の目的は動作確認であり、公平なモデル性能比較ではない。

自由創作creative-story、thinking auto、生成数-1、総時間10秒では入力3,605 tokensがprompt_nと一致し、思考4,238文字を保存した。同じ保存先で再開し、旧試行を残して新しいattemptが追記されることを確認した。

さらに総時間30秒のGPU／CPU計画を実際にCtrl+Cで中断し、GPUをcancelled、CPUをnot_run、終了コード130として保存した。途中思考505文字と再開案内が残った。同条件再開ではGPU／CPUをattempt2として実行し、最新2課題の件数が2/2を超えず、過去の中断・未実行行も残った。再開した応答も時間制限に達し、完成回答として集計しない。

## 入力取得とレポート

`realistic_pages.json` の日本語Google記事7,543文字、気象庁の気温FAQ885文字、Python3.13チュートリアル4,797文字を取得し、全文末尾までプロンプトへ付くことを確認した。それぞれの取得情報とSHAはローカル保存する。別のページ・取得日時の結果は別の入力として扱う。

実Chromium140.0.7339.16（Playwright1.55.0）でローカルのJS追加ページを取得し、動的文章・重複メニュー・末尾を保存した。viewport1440×900、domcontentloaded後3秒、最下部スクロール後2秒。保存したDOM時点の全文であり、無限スクロール等の網羅取得の保証ではない。

実ブラウザーでHTMLレポートの描画、モデルの絞り込み、途中回答表示を確認した。rawログ・HTML・全文入力・画像は.localへ保存しGitに含めない。

## 独立レビューと自動確認

別のレビュー担当が読み取り専用で実装と実出力を確認した。終了直前イベントの競合を独立再現し、terminal state検知後の再drainで解消した。watcherのSIGINT無視と親の中断処理、存在しない／破損した再開記録の日本語エラー、取得完了renameとstatの競合、ブラウザー導入表示、再試行の過剰カウントを修正して再確認した。

実PTY80桁でダウンロード20%→60%・速度・回答待ち・結果、非TTYでCR／ANSIなしの経過表示、短い連続イベントと最終イベントを確認した。独立レビューの最終確認では残る重大な表示問題なし。

自動テスト54件、シェル構文、plan、help、diffの空白を確認する。疑似APIの時間や疑似ダウンロード速度はLLM性能の実測ではない。全モデルの比較・全文翻訳の品質評価・27B・運用APIは未実施。

API仕様は使用したcommitの [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/7fe450e19305b828c199d602c23a8337aaa1f03b/tools/server/README.md)、ブラウザー取得は [Playwright Page API](https://playwright.dev/python/docs/api/class-page) を参照。
