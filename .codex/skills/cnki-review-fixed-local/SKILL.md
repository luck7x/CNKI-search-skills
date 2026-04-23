---
name: cnki-review-fixed-local
description: "Fixed CNKI review workflow for this project. Use when Codex needs a stable low-freedom literature review pass: first pull two result pages with abstracts, let the agent judge whether that set is sufficient, and if not continue with review-expand while keeping the query fixed. Do not use this skill to download PDFs. If the user later explicitly wants a file download, switch to $cnki-download-convert-local."
---

# CNKI Review Fixed (Local)

Use this skill as the default CNKI retrieval entry point in this project.

## Scope

This skill is intentionally low-freedom. It only does:

- advanced search
- fixed first-two-page initial collection
- abstract and keyword enrichment
- local `JSON + Markdown` bundle output for agent reading

It does not download PDF or CAJ files.

## When to use

Use this skill when the user wants to:

- search CNKI literature quickly
- review abstracts in a stable way before deciding whether enough papers have been surfaced
- compare abstracts before deciding what to read or download
- generate a structured bundle for downstream agent analysis

## When not to use

Do not use this skill for download requests.

If the user explicitly says to download a paper, or confirms download after you ask, switch to `$cnki-download-convert-local`.

## Prerequisites

- The CNKI session must already be logged in.
- If CNKI shows a captcha, pause and let the user solve it manually.

The local wrapper now handles the common machine-side prerequisites automatically:

- re-launches itself into the project `.venv` when the current Python is wrong
- auto-starts the project Chrome debug session on `127.0.0.1:9222` when the port is down

Do not waste turns repeatedly probing `9222` first. Call the skill directly.

## Fixed operating sequence for agent

Follow this exact sequence:

1. Call `python scripts/run.py ...` directly with the search arguments.
2. Read the returned `reviewJsonPath` and `reviewMarkdownPath`.
3. Judge whether the current first-two-page abstract bundle is enough.
4. If not enough, call `python ../../cnki-codex-skills/_shared/cnki/cli.py review-expand --review-file "..."`.
5. Only after the user explicitly wants files, switch to `$cnki-download-convert-local`.

Do not:

- pre-run `Test-NetConnection` as a gating step
- switch to MCP, `mcp-router`, or any separate browser instance for CNKI review
- download PDFs during the review stage

## Run

```bash
python3 scripts/run.py \
  --query "DOA估计" \
  --field-type TKA \
  --query2 "稀疏阵列" \
  --field-type2 TKA \
  --row-logic AND \
  --start-year 2021 \
  --end-year 2026
```

If the first two pages are not enough, continue with the fixed follow-up command:

```bash
python3 ../../cnki-codex-skills/_shared/cnki/cli.py review-expand \
  --review-file "outputs\\xxx.json"
```

## Expected result

Expect JSON with at least:

- `data.total`
- `data.pageSummaries`
- `data.items`
- `data.reviewJsonPath`
- `data.reviewMarkdownPath`
- `data.items[].selectionId`

## Operating rule

Keep the workflow fixed:

1. Start with `review-fixed`.
2. Read the returned abstracts and judge whether the current bundle is enough.
3. If not enough, run `review-expand --review-file ...` to continue screening later pages.
4. Only switch to `$cnki-download-convert-local` after the user explicitly wants downloads.

## Failure policy

- If the wrapper auto-starts Chrome and CNKI is not logged in yet, ask the user to complete the login in the opened Chrome window, then rerun the same command.
- If CNKI shows a captcha, tell the user to solve it in the opened Chrome window, then rerun the same command.
- If the wrapper fails on a real CNKI page interaction, stay on the wrapper path: inspect the current `127.0.0.1:9222` tab state, then fix the shared runtime or selectors instead of switching to MCP.
