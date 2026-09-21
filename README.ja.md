# gtm-engine (オープンソース版)

<p align="center">
  <strong>言語 / Language:</strong>
  <a href="README.md">English</a> |
  <a href="README.zh-CN.md">简体中文</a> |
  <strong>日本語</strong> |
  <a href="README.es.md">Español</a> |
  <a href="README.de.md">Deutsch</a> |
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  <a href="https://github.com/henryroxstar/gtm-engine/stargazers"><img src="https://img.shields.io/github/stars/henryroxstar/gtm-engine?style=flat&label=Stars" alt="Stars" /></a>
  <a href="https://twitter.com/intent/tweet?text=The%20open-source%20GTM%20agent%20harness%20for%20startups%3A%2063%20skills%2C%20zero%20auto-spam%2C%20runs%20locally%20in%20Claude%20Code%20or%20Antigravity.&url=https%3A%2F%2Fgithub.com%2Fhenryroxstar%2Fgtm-engine"><img src="https://img.shields.io/badge/Share%20on-X-black?style=flat&logo=x" alt="Share on X" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+" /></a>
  <a href="https://docs.anthropic.com/en/api/agent-sdk/overview"><img src="https://img.shields.io/badge/built%20with-Claude%20Agent%20SDK-d97757.svg" alt="Built with Claude Agent SDK" /></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/connectivity-MCP--first-6E56CF.svg" alt="MCP-first" /></a>
  <a href="#アーキテクチャの特徴と安全設計"><img src="https://img.shields.io/badge/human%20gates-%E5%9B%9E%E9%81%BF%E4%B8%8D%E5%8F%AF%E3%81%AA%E6%89%BF%E8%AA%8D%E3%82%B2%E3%83%BC%E3%83%88-2ea44f.svg" alt="Human Gates" /></a>
  <a href="#ワークスペースと実行環境のサポート"><img src="https://img.shields.io/badge/workspaces-Claude%20%7C%20Antigravity%20%7C%20Cursor%20%7C%20Codex-orange.svg" alt="Harness Support" /></a>
</p>

![GTM Content OS & アウトバウンド・パイプライン](docs/assets/gtm-pipeline-flow.png)

*B2BソフトウェアスタートアップのためのオープンソースGo-To-Market (GTM) AIエージェント基盤。営業開拓・プリセールス・フィールドマーケティング活動を10倍に加速します。*

```
      .-"""-.
     /  o o  \        1つの頭脳、慎重で確実な複数の手 ——
     \   ^   /        すべての発信・アプローチは、あなたの承認が必須
      )-----(
     / /| |\ \
    ( ( | | ) )
     \_/ | \_/
        `-`
```

### あなたは創業者であり、同時にGTMチームの全員です。

スタートアップ初期、リソースは限られ、PMF（プロダクトマーケットフィット）の追求に奔走する日々。案件クローズだけが仕事ではありません。見込み顧客のパイプラインを自ら構築し、顧客の生の声を開発チームにフィードバックしてプロダクトを磨き上げ、まだ完成していない機能や先々週更新が止まったドキュメントについて商談で的確に語り、毎週変化する市場トレンドを捉えて新しいメッセージングを検証し、ターゲット購買層を惹きつけるコンテンツを発信し続ける必要があります。

これは本来5人分の職務です。従来のプレイブックは「5人採用しろ」と言いますが、あなたにあるのはノートPC1台、普段使っているAIワークスペース（Claude Desktop、Google Antigravity、Cursor、Codex）、そして限られた時間だけです。

**gtm-engine は、その5つの仕事をあなたと共に担うエージェント実行基盤です。** コールドプロスペクティング（見込み顧客発掘）、商談事前準備、アカウントプラン策定、提案スライド作成、週次市場レーダー、そしてマルチプラットフォーム対応の高品質コンテンツ作成（LinkedIn投稿、ブログ記事、ポッドキャスト台本、図解画像）——すべてチャットで一言伝えるだけで、あなたのブランドボイスと自社ナレッジに基づいて生成されます。

さらに重要なのは、**アーキテクチャの構造上、エージェントが勝手にメール送信やSNS投稿を行ったり、データを漏洩させたりすることが絶対にできない設計**になっている点です。根拠のない信頼を求めるのではなく、越権行為が物理的に不可能な構造を採用しています。

変わることのない3つの基本原則：
1. **あなたの明確な承認なしに、メール送信や外部公開は一切行われません**。
2. **企業ごとのデータは、各社専用のProfile（プロファイル）ディレクトリ内に完全に物理隔離されます**。
3. **エージェントにはRaw HTTPやターミナルShellの自由な実行権限がありません**。すべて検証済みのMCP（Model Context Protocol）ツール経由でのみ動作します。

![Content OS Architecture](docs/assets/content-os-grade-a-plus-architecture.png)

---

### なぜ gtm-engine なのか？（アーキテクチャ比較）

| 比較項目 | ブラックボックス「AI SDR」商用SaaS (11x, Artisan等) | 単純なPrompt対話 (ChatGPT / Claude) | 汎用Agentフレームワーク (CrewAI / LangChain等) | **gtm-engine (本システム)** |
|---|---|---|---|---|
| **導入コスト** | 月額 $500 – $3,000 | 月額 $20（ただし手動コピペの嵐） | Token従量課金 ＋ サーバーホスティング費用 | **基本 $0**（既存のAIワークスペースサブスクリプションで動作） |
| **外部発信の安全性** | コールドメールの自動送信（ドメイン失墜リスク） | 手作業によるコピー＆レビュー | ツール実行権限が広範に委譲される | **回避不能な人間承認ゲート**（プログラム上自動送信不可） |
| **自社コンテキスト理解**| 画一的なWebスクレイピング | 毎回チャットに会社概要を再貼り付け | ベクトルDBやパイプラインの独自構築が必要 | **Profile セカンドブレイン**（1度のオンボーディングで全スキルへ自動継承） |
| **対応ワークフロー** | コールドメールに限定 | テキスト生成に限定 | 複雑なコードやノードグラフの自作が必要 | **63種類のスキル ＆ 10大ワークフローパック**（動画・スライド・記事・SDR） |
| **データ主権とプライバシー**| 第三者クラウドSaaSへのロックイン | 学習データへの流用懸念 | 構築環境のセキュリティ依存 | **100% ローカル完結 / Gitignore**（データは手元のマシンから出ません） |

---

### 30秒クイックスタート

```bash
# 1. リポジトリをクローン
git clone https://github.com/henryroxstar/gtm-engine.git && cd gtm-engine

# 2. お好みのAIワークスペースでこのフォルダを開く (Claude Desktop、Google Antigravity、Cursor、または Codex)

# 3. チャットで次のように入力：
"set me up" --site yourcompany.com
```
*最初の実行にはDocker、常駐バックエンドサーバー、サードパーティAPIキーは一切不要です。*

---

## 実際の動作フロー（See it work）

複雑な初期設定は不要です。フォルダを開いて、チャットに一言打ち込むだけです。月曜日の朝、「何か有益な発信をしなければ」という思いがどう形になるかをご覧ください：

![実際の動作フロー：プロンプトから人間承認まで](docs/assets/see-it-work-workflows.png)

わずか1分前には白紙だった画面から、十分なリサーチに基づき、あなたの思考とトーンを的確に反映した投稿ドラフトが手に入ります。そして、すべての単語はあなたの承認を経て出力されます。

同じように、チャットの指示ひとつで1週間の重要業務をカバーします：

---

## あなたに合ったスタート方法

| あなたの役割やニーズ | 推奨される入口 | 所要時間 |
|---|---|---|
| **非エンジニア** —— コードではなくビジネス・営業に集中したい | [`END-USER-ONBOARDING.md`](END-USER-ONBOARDING.md) を参照。ターミナル不要、平易な言葉でのガイド | 約30分 |
| **エンジニア / 開発者** —— ワークスペースやCLIから対話的に操作したい | 下記の [はじめに（Chat モード）](#はじめにchat-モード) を参照 | 約10分 |
| **アーキテクチャ評価者** —— セキュリティ、制御フロー、設計方針を確認したい | [アーキテクチャの特徴と安全設計](#アーキテクチャの特徴と安全設計) を参照 | 約10分 |

---

## 4つの実行・統合モード

gtm-engine は、1つの共通コアエンジン (`gtm_core`) を4つの実行サーフェスで提供します：

**1 · Chat モード（デフォルト推奨 — インフラ不要）**
お好みのAIワークスペース —— **Claude Desktop**、**Google Antigravity**、**Cursor**、または **Codex** で本フォルダを開き、`"set me up"` とチャットするだけ。すべてのGTMスキルがローカルで実行され、自社のProfileを参照し、あなたの声で動作します。VPSやDocker、データベースは不要です。→ [はじめに（Chat モード）](#はじめにchat-モード)

**2 · 自律型セルフホストAgent（24/7 自動巡航）**
**Claude Agent SDK** ベースのランタイムを、ローカル常駐または自社 **VPS** にデプロイ。ニュース監視 → 企画 → リサーチ → 制作 → 配信準備を24時間コンテナで稼働させ、Telegramの承認ゲートで人間の合図を待ちます。詳細は [`docs/DEPLOY.md`](docs/DEPLOY.md) を参照。

**3 · クライアント REST API 開発**
ローカルのFastAPIバックエンド（`./scripts/stack.sh start`、ポート `:8000`）をPostgres・Redisとともに起動。独自の管理画面、ダッシュボード、モバイルアプリ等を開発するエンジニア向け。

**4 · 外部連携用 GTM MCP サーバー**
HTTP FastMCPコンテナ（`deploy/Dockerfile.mcp`、ポート `:8001`、APIキー認証 `sk-...`）を立ち上げ、厳選されたGTMツールを外部エージェント（外部Claudeインスタンス、LangChain、AutoGen、CrewAI等）へリモート提供。

### 実行パスの選択（混同を避けるために）

| パス | 対象ユーザー | 実行方法 | やってはいけないこと |
|---|---|---|---|
| **Chat モード (デフォルト)** | 創業者、営業、マーケター | AIワークスペースで開いて `"set me up"` | **Dockerの起動や `./scripts/stack.sh` の実行は不要です。** |
| **自律型セルフホストAgent** | 24/7自動化を求めるチーム | [`docs/DEPLOY.md`](docs/DEPLOY.md) に従ってDocker Compose起動 | このモードで即興のチャットはできません。Telegram通知で承認を行います。 |
| **クライアント API 開発** | カスタムUI/API連携を開発するエンジニア | `./scripts/stack.sh start` でローカルAPIを起動 | スキルをチャットで使いたいだけなら不要です。 |
| **外部連携用 MCP サーバー** | 外部エージェントからGTMツールを呼び出したい場合 | 8001番ポートでFastMCPコンテナを起動 | APIキー認証や予算上限の設定なしで外部公開しないでください。 |

### ワークスペースと実行環境のサポート

| ワークスペース / ランタイム | サポート状況 | スキルの読み込み方式 | 備考 |
|---|---|---|---|
| **Claude Desktop / Code** | ネイティブ対応 | プラグイン形式 (`plugin/`) | 全63スキル、MCP連携、インタラクティブ承認を完全サポート |
| **Google Antigravity** | ネイティブ対応 | `.agents/` による自動検出 | マルチエージェント協調、ネイティブコマンド・ファイル操作 |
| **Cursor / Codex** | 完全互換 | `.agents/AGENTS.md` + 設定ルール | チャット対話形式で各スキルを自然言語呼び出し |
| **Headless VPS (Agent SDK)** | 専用ランタイム | コンテナ化エージェントループ | Telegram承認ボットと連携した24/7自律巡航 |

---

## はじめに（Chat モード）

**事前準備：** Python 3.11+ および [`uv`](https://docs.astral.sh/uv/)。Chatモードでは、エージェントがステップ1の対話の中でセットアップを案内します。

**Step 1 — エンジンと自社プロファイル（Profile）の初期化**
チャットで `"set me up"` と入力します。環境チェックが走り、自社WebサイトのURLをもとに、ブランド、ターゲット購買層（ICP）、トーン、競合、プロダクト情報を自動抽出して初期Profileを構成します。

**Step 2 — 外部ツールとAPIキーの接続（任意設定）**
すべての外部サービス連携は任意であり、未設定時は自動的にAPIキー不要の公開Web検索へフォールバックします：
- **見込み顧客発掘連携**（Vibe、RocketReach、Apollo）：検証済みメールアドレスや購買シグナルの取得。
- **Webスクレイピング連携**（Firecrawl）：動的ページの高度な構造化抽出。
- **予算支出上限の設定**：初期設定時に月次および1回あたりの支出上限を設定。システムが有償APIを呼び出す前に自動で上限を確認します。

#### 環境チェックコマンド (`check_env`)
診断コマンドを実行して、Profile、外部ツール接続、予算制限が正しく設定されているか確認できます：
```bash
uv run python -m gtm_core.check_env
```

---

## 今日のやりたいことから探す（クイックインデックス）

| あなたの目的 | チャットに入力するフレーズ | 呼び出されるスキル | 主な成果物 |
|---|---|---|---|
| **見込み顧客と購買関与者の発掘** | `"find prospects in [業界/市場]"` | `prospect`, `draft-outreach` | 顧客評価リスト、HubSpot用CSV、検証済み連絡先 |
| **重要な商談・プレゼンの準備** | `"prep me for my call with [企業名]"` | `call-prep`, `account-dossier` | 5分要約ブリーフ、SPIN質問集、対抗事例集 |
| **高品質なSNS・専門記事の執筆** | `"draft my LinkedIn post about [話題]"` | `content-radar`, `content-studio` | 3パターンのフック案 (Gate 1) $\rightarrow$ 規約チェック済み原稿 (Gate 2) |
| **専門コミュニティでの価値提供コメント** | `"reply to this post: [URL]"` | `linkedin-reply`, `reddit-reply` | 売り込み感のない有益な返信ドラフト（要確認） |
| **エンタープライズ提案設計書の作成** | `"design the solution for [企業名]"` | `solution-discovery`, `solution-design`| システム構成案・現状分析を含む技術提案書 (SAD) |
| **アカウント攻略プランの策定** | `"build an account plan for [企業名]"` | `account-plan` | MEDDPICC評価表、ステークホルダー相関図、実行計画 |
| **環境状態と予算の確認** | `"run environment check"` | `check_env` CLI | プロファイル健全性、接続状況、予算保護レポート |

> 全63種類のスキル一覧は [`docs/SKILLS.md`](docs/SKILLS.md) をご覧ください。

---

## アーキテクチャの特徴と安全設計

1. **二重の人間承認ゲート（Gate 1 / Gate 2）は構造的にバイパス不可**
   エージェントは企画フェーズ（Gate 1: 切り口の選択）と完成原稿フェーズ（Gate 2: 送信・投稿の最終承認）で必ず停止します。システム全体で `autopublish: false` が強制されており、外部への自動送信は物理的に遮断されています。
2. **完全なテナント・プロファイル隔離**
   企業ごとのデータは `profiles/<profile_name>/` に独立して保持され、同一のエンジンで複数社を安全に運用できます。
3. **AIモデルは頭脳、MCPツールは唯一の手足**
   エージェントは直接外部ネットワーク通信を行わず、認可されたMCPツール経由でのみ外部リソースと対話します。APIキー等の認証情報はエージェントのコンテキスト内には露出されません。
4. **コードと自社情報の完全分離**
   自社名、製品情報、ターゲット像などのハードコードは一切なく、すべて実行時にProfileから安全に注入されます。

---

## スター履歴（Star History）

[![Star History Chart](https://api.star-history.com/svg?repos=henryroxstar/gtm-engine&type=Date)](https://star-history.com/#henryroxstar/gtm-engine&Date)

---

## ライセンス

本プロジェクトは **Apache License 2.0** のもとで公開されています —— 詳細は [`LICENSE`](LICENSE) をご確認ください。商用利用、改変、再配布が自由に認められています。
