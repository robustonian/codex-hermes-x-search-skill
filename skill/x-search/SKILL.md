---
name: x-search
description: Search X posts through Hermes x_search.
---

# X Search Skill

Use this skill when the user asks to search X, Twitter, posts, threads,
handles, reactions, or current discussion on X. This does not add a native
Codex tool. It provides a reliable wrapper around the local Hermes Agent
`x_search` setup by reading Hermes-managed credentials from `~/.hermes` and
calling xAI's Responses API directly.

## When to Use

- The user explicitly asks to search X or Twitter.
- The user asks for recent public reactions, posts, threads, or discourse on X.
- The user asks about posts from specific handles.

Prefer normal web search for general web pages, documentation, news articles,
or sources outside X.

## How to Run

Call the bundled wrapper with `terminal`:

Use `python3` when available. If the environment only exposes Python 3 as
`python`, use `python` instead.

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/x-search/scripts/hermes_x_search.py" \
  --query "latest reactions to Grok on X"
```

The wrapper does not require the `hermes` CLI on `PATH`. If needed, pass
`--hermes-home /path/to/.hermes`.

Use handle filters when helpful:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/x-search/scripts/hermes_x_search.py" \
  --query "latest product announcement" \
  --allowed-handle xai
```

Use date filters in `YYYY-MM-DD` format:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/x-search/scripts/hermes_x_search.py" \
  --query "discussion about OpenAI Codex" \
  --from-date 2026-05-01 \
  --to-date 2026-05-18
```

## Procedure

1. Run `--check` if you need to confirm availability before searching.
2. Convert the user's request into a concise X search query.
3. Add `--allowed-handle` for specific accounts, without `@`.
4. Do not combine allowed and excluded handles.
5. Read the returned JSON.
6. Summarize the `answer` field and include useful citation URLs from
   `citations` or `inline_citations` when present.

## Verification

Availability check:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/x-search/scripts/hermes_x_search.py" --check
```

Successful searches return JSON with:

- `success: true`
- `tool: "x_search"`
- `answer`
- optional `citations` and `inline_citations`

If `success` is false, report the `error` field plainly.
If the error mentions `NameResolutionError` or `Failed to resolve 'api.x.ai'`,
report that Codex network/DNS access is blocked or unavailable in the current
execution environment.
