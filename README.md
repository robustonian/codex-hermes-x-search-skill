# Codex x-search Skill

CodexからHermes Agentの既存`x_search`ツールを呼び出すためのローカルスキル配布リポジトリです。

Codexに新しいネイティブツールを追加するものではありません。Codexの`terminal`経由で、ローカルにあるHermes Agentの`x_search_tool()`を実行し、X上の投稿・スレッド・反応を検索します。

![Codex x-search Skill architecture](assets/architecture.png)

## できること

- X / Twitter上の投稿、スレッド、反応を検索する
- 特定のXハンドルに絞って検索する
- 特定のXハンドルを除外して検索する
- 日付範囲を指定して検索する
- Hermes側のxAI OAuthまたは`XAI_API_KEY`を使って認証する
- 検索結果をJSONで受け取り、Codexが要約や引用URL提示に使う

## できないこと

- Codexのネイティブツール一覧に`x_search`を直接追加する
- Hermes Agentなしで単独動作する
- xAI認証なしでX検索を実行する
- X以外の一般Webページを検索する

一般Web検索にはCodex標準のWeb検索やHermesの`web` toolsetを使ってください。

## 仕組み

このリポジトリは、人間向けの説明とエージェント向けスキル本体を分けています。

```text
codex-x-search-skill/
├── README.md
├── assets/
│   └── architecture.png
└── skill/
    └── x-search/
        ├── SKILL.md
        ├── agents/
        │   └── openai.yaml
        └── scripts/
            └── hermes_x_search.py
```

`skill/x-search/` の中身はCodexエージェント向けです。人間向けのREADMEや図解はスキルフォルダの外に置いています。

インストール後のCodex skillsディレクトリは次の形になります。

```text
~/.codex/skills/x-search/
├── SKILL.md
├── agents/
│   └── openai.yaml
└── scripts/
    └── hermes_x_search.py
```

処理の流れは次の通りです。

1. ユーザーが「Xで検索して」「Twitterの反応を調べて」と依頼する
2. Codexが`x-search`スキルを読み込む
3. Codexが`terminal`で`scripts/hermes_x_search.py`を実行する
4. wrapper scriptが`~/.hermes/hermes-agent`をPython pathに追加する
5. 必要ならHermes Agentのvenv Pythonで自動再実行する
6. Hermes Agentの`tools/x_search_tool.py`から`x_search_tool()`を呼ぶ
7. Hermes側の認証情報でxAI Responses APIへ問い合わせる
8. 結果JSONをCodexが読み、要約して回答する

## 必要条件

- Codexが動くローカル環境
- Hermes Agent checkout
  - デフォルト: `~/.hermes/hermes-agent`
- Hermes AgentのPython環境
  - デフォルト探索順: `~/.hermes/hermes-agent/venv`, `~/.hermes/hermes-agent/.venv`
- xAI認証情報のどちらか
  - `hermes auth add xai-oauth`で保存したxAI OAuth
  - `~/.hermes/.env`または環境変数の`XAI_API_KEY`

## 導入方法

このリポジトリを任意の場所にcloneします。

```bash
git clone <PRIVATE_REPO_URL> codex-x-search-skill
cd codex-x-search-skill
```

スキル本体だけをCodexのskillsディレクトリへ配置します。

```bash
mkdir -p ~/.codex/skills
cp -R skill/x-search ~/.codex/skills/x-search
```

既存ディレクトリを更新する場合:

```bash
cd codex-x-search-skill
git pull
rm -rf ~/.codex/skills/x-search
cp -R skill/x-search ~/.codex/skills/x-search
```

Codexを起動し直すと、スキル一覧に`x-search`が読み込まれます。

## 動作確認

まずは認証と登録状態を確認します。

```bash
python skill/x-search/scripts/hermes_x_search.py --check
```

成功例:

```json
{
  "success": true,
  "registered": true,
  "toolset": "x_search",
  "requirements_ok": true,
  "model": "grok-4.3",
  "timeout_seconds": 180,
  "retries": 2
}
```

`requirements_ok`が`false`の場合は、Hermes側のxAI OAuthまたは`XAI_API_KEY`を確認してください。

## 使い方

基本検索:

```bash
python skill/x-search/scripts/hermes_x_search.py \
  --query "latest reactions to Grok on X"
```

特定ハンドルに絞る:

```bash
python skill/x-search/scripts/hermes_x_search.py \
  --query "latest post from xAI" \
  --allowed-handle xai
```

特定ハンドルを除外する:

```bash
python skill/x-search/scripts/hermes_x_search.py \
  --query "discussion about Grok" \
  --excluded-handle xai
```

日付範囲を指定する:

```bash
python skill/x-search/scripts/hermes_x_search.py \
  --query "OpenAI Codex reactions" \
  --from-date 2026-05-01 \
  --to-date 2026-05-18
```

画像・動画理解を有効にする:

```bash
python skill/x-search/scripts/hermes_x_search.py \
  --query "posts showing Grok image examples" \
  --image-understanding
```

```bash
python skill/x-search/scripts/hermes_x_search.py \
  --query "videos about Grok launch reactions" \
  --video-understanding
```

## 出力形式

検索結果はJSONです。

```json
{
  "success": true,
  "provider": "xai",
  "credential_source": "xai-oauth",
  "tool": "x_search",
  "model": "grok-4.3",
  "query": "latest post from xAI",
  "answer": "...",
  "citations": [],
  "inline_citations": [
    {
      "url": "https://x.com/xai/status/...",
      "title": "1",
      "start_index": 92,
      "end_index": 143
    }
  ]
}
```

Codexは主に`answer`を要約し、`citations`または`inline_citations`にURLがあれば引用元として提示します。

## 設定

wrapper scriptはデフォルトで次のパスを使います。

```text
Hermes home:  ~/.hermes
Hermes Agent: ~/.hermes/hermes-agent
```

別の場所にあるHermes Agentを使う場合:

```bash
python skill/x-search/scripts/hermes_x_search.py \
  --hermes-home /path/to/.hermes \
  --hermes-agent /path/to/hermes-agent \
  --check
```

検索モデルやタイムアウトはHermes側の設定を使います。

```yaml
x_search:
  model: grok-4.3
  timeout_seconds: 180
  retries: 2
```

## トラブルシュート

### `requirements_ok`が`false`

Hermes側でxAI認証が使えない状態です。

確認するもの:

- `hermes auth add xai-oauth`を実行済みか
- `~/.hermes/.env`に`XAI_API_KEY`があるか
- `~/.hermes/hermes-agent/venv`が存在するか

### `Hermes Agent checkout not found`

`~/.hermes/hermes-agent`が存在しません。Hermes Agentの場所を`--hermes-agent`で指定してください。

### 検索が遅い

`x_search`はxAI Responses APIのserver-side検索を使うため、通常のWeb検索より時間がかかることがあります。Hermes側の`x_search.timeout_seconds`で調整できます。

### Codexがスキルを認識しない

配置先が`~/.codex/skills/x-search`になっているか確認し、Codexを再起動してください。

## ライセンス

このリポジトリのライセンス方針に従ってください。
