"""Paper detail helpers used by the search-only CNKI workflow."""

from __future__ import annotations

from pathlib import Path

if __package__ in (None, ""):
    import sys

    sys.path.append(str(Path(__file__).resolve().parent))
    from browser import ChromeSession, CnkiError  # type: ignore
    from cnki_selectors import PAPER_SECTION_SELECTOR  # type: ignore
else:
    from .browser import ChromeSession, CnkiError
    from .cnki_selectors import PAPER_SECTION_SELECTOR


DETAIL_JS = """() => {
  const brief = document.querySelector('.brief');
  if (!brief) return null;

  const title = brief.querySelector('h1')?.innerText?.trim()
    ?.replace(/\\s*附视频\\s*$/, '')
    ?.replace(/\\s*网络首发\\s*$/, '');
  const authorH3s = brief.querySelectorAll('h3.author');
  const authors = [];
  if (authorH3s[0]) {
    authorH3s[0].querySelectorAll('a').forEach(a => {
      const raw = a.innerText || '';
      authors.push({
        name: raw.replace(/\\d+$/, '').trim(),
        affiliationNum: (raw.match(/(\\d+)$/) || [])[1] || ''
      });
    });
  }
  const affiliations = authorH3s[1]
    ? Array.from(authorH3s[1].querySelectorAll('a')).map(a => a.innerText?.trim())
    : [];
  const abstract = document.querySelector('.abstract-text')?.innerText?.trim() || '';
  const keywords = Array.from(document.querySelectorAll('p.keywords a')).map(a => a.innerText?.replace(/;$/, '').trim());
  const fund = document.querySelector('p.funds')?.innerText?.trim() || '';
  const classification = document.querySelector('.clc-code')?.innerText?.trim() || '';
  const journal = document.querySelector('.doc-top a')?.innerText?.trim() || '';
  const pubInfo = document.querySelector('.head-time')?.innerText?.trim() || '';
  return {
    title,
    authors,
    affiliations,
    abstract,
    keywords,
    fund,
    classification,
    journal,
    pubInfo,
    isOnlineFirst: !!brief.querySelector('.icon-shoufa'),
  };
}"""


async def ensure_detail_page(session: ChromeSession, page, url: str | None) -> None:
    if url:
        await session.goto(page, url)
    await session.ensure_selector(page, PAPER_SECTION_SELECTOR)
    await session.require_no_captcha(page)


async def extract_detail_from_page(page) -> dict[str, object]:
    detail = await page.evaluate(DETAIL_JS)
    if not detail:
        raise CnkiError(
            "page_not_supported",
            "The current page is not a CNKI paper detail page.",
            page_url=page.url,
        )
    return detail
