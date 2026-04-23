"""Search-related CNKI workflows."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import random
import re
from typing import Any

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parent))
    from browser import ChromeSession, CnkiError, blocked, ok, partial  # type: ignore
    from cnki_selectors import ADVANCED_SEARCH_URL, SEARCH_URL  # type: ignore
    from paper import ensure_detail_page, extract_detail_from_page  # type: ignore
else:
    from .browser import ChromeSession, CnkiError, blocked, ok, partial
    from .cnki_selectors import ADVANCED_SEARCH_URL, SEARCH_URL
    from .paper import ensure_detail_page, extract_detail_from_page


PARSE_RESULTS_JS = """() => {
  const rows = document.querySelectorAll('.result-table-list tbody tr');
  const checkboxes = document.querySelectorAll('.result-table-list tbody input.cbItem');
  const results = Array.from(rows).map((row, index) => {
    const nameCell = row.querySelector('td.name');
    const titleLink = nameCell?.querySelector('a.fz14');
    const authorCell = row.querySelector('td.author');
    const sourceCell = row.querySelector('td.source');
    const dateCell = row.querySelector('td.date');
    const dataCell = row.querySelector('td.data');
    const quoteCell = row.querySelector('td.quote');
    const downloadCell = row.querySelector('td.download');
    const isOnlineFirst = !!nameCell?.querySelector('.marktip');

    return {
      number: index + 1,
      title: titleLink?.innerText?.trim() || '',
      url: titleLink?.href || '',
      exportId: checkboxes[index]?.value || '',
      authors: Array.from(authorCell?.querySelectorAll('a.KnowledgeNetLink') || []).map(a => a.innerText?.trim()),
      journal: sourceCell?.querySelector('a')?.innerText?.trim() || '',
      date: dateCell?.innerText?.trim() || '',
      database: dataCell?.innerText?.trim() || '',
      citations: quoteCell?.innerText?.trim() || '',
      downloads: downloadCell?.innerText?.trim() || '',
      isOnlineFirst
    };
  });

  const totalText = document.querySelector('.pagerTitleCell')?.innerText || '';
  const totalMatch = totalText.match(/([\\d,]+)/);
  const pageInfo = document.querySelector('.countPageMark')?.innerText || '';

  return {
    total: totalMatch ? totalMatch[1] : '0',
    page: pageInfo || '1/1',
    items: results
  };
}"""

THESIS_SCOPE_SELECTOR = "a[name='classify'][resource='DISSERTATION'][data-chs='CDFD,CMFD']"
TOTAL_SCOPE_SELECTOR = "a[name='classify'][resource='CROSSDB'][classid='WD0FTY92']"
CHINESE_SWITCH_SELECTOR = ".switch-ChEn a.ch[data-val='Chinese'], .switch-ChEn a.ch"
THESIS_ALLOWED_DEGREES = {
    "both": {"博士", "硕士"},
    "doctoral": {"博士"},
    "master": {"硕士"},
}
DETAIL_RETRYABLE_ERROR_CODES = {"overlay", "page_error", "page_not_supported", "timeout", "browser_error", "unexpected_error"}
SORT_ID_MAP = {
    "relevance": "FFD",
    "date": "PT",
    "citations": "CF",
    "downloads": "DFR",
    "comprehensive": "ZH",
}
ADVANCED_SEARCH_JS = """async (config) => {
  const selects = Array.from(document.querySelectorAll('select')).filter(s => s.offsetParent !== null);
  const setValue = (el, value, eventName='change') => {
    if (!el) return;
    el.value = value;
    el.dispatchEvent(new Event(eventName, { bubbles: true }));
  };

  if (config.sourceTypes.length > 0) {
    const all = document.querySelector('#gjAll');
    if (all && all.checked) all.click();
    for (const key of config.sourceTypes) {
      const box = document.querySelector('#' + key);
      if (box && !box.checked) box.click();
    }
  }

  setValue(selects[0], config.fieldType);
  const input1 = document.querySelector('#txt_1_value1');
  if (input1) {
    input1.value = config.query;
    input1.dispatchEvent(new Event('input', { bubbles: true }));
  }

  if (config.query2) {
    setValue(selects[5], config.rowLogic);
    setValue(selects[6], config.fieldType2);
    const input2 = document.querySelector('#txt_2_value1');
    if (input2) {
      input2.value = config.query2;
      input2.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  const author = document.querySelector('#au_1_value1');
  if (author && config.author) {
    author.value = config.author;
    author.dispatchEvent(new Event('input', { bubbles: true }));
  }

  const journal = document.querySelector('#magazine_value1');
  if (journal && config.journal) {
    journal.value = config.journal;
    journal.dispatchEvent(new Event('input', { bubbles: true }));
  }

  if (config.startYear) setValue(document.querySelector('#startYear'), config.startYear);
  if (config.endYear) setValue(document.querySelector('#endYear'), config.endYear);

  document.querySelector('div.search')?.click();
}"""


@dataclass(slots=True)
class DetailConcurrencyConfig:
    mode: str
    initial_concurrency: int
    max_concurrency: int
    min_delay_ms: int
    max_delay_ms: int
    success_to_three: int = 5
    success_to_four: int = 13
    max_retries: int = 2
    max_recoveries: int = 2
    cooldown_min_ms: int = 20000
    cooldown_max_ms: int = 45000


@dataclass(slots=True)
class DetailJob:
    index: int
    item: dict[str, Any]
    attempts: int = 0


async def parse_results_from_page(page) -> dict[str, Any]:
    parsed = await page.evaluate(PARSE_RESULTS_JS)
    if not parsed["items"] and "条结果" not in await page.text_content("body"):
        raise CnkiError("page_not_supported", "The current page is not a CNKI results page.", page_url=page.url)
    return parsed


async def _apply_default_total_chinese(page) -> None:
    # CNKI remembers previous language mode (Chinese/Foreign). For predictable
    # default search behavior, force CROSSDB + Chinese before entering queries.
    await page.evaluate(
        """(config) => {
            const pickVisible = (nodes) => {
              const isVisible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return !!el.offsetParent
                  && rect.width > 0
                  && rect.height > 0
                  && style.display !== 'none'
                  && style.visibility !== 'hidden';
              };
              return nodes.find(isVisible) || nodes[0] || null;
            };

            const total = pickVisible(Array.from(document.querySelectorAll(config.totalSelector)));
            if (total) total.click();

            const chinese = pickVisible(Array.from(document.querySelectorAll(config.chineseSelector)));
            if (chinese) chinese.click();

            const rlang = document.querySelector('#rlang');
            if (rlang) rlang.value = 'CHINESE';
        }""",
        {"totalSelector": TOTAL_SCOPE_SELECTOR, "chineseSelector": CHINESE_SWITCH_SELECTOR},
    )
    await page.wait_for_timeout(300)


async def _set_visible_search_input(page, query: str) -> None:
    input_box = page.locator("input.search-input:visible").first
    await input_box.fill("")
    await input_box.fill(query)
    current = (await input_box.input_value()).strip()
    if current != query.strip():
        # Some CNKI states delay input binding. Retry with keyboard overwrite.
        await input_box.focus()
        await page.keyboard.press("Control+A")
        await page.keyboard.type(query)
        current = (await input_box.input_value()).strip()
        if current != query.strip():
            raise CnkiError("not_found", "Unable to set the CNKI search input value.", page_url=page.url)

    # Keep DOM attributes and jQuery listeners in sync with the visible value.
    await page.evaluate(
        """(q) => {
            const el = document.querySelector('input.search-input');
            if (!el) return;
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            setter?.call(el, q);
            el.setAttribute('value', q);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        query,
    )
    await page.wait_for_timeout(300)


async def _current_conditions_text(page) -> str:
    locator = page.locator(".search-his-tip .conditions").first
    if await locator.count() == 0:
        return ""
    return (await locator.inner_text()).strip()


async def _current_first_result_title(page) -> str:
    locator = page.locator(".result-table-list tbody tr td.name a.fz14").first
    if await locator.count() == 0:
        return ""
    return (await locator.inner_text()).strip()


async def _current_results_state(page) -> dict[str, str]:
    return await page.evaluate(
        """() => ({
            mark: (document.querySelector('.countPageMark')?.innerText || '').trim(),
            total: (document.querySelector('.pagerTitleCell')?.innerText || '').trim(),
            firstTitle: (document.querySelector('.result-table-list tbody tr td.name a.fz14')?.innerText || '').trim(),
        })"""
    )


async def _clear_dialog_overlays(page) -> None:
    await page.evaluate(
        """() => {
            for (const el of document.querySelectorAll('.layui-layer-btn0,.layui-layer-close')) {
                if (el instanceof HTMLElement) el.click();
            }
            for (const el of document.querySelectorAll('.layui-layer-shade,.layui-layer')) {
                if (!(el instanceof HTMLElement)) continue;
                el.style.pointerEvents = 'none';
                el.style.display = 'none';
            }
        }"""
    )
    await page.wait_for_timeout(100)


async def _submit_search(page, query: str) -> None:
    async def _submit_once() -> str | None:
        previous_conditions = await _current_conditions_text(page)
        previous_first_title = await _current_first_result_title(page)
        dialog_task = asyncio.create_task(page.wait_for_event("dialog"))
        conditions_task = asyncio.create_task(
            page.wait_for_function(
                """({query, previousConditions, previousTitle}) => {
                    const conditions = (document.querySelector('.search-his-tip .conditions')?.innerText || '').trim();
                    const title = (document.querySelector('.result-table-list tbody tr td.name a.fz14')?.innerText || '').trim();
                    const queryMatched = conditions.includes(query);
                    if (!queryMatched) return false;
                    if (!previousConditions) return true;
                    if (conditions !== previousConditions) return true;
                    if (title && title !== previousTitle) return true;
                    return false;
                }""",
                arg={
                    "query": query.strip(),
                    "previousConditions": previous_conditions,
                    "previousTitle": previous_first_title,
                },
                timeout=8000,
            )
        )
        try:
            await _clear_dialog_overlays(page)
            await page.locator("input.search-btn:visible").first.click()

            done, pending = await asyncio.wait(
                {dialog_task, conditions_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            finished = next(iter(done))
            if finished is dialog_task:
                dialog = finished.result()
                message = dialog.message
                await dialog.accept()
                await _clear_dialog_overlays(page)
                return message

            # No dialog appeared and the query/result signature changed.
            finished.result()

            await page.wait_for_timeout(150)
            return None
        finally:
            if not dialog_task.done():
                dialog_task.cancel()
            if not conditions_task.done():
                conditions_task.cancel()

    current_conditions = await _current_conditions_text(page)
    if query.strip() and query.strip() in current_conditions:
        return

    for _ in range(3):
        await _clear_dialog_overlays(page)
        await _set_visible_search_input(page, query)
        dialog_message = await _submit_once()
        if not dialog_message:
            return

    raise CnkiError(
        "not_found",
        "CNKI blocked search submission with dialog: 请输入检索词",
        page_url=page.url,
    )


async def search(args) -> dict[str, Any]:
    async with ChromeSession(args.cdp_url) as session:
        page = await session.get_or_open_page(SEARCH_URL)
        await session.goto(page, SEARCH_URL)
        await session.ensure_selector(page, "input.search-input")
        await session.require_no_captcha(page)
        await _apply_default_total_chinese(page)
        await _submit_search(page, args.query)
        await session.ensure_text(page, "条结果")
        await session.require_no_captcha(page)
        parsed = await parse_results_from_page(page)
        return ok(f'Searched CNKI for "{args.query}".', parsed, page_url=page.url)


def _collect_result_items(
    parsed: dict[str, Any],
    seen_keys: set[str],
    collected: list[dict[str, Any]],
) -> None:
    for item in parsed.get("items", []):
        key = (item.get("url") or item.get("title") or "").strip()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        collected.append(dict(item))


def _collect_result_items_with_page(
    parsed: dict[str, Any],
    seen_keys: set[str],
    collected: list[dict[str, Any]],
    *,
    source_page: int,
) -> None:
    for item in parsed.get("items", []):
        key = (item.get("url") or item.get("title") or "").strip()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        record = dict(item)
        record["sourcePage"] = source_page
        record["sourcePosition"] = item.get("number")
        record["selectionId"] = len(collected) + 1
        collected.append(record)


def _normalize_degree(database: str) -> str | None:
    text = (database or "").strip()
    if "博士" in text or "CDFD" in text:
        return "博士"
    if "硕士" in text or "CMFD" in text:
        return "硕士"
    return None


def _collect_thesis_items(
    parsed: dict[str, Any],
    degree_mode: str,
    seen_keys: set[str],
    collected: list[dict[str, Any]],
) -> None:
    allowed = THESIS_ALLOWED_DEGREES[degree_mode]
    for item in parsed.get("items", []):
        degree = _normalize_degree(item.get("database", ""))
        if not degree or degree not in allowed:
            continue
        key = (item.get("url") or item.get("title") or "").strip()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        record = dict(item)
        record["degree"] = degree
        collected.append(record)


async def _apply_thesis_scope(page) -> None:
    # Prefer the visible dissertation scope item. Fallback to first matching node.
    clicked = await page.evaluate(
        """(selector) => {
            const nodes = Array.from(document.querySelectorAll(selector));
            if (!nodes.length) return false;
            const isVisible = (el) => {
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return !!el.offsetParent
                && rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const target = nodes.find(isVisible) || nodes[0];
            target.click();
            return true;
        }""",
        THESIS_SCOPE_SELECTOR,
    )
    if not clicked:
        raise CnkiError("not_found", "CNKI dissertation filter was not found.", page_url=page.url)


async def _wait_for_results_page_change(page, previous_state: dict[str, str] | None) -> None:
    try:
        await page.wait_for_function(
            """(prev) => {
                const mark = document.querySelector('.countPageMark')?.innerText || '';
                const total = document.querySelector('.pagerTitleCell')?.innerText || '';
                const firstTitle = document.querySelector('.result-table-list tbody tr td.name a.fz14')?.innerText || '';
                if (mark && mark !== (prev.mark || '')) return true;
                if (total && total !== (prev.total || '')) return true;
                if (firstTitle && firstTitle !== (prev.firstTitle || '')) return true;
                return false;
            }""",
            arg=previous_state or {"mark": "", "total": "", "firstTitle": ""},
            timeout=15000,
        )
    except Exception:  # noqa: BLE001
        pass


async def _apply_sort(page, sort_by: str) -> None:
    previous_state = await _current_results_state(page)
    await page.click(f"#orderList li#{SORT_ID_MAP[sort_by]}")
    await _wait_for_results_page_change(page, previous_state)


async def _has_next_results_page(page, current_page: int) -> bool:
    return bool(
        await page.evaluate(
            """(nextPage) => {
                const normalize = (value) => (value || '').replace(/\\s+/g, '').trim();
                const isVisible = (el) => {
                    if (!(el instanceof HTMLElement)) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden';
                };
                const inPagingRegion = (el) => {
                    const chain = [el, el.parentElement, el.parentElement?.parentElement]
                        .filter(Boolean)
                        .map((node) => `${node.id || ''} ${node.className || ''}`.toLowerCase())
                        .join(' ');
                    return /page|pager|pagination|countpage/i.test(chain);
                };

                return Array.from(document.querySelectorAll('a,button,li,span'))
                    .some((el) => isVisible(el) && inPagingRegion(el) && (
                        normalize(el.innerText) === nextPage || normalize(el.innerText) === '下一页'
                    ));
            }""",
            str(current_page + 1),
        )
    )


async def _move_to_next_results_page(page, current_page: int) -> bool:
    if not await _has_next_results_page(page, current_page):
        return False

    previous_state = await _current_results_state(page)
    moved = False
    try:
        await page.get_by_text(str(current_page + 1), exact=True).first.click()
        moved = True
    except Exception:  # noqa: BLE001
        pass

    if not moved:
        try:
            await page.get_by_text("下一页").first.click()
            moved = True
        except Exception:  # noqa: BLE001
            moved = False

    if moved:
        await _wait_for_results_page_change(page, previous_state)
    return moved


def _build_detail_config(args) -> DetailConcurrencyConfig:
    mode = getattr(args, "concurrency_mode", "adaptive") or "adaptive"
    min_delay_ms = max(0, int(getattr(args, "min_delay_ms", 300) or 0))
    max_delay_ms = max(min_delay_ms, int(getattr(args, "max_delay_ms", 1200) or min_delay_ms))
    if mode == "serial":
        return DetailConcurrencyConfig(
            mode="serial",
            initial_concurrency=1,
            max_concurrency=1,
            min_delay_ms=min_delay_ms,
            max_delay_ms=max_delay_ms,
        )

    max_concurrency = max(1, min(4, int(getattr(args, "max_concurrency", 4) or 4)))
    return DetailConcurrencyConfig(
        mode="adaptive",
        initial_concurrency=min(2, max_concurrency),
        max_concurrency=max_concurrency,
        min_delay_ms=min_delay_ms,
        max_delay_ms=max_delay_ms,
    )


def _make_detail_error(code: str, message: str, *, page_url: str | None = None, detail: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if page_url:
        payload["page_url"] = page_url
    if detail is not None:
        payload["detail"] = detail
    return payload


def _merge_detail_into_record(record: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    merged = dict(record)
    merged["abstract"] = detail.get("abstract", "")
    merged["keywords"] = detail.get("keywords", [])
    merged["fund"] = detail.get("fund", "")
    merged["classification"] = detail.get("classification", "")
    merged["affiliations"] = detail.get("affiliations", [])
    merged["detailAuthors"] = detail.get("authors", [])
    merged["journalDetail"] = detail.get("journal", "")
    merged["pubInfo"] = detail.get("pubInfo", "")
    merged["detail"] = detail
    return merged


def _build_advanced_payload(args) -> dict[str, Any]:
    return {
        "query": args.query,
        "fieldType": args.field_type,
        "query2": args.query2 or "",
        "fieldType2": args.field_type2,
        "rowLogic": args.row_logic,
        "sourceTypes": args.source or [],
        "startYear": args.start_year or "",
        "endYear": args.end_year or "",
        "author": args.author or "",
        "journal": args.journal or "",
    }


async def _run_advanced_search(session: ChromeSession, page, args) -> tuple[dict[str, Any], dict[str, Any]]:
    await session.goto(page, ADVANCED_SEARCH_URL)
    await session.ensure_selector(page, "#txt_1_value1")
    await session.require_no_captcha(page)

    payload = _build_advanced_payload(args)
    await page.evaluate(ADVANCED_SEARCH_JS, payload)
    await session.ensure_text(page, "条结果")
    await session.require_no_captcha(page)
    if getattr(args, "sort_by", None):
        await _apply_sort(page, args.sort_by)
        await session.require_no_captcha(page)

    parsed = await parse_results_from_page(page)
    parsed["filters"] = payload
    return parsed, payload


def _safe_name(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "cnki-review"


def _resolve_output_dir(raw: str | None) -> Path:
    base = Path(raw or "outputs")
    if not base.is_absolute():
        base = Path.cwd() / base
    base.mkdir(parents=True, exist_ok=True)
    return base


def _render_review_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# CNKI 检索工作流结果",
        "",
        f"- 查询1：`{data['filters']['query']}`",
        f"- 查询2：`{data['filters']['query2'] or '(无)'}`",
        f"- 逻辑：`{data['filters']['rowLogic']}`",
        f"- 时间范围：`{data['filters']['startYear'] or '不限'} - {data['filters']['endYear'] or '不限'}`",
        f"- 命中总数：`{data['total']}`",
        f"- 页数：已抓取 `{data['pagesScanned']}` 页，共约 `{data['collected']}` 篇",
        f"- 详情并发：`{data['finalConcurrency']}`",
        "",
        "## 结果",
        "",
    ]

    for item in data["items"]:
        authors = "；".join(item.get("authors") or []) or "未知"
        keywords = "；".join(item.get("keywords") or []) or "无"
        journal = item.get("journal") or item.get("journalDetail") or "未知"
        abstract = (item.get("abstract") or "").strip() or "无摘要"
        lines.extend(
            [
                f"### [{item['selectionId']}] {item.get('title', '')}",
                "",
                f"- 来源页：第 `{item.get('sourcePage', '?')}` 页，第 `{item.get('sourcePosition', '?')}` 条",
                f"- 日期：`{item.get('date', '') or item.get('pubInfo', '') or '未知'}`",
                f"- 期刊：`{journal}`",
                f"- 作者：`{authors}`",
                f"- 下载数：`{item.get('downloads', '') or '未知'}`",
                f"- 关键词：`{keywords}`",
                f"- 摘要：{abstract}",
                f"- 链接：{item.get('url', '')}",
                "",
            ]
        )

    if data.get("detailErrors"):
        lines.extend(["## 详情失败", ""])
        for err in data["detailErrors"]:
            lines.append(f"- `{err.get('title', '未知')}`: {err.get('message', '')}")

    return "\n".join(lines)


def _write_review_outputs(data: dict[str, Any], output_dir: str | None) -> dict[str, str]:
    target_dir = _resolve_output_dir(output_dir)
    query_bits = [data["filters"]["query"]]
    if data["filters"].get("query2"):
        query_bits.append(data["filters"]["query2"])
    prefix = _safe_name("-".join(query_bits))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = target_dir / f"{prefix}-{stamp}.json"
    md_path = target_dir / f"{prefix}-{stamp}.md"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_review_markdown(data), encoding="utf-8")
    return {"json": str(json_path.resolve()), "markdown": str(md_path.resolve())}


async def _sleep_with_jitter(config: DetailConcurrencyConfig) -> None:
    if config.max_delay_ms <= 0:
        return
    delay_ms = random.randint(config.min_delay_ms, config.max_delay_ms)
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)


async def _collect_single_detail(
    session: ChromeSession,
    detail_page,
    job: DetailJob,
    config: DetailConcurrencyConfig,
) -> dict[str, Any]:
    record = dict(job.item)
    detail_url = (record.get("url") or "").strip()
    if not detail_url:
        error = _make_detail_error("not_found", "Result item has no detail URL.")
        return {"kind": "error", "job": job, "record": record, "error": error, "retryable": False}

    await _sleep_with_jitter(config)
    await session.dismiss_known_overlays(detail_page)

    try:
        await ensure_detail_page(session, detail_page, detail_url)
        await session.dismiss_known_overlays(detail_page)
        risk = await session.detect_risk(detail_page)
        if risk:
            if risk["code"] == "captcha":
                return {"kind": "captcha", "job": job, "record": record, "risk": risk}
            return {"kind": "risk", "job": job, "record": record, "risk": risk}
        detail = await extract_detail_from_page(detail_page)
        return {"kind": "success", "job": job, "record": _merge_detail_into_record(record, detail)}
    except CnkiError as exc:
        risk = await session.detect_risk(detail_page)
        if risk:
            if risk["code"] == "captcha":
                return {"kind": "captcha", "job": job, "record": record, "risk": risk}
            return {"kind": "risk", "job": job, "record": record, "risk": risk}
        error = _make_detail_error(exc.code, exc.message, page_url=exc.page_url)
        return {
            "kind": "error",
            "job": job,
            "record": record,
            "error": error,
            "retryable": exc.code in DETAIL_RETRYABLE_ERROR_CODES,
        }
    except Exception as exc:  # noqa: BLE001
        risk = await session.detect_risk(detail_page)
        if risk:
            if risk["code"] == "captcha":
                return {"kind": "captcha", "job": job, "record": record, "risk": risk}
            return {"kind": "risk", "job": job, "record": record, "risk": risk}
        error = _make_detail_error("unexpected_error", str(exc), page_url=detail_page.url)
        return {"kind": "error", "job": job, "record": record, "error": error, "retryable": True}


async def _enrich_items_with_detail(
    session: ChromeSession,
    items: list[dict[str, Any]],
    args,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    config = _build_detail_config(args)
    assert session.context is not None

    detail_pages = [await session.context.new_page() for _ in range(config.max_concurrency)]
    records = [dict(item) for item in items]
    errors: list[dict[str, Any]] = []
    stats = {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "retried": 0,
        "captchaHits": 0,
        "cooldowns": 0,
    }
    risk_events: list[dict[str, Any]] = []
    pending = [DetailJob(index=index, item=dict(item)) for index, item in enumerate(items)]
    current_concurrency = config.initial_concurrency
    consecutive_successes = 0
    recoveries = 0
    blocked_state: dict[str, Any] | None = None
    stopped_early = False

    try:
        while pending:
            batch_size = 1 if config.mode == "serial" else min(current_concurrency, len(pending))
            batch_jobs = [pending.pop(0) for _ in range(batch_size)]
            outcomes = await asyncio.gather(
                *[
                    _collect_single_detail(session, detail_pages[index], job, config)
                    for index, job in enumerate(batch_jobs)
                ]
            )

            risk_in_batch = False
            for outcome in outcomes:
                stats["attempted"] += 1
                job = outcome["job"]
                title = job.item.get("title", "")
                url = job.item.get("url", "")

                if outcome["kind"] == "success":
                    records[job.index] = outcome["record"]
                    stats["succeeded"] += 1
                    consecutive_successes += 1
                    continue

                consecutive_successes = 0

                if outcome["kind"] == "captcha":
                    stats["captchaHits"] += 1
                    blocked_state = {
                        "message": "CNKI showed a slider captcha during batch detail collection. Solve it in Chrome, then rerun the command.",
                        "code": "captcha",
                    }
                    error = _make_detail_error(
                        "captcha",
                        "CNKI showed a slider captcha during batch detail collection.",
                        page_url=outcome["risk"].get("page_url"),
                        detail=outcome["risk"].get("detail"),
                    )
                    records[job.index]["detailError"] = error
                    errors.append({"title": title, "url": url, **error})
                    risk_events.append(
                        {
                            "type": "blocked",
                            "code": "captcha",
                            "title": title,
                            "attempt": job.attempts + 1,
                        }
                    )
                    break

                if outcome["kind"] == "risk":
                    risk_in_batch = True
                    risk = outcome["risk"]
                    risk_events.append(
                        {
                            "type": "risk",
                            "code": risk["code"],
                            "title": title,
                            "attempt": job.attempts + 1,
                        }
                    )
                    if job.attempts < config.max_retries:
                        stats["retried"] += 1
                        pending.append(DetailJob(index=job.index, item=job.item, attempts=job.attempts + 1))
                    else:
                        error = _make_detail_error(
                            risk["code"],
                            risk["message"],
                            page_url=risk.get("page_url"),
                            detail=risk.get("detail"),
                        )
                        records[job.index]["detailError"] = error
                        errors.append({"title": title, "url": url, **error})
                        stats["failed"] += 1
                    continue

                error = outcome["error"]
                if outcome["retryable"] and job.attempts < config.max_retries:
                    stats["retried"] += 1
                    pending.append(DetailJob(index=job.index, item=job.item, attempts=job.attempts + 1))
                    if error["code"] in DETAIL_RETRYABLE_ERROR_CODES:
                        risk_in_batch = True
                        risk_events.append(
                            {
                                "type": "retry",
                                "code": error["code"],
                                "title": title,
                                "attempt": job.attempts + 1,
                            }
                        )
                else:
                    records[job.index]["detailError"] = error
                    errors.append({"title": title, "url": url, **error})
                    stats["failed"] += 1

            if blocked_state:
                stopped_early = True
                break

            if config.mode == "adaptive":
                target_concurrency = current_concurrency
                if risk_in_batch:
                    if current_concurrency != 1:
                        risk_events.append(
                            {
                                "type": "downgrade",
                                "from": current_concurrency,
                                "to": 1,
                            }
                        )
                    target_concurrency = 1
                    if pending:
                        if recoveries < config.max_recoveries:
                            cooldown_ms = random.randint(config.cooldown_min_ms, config.cooldown_max_ms)
                            stats["cooldowns"] += 1
                            recoveries += 1
                            risk_events.append(
                                {
                                    "type": "cooldown",
                                    "milliseconds": cooldown_ms,
                                    "recovery": recoveries,
                                }
                            )
                            current_concurrency = target_concurrency
                            await asyncio.sleep(cooldown_ms / 1000)
                        else:
                            stopped_early = True
                            risk_events.append({"type": "stop", "reason": "max_recoveries_exceeded"})
                            current_concurrency = target_concurrency
                            break
                else:
                    if consecutive_successes >= config.success_to_four:
                        target_concurrency = min(4, config.max_concurrency)
                    elif consecutive_successes >= config.success_to_three:
                        target_concurrency = min(3, config.max_concurrency)
                    if target_concurrency > current_concurrency:
                        risk_events.append(
                            {
                                "type": "scale_up",
                                "from": current_concurrency,
                                "to": target_concurrency,
                            }
                        )
                    current_concurrency = target_concurrency

        meta = {
            "concurrencyMode": config.mode,
            "initialConcurrency": config.initial_concurrency,
            "maxConcurrency": config.max_concurrency,
            "finalConcurrency": current_concurrency,
            "detailStats": stats,
            "riskEvents": risk_events,
            "blocked": blocked_state is not None,
            "blockedMessage": blocked_state["message"] if blocked_state else "",
            "stoppedEarly": stopped_early and blocked_state is None,
        }
        return records, errors, meta
    finally:
        for detail_page in detail_pages:
            await detail_page.close()


async def thesis_search(args) -> dict[str, Any]:
    requested = max(1, int(args.count or 20))
    max_pages = max(1, int(args.max_pages or 20))
    degree_mode = args.degree or "both"

    async with ChromeSession(args.cdp_url) as session:
        page = await session.get_or_open_page(SEARCH_URL)
        await session.goto(page, SEARCH_URL)
        await session.ensure_selector(page, "input.search-input")
        await session.require_no_captcha(page)
        await _apply_default_total_chinese(page)
        await _submit_search(page, args.query)
        await session.ensure_text(page, "条结果")
        await session.require_no_captcha(page)

        previous_mark = await page.locator(".countPageMark").text_content()
        await _apply_thesis_scope(page)
        await _wait_for_results_page_change(page, previous_mark)

        parsed = await parse_results_from_page(page)
        seen: set[str] = set()
        collected: list[dict[str, Any]] = []
        _collect_thesis_items(parsed, degree_mode, seen, collected)

        current_page = 1
        while len(collected) < requested and current_page < max_pages:
            if not await _move_to_next_results_page(page, current_page):
                break

            await session.require_no_captcha(page)
            parsed = await parse_results_from_page(page)
            _collect_thesis_items(parsed, degree_mode, seen, collected)
            current_page += 1

        items = collected[:requested]
        data = {
            "query": args.query,
            "degree": degree_mode,
            "requested": requested,
            "collected": len(items),
            "pagesScanned": current_page,
            "summary": {
                "doctoral": sum(1 for item in items if item.get("degree") == "博士"),
                "master": sum(1 for item in items if item.get("degree") == "硕士"),
            },
            "items": items,
        }
        return ok(
            f'Collected {len(items)} thesis record(s) for "{args.query}" in {degree_mode} mode.',
            data,
            page_url=page.url,
        )


async def collect_details(args) -> dict[str, Any]:
    requested = max(1, int(args.count or 10))
    max_pages = max(1, int(args.max_pages or 20))
    scope = args.scope or "papers"
    degree_mode = args.degree or "both"

    async with ChromeSession(args.cdp_url) as session:
        page = await session.get_or_open_page(SEARCH_URL)
        await session.goto(page, SEARCH_URL)
        await session.ensure_selector(page, "input.search-input")
        await session.require_no_captcha(page)
        await _apply_default_total_chinese(page)
        await _submit_search(page, args.query)
        await session.ensure_text(page, "条结果")
        await session.require_no_captcha(page)

        if scope == "theses":
            previous_mark = await page.locator(".countPageMark").text_content()
            await _apply_thesis_scope(page)
            await _wait_for_results_page_change(page, previous_mark)

        parsed = await parse_results_from_page(page)
        seen: set[str] = set()
        collected: list[dict[str, Any]] = []
        if scope == "theses":
            _collect_thesis_items(parsed, degree_mode, seen, collected)
        else:
            _collect_result_items(parsed, seen, collected)

        current_page = 1
        while len(collected) < requested and current_page < max_pages:
            if not await _move_to_next_results_page(page, current_page):
                break
            await session.require_no_captcha(page)
            parsed = await parse_results_from_page(page)
            if scope == "theses":
                _collect_thesis_items(parsed, degree_mode, seen, collected)
            else:
                _collect_result_items(parsed, seen, collected)
            current_page += 1

        source_items = collected[:requested]
        enriched_items, detail_errors, detail_meta = await _enrich_items_with_detail(session, source_items, args)
        data = {
            "query": args.query,
            "scope": scope,
            "degree": degree_mode if scope == "theses" else None,
            "requested": requested,
            "collected": len(enriched_items),
            "pagesScanned": current_page,
            "detailErrors": detail_errors,
            "concurrencyMode": detail_meta["concurrencyMode"],
            "initialConcurrency": detail_meta["initialConcurrency"],
            "maxConcurrency": detail_meta["maxConcurrency"],
            "finalConcurrency": detail_meta["finalConcurrency"],
            "detailStats": detail_meta["detailStats"],
            "riskEvents": detail_meta["riskEvents"],
            "items": enriched_items,
        }
        if scope == "theses":
            data["summary"] = {
                "doctoral": sum(1 for item in enriched_items if item.get("degree") == "博士"),
                "master": sum(1 for item in enriched_items if item.get("degree") == "硕士"),
            }
            message = f'Collected {len(enriched_items)} thesis detail record(s) for "{args.query}" in {degree_mode} mode.'
        else:
            message = f'Collected {len(enriched_items)} paper detail record(s) for "{args.query}".'
        if detail_meta["blocked"]:
            return blocked(detail_meta["blockedMessage"], data, page_url=page.url)
        if detail_meta["stoppedEarly"]:
            return partial(
                f'{message} CNKI throttling forced an early stop before every queued detail page could be collected.',
                data,
                page_url=page.url,
            )
        return ok(message, data, page_url=page.url)


async def advanced_search(args) -> dict[str, Any]:
    async with ChromeSession(args.cdp_url) as session:
        page = await session.get_or_open_page(ADVANCED_SEARCH_URL)
        parsed, payload = await _run_advanced_search(session, page, args)
        return ok(f'Ran advanced CNKI search for "{args.query}".', parsed, page_url=page.url)


async def review_workflow(args) -> dict[str, Any]:
    pages_requested = max(1, int(getattr(args, "pages", 2) or 2))

    async with ChromeSession(args.cdp_url) as session:
        page = await session.get_or_open_page(ADVANCED_SEARCH_URL)
        parsed, payload = await _run_advanced_search(session, page, args)
        total_hits = parsed.get("total", "")

        seen: set[str] = set()
        collected: list[dict[str, Any]] = []
        _collect_result_items_with_page(parsed, seen, collected, source_page=1)
        page_summaries = [
            {
                "pageNumber": 1,
                "pageMark": parsed.get("page", ""),
                "items": len(parsed.get("items", [])),
                "total": total_hits,
            }
        ]

        current_page = 1
        while current_page < pages_requested:
            if not await _move_to_next_results_page(page, current_page):
                break
            await session.require_no_captcha(page)
            parsed = await parse_results_from_page(page)
            current_page += 1
            page_summaries.append(
                {"pageNumber": current_page, "pageMark": parsed.get("page", ""), "items": len(parsed.get("items", []))}
            )
            _collect_result_items_with_page(parsed, seen, collected, source_page=current_page)

        enriched_items, detail_errors, detail_meta = await _enrich_items_with_detail(session, collected, args)
        for index, item in enumerate(enriched_items, start=1):
            item["selectionId"] = index
            item["reviewAbstract"] = (item.get("abstract") or "").strip()[:220]

        data = {
            "workflow": "review-workflow",
            "query": args.query,
            "filters": payload,
            "total": total_hits,
            "pagesRequested": pages_requested,
            "pagesScanned": current_page,
            "pageSummaries": page_summaries,
            "collected": len(enriched_items),
            "detailErrors": detail_errors,
            "concurrencyMode": detail_meta["concurrencyMode"],
            "initialConcurrency": detail_meta["initialConcurrency"],
            "maxConcurrency": detail_meta["maxConcurrency"],
            "finalConcurrency": detail_meta["finalConcurrency"],
            "detailStats": detail_meta["detailStats"],
            "riskEvents": detail_meta["riskEvents"],
            "items": enriched_items,
        }
        output_files = _write_review_outputs(data, getattr(args, "output_dir", None))
        data["outputFiles"] = output_files

        message = f'Collected {len(enriched_items)} enriched record(s) across the first {current_page} page(s).'
        if detail_meta["blocked"]:
            return blocked(detail_meta["blockedMessage"], data, page_url=page.url)
        if detail_meta["stoppedEarly"]:
            return partial(
                f"{message} CNKI throttling forced an early stop before every queued detail page could be collected.",
                data,
                page_url=page.url,
            )
        return ok(message, data, page_url=page.url)


async def review_fixed(args) -> dict[str, Any]:
    """Project-local fixed review workflow with stable defaults."""

    args.pages = 2
    args.concurrency_mode = "adaptive"
    args.max_concurrency = 3
    args.min_delay_ms = 400
    args.max_delay_ms = 1200

    result = await review_workflow(args)
    data = result.get("data")
    if isinstance(data, dict):
        data["workflow"] = "review-fixed"
        data["sortBy"] = getattr(args, "sort_by", None)
        output_files = data.get("outputFiles") or {}
        if isinstance(output_files, dict):
            data["reviewJsonPath"] = output_files.get("json", "")
            data["reviewMarkdownPath"] = output_files.get("markdown", "")
        data["fixedConfig"] = {
            "initialPages": 2,
            "expandStepPages": 2,
            "concurrencyMode": "adaptive",
            "maxConcurrency": 3,
            "minDelayMs": 400,
            "maxDelayMs": 1200,
            "downloadsIncluded": False,
        }
    return result


def _load_review_bundle(review_file: str) -> dict[str, Any]:
    review_path = Path(review_file).expanduser().resolve()
    if not review_path.exists():
        raise CnkiError("not_found", f"Review file not found: {review_path}")

    data = json.loads(review_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "filters" not in data:
        raise CnkiError("invalid_input", f"Review file is missing required filters: {review_path}")
    data["reviewFile"] = str(review_path)
    return data


async def review_expand(args) -> dict[str, Any]:
    """Expand a fixed review bundle by rerunning the same search with more pages."""

    bundle = _load_review_bundle(args.review_file)
    filters = bundle.get("filters") or {}
    pages_scanned = max(1, int(bundle.get("pagesScanned", 2) or 2))
    additional_pages = max(1, int(getattr(args, "additional_pages", 2) or 2))
    target_pages = pages_scanned + additional_pages

    rerun_args = SimpleNamespace(
        cdp_url=args.cdp_url,
        query=filters.get("query", ""),
        field_type=filters.get("fieldType", "SU"),
        query2=filters.get("query2"),
        field_type2=filters.get("fieldType2", "KY"),
        row_logic=filters.get("rowLogic", "AND"),
        source=filters.get("sourceTypes") or [],
        start_year=filters.get("startYear"),
        end_year=filters.get("endYear"),
        author=filters.get("author"),
        journal=filters.get("journal"),
        sort_by=bundle.get("sortBy"),
        output_dir=getattr(args, "output_dir", None),
        pages=target_pages,
        concurrency_mode="adaptive",
        max_concurrency=3,
        min_delay_ms=400,
        max_delay_ms=1200,
    )
    result = await review_workflow(rerun_args)
    data = result.get("data")
    if isinstance(data, dict):
        data["workflow"] = "review-expand"
        data["sortBy"] = bundle.get("sortBy")
        data["expandedFrom"] = bundle.get("reviewFile", "")
        data["reviewJsonPath"] = (data.get("outputFiles") or {}).get("json", "")
        data["reviewMarkdownPath"] = (data.get("outputFiles") or {}).get("markdown", "")
        data["fixedConfig"] = {
            "initialPages": 2,
            "expandStepPages": additional_pages,
            "pagesBeforeExpand": pages_scanned,
            "pagesAfterExpand": data.get("pagesScanned", target_pages),
            "concurrencyMode": "adaptive",
            "maxConcurrency": 3,
            "minDelayMs": 400,
            "maxDelayMs": 1200,
            "downloadsIncluded": False,
        }
    return result


async def parse_results(args) -> dict[str, Any]:
    async with ChromeSession(args.cdp_url) as session:
        page = await session.get_or_open_page(SEARCH_URL)
        await session.require_no_captcha(page)
        parsed = await parse_results_from_page(page)
        return ok("Parsed the current CNKI results page.", parsed, page_url=page.url)


async def navigate_pages(args) -> dict[str, Any]:
    async with ChromeSession(args.cdp_url) as session:
        page = await session.get_or_open_page(SEARCH_URL)
        await session.require_no_captcha(page)
        previous_mark = await page.locator(".countPageMark").text_content()

        if args.sort_by:
            id_map = {
                "relevance": "FFD",
                "date": "PT",
                "citations": "CF",
                "downloads": "DFR",
                "comprehensive": "ZH",
            }
            sort_id = id_map[args.sort_by]
            await page.click(f"#orderList li#{sort_id}")
        elif args.action == "next":
            await page.get_by_text("下一页").first.click()
        elif args.action == "previous":
            await page.get_by_text("上一页").first.click()
        elif args.page:
            await page.get_by_text(str(args.page), exact=True).first.click()
        else:
            raise CnkiError("not_found", "Provide --sort-by, --action, or --page.", page_url=page.url)

        try:
            await page.wait_for_function(
                """(prev) => {
                    const mark = document.querySelector('.countPageMark')?.innerText || '';
                    return Boolean(mark) && mark !== prev;
                }""",
                arg=previous_mark or "",
                timeout=15000,
            )
        except Exception:  # noqa: BLE001
            # CNKI occasionally updates list content without refreshing the page marker in time.
            # Continue and parse the page snapshot instead of failing hard.
            pass
        await session.require_no_captcha(page)
        parsed = await parse_results_from_page(page)
        message = "Updated CNKI page ordering." if args.sort_by else "Navigated the CNKI results page."
        return ok(message, parsed, page_url=page.url)
