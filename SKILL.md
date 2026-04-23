---
name: cnki-review-fixed-local
description: Fixed CNKI review workflow for Windows environments. Use when Codex needs a stable low-freedom literature review pass: pull two result pages with abstracts, judge whether the set is sufficient, and continue with review-expand while keeping the query fixed. Do not use for PDF downloads.
---

# CNKI Review Fixed (Local)

Use this skill as the default CNKI retrieval entry point.

## Scope

This skill is intentionally low-freedom. It only does:

- advanced search
- fixed first-two-page initial collection
- abstract and keyword enrichment
- local `JSON + Markdown` bundle output for agent reading

It does not download PDF or CAJ files.

## Prerequisites

- The CNKI session must already be logged in.
- If CNKI shows a captcha, pause and let the user solve it manually.

The local wrapper handles common machine-side prerequisites automatically:

- re-launches itself into the skill-local `.venv` when the current Python is wrong
- auto-starts the Chrome debug session on `127.0.0.1:9222` when the port is down

## Fixed operating sequence

Follow this exact sequence:

1. Call `python scripts/run.py ...` directly with the search arguments.
2. Read the returned `reviewJsonPath` and `reviewMarkdownPath`.
3. Judge whether the current first-two-page abstract bundle is enough.
4. If not enough, call `python scripts/runtime/cnki/cli.py review-expand --review-file "..."`.

Do not:

- pre-run `Test-NetConnection` as a gating step
- switch to MCP, `mcp-router`, or any separate browser instance for CNKI review
- download PDFs during the review stage

## Run

```bash
python scripts/run.py \
  --query "DOA估计" \
  --field-type TKA \
  --query2 "稀疏阵列" \
  --field-type2 TKA \
  --row-logic AND \
  --start-year 2021 \
  --end-year 2026
```

If the first two pages are not enough, continue with:

```bash
python scripts/runtime/cnki/cli.py review-expand \
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

## Failure policy

- If the wrapper auto-starts Chrome and CNKI is not logged in yet, ask the user to complete the login in the opened Chrome window, then rerun the same command.
- If CNKI shows a captcha, tell the user to solve it in the opened Chrome window, then rerun the same command.
- If the wrapper fails on a real CNKI page interaction, stay on the wrapper path: inspect the current `127.0.0.1:9222` tab state, then fix the shared runtime or selectors instead of switching to MCP.
