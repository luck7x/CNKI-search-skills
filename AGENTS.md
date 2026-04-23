# CNKI Search Skill Rules

## CNKI Wrapper-Only Policy

- CNKI tasks in this package must use the local review wrapper first.
- The only standard browser session is `127.0.0.1:9222` with the local `.chrome-profile`.
- Do not switch CNKI work to `mcp-router`, `stealth-browser`, or any separately spawned browser instance.
- Diagnose CNKI state only from the wrapper session connected to `9222`.

## Preferred Entry Point

- Review and abstract screening: `cnki-review-fixed-local`
- Continue screening later pages: `review-expand --review-file ...`

## Operating Notes

- Do not pre-run `Test-NetConnection` as a gating ritual.
- If CNKI asks for login, institution auth, or captcha handling, let the user complete it in the opened Chrome window, then rerun the same command.
