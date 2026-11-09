"""Turn messy HTML into text an analyst (or an LLM) can actually read.

The original n8n version passed the raw page text to the model: menus,
cookie banners, footers and legal notices included. This module keeps the
content and drops the chrome, in three passes:

1. **Structural removal** — scripts, styles, nav, header, footer, forms and
   any element whose id/class looks like chrome (cookie, menu, newsletter…).
2. **Light structure** — headings become ``#`` lines and list items ``-``
   lines, so the model still sees how the page is organised.
3. **Cross-page de-duplication** — a line that appears on most crawled pages
   is template (menu, footer, slogan), not content, and is removed.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

from bs4 import BeautifulSoup, Tag

from pe_origination.models import Page

DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "nav",
    "header",
    "footer",
    "form",
    "button",
    "select",
    "template",
    "canvas",
)
CHROME_HINTS = re.compile(
    r"cookie|consent|gdpr|rgpd|banner|newsletter|navbar|nav-|menu|breadcrumb|footer|"
    r"header|social|share|popup|modal|subscribe|sidebar|skip-link|lang-switch",
    re.I,
)
BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "td", "th", "blockquote", "dd", "dt", "figcaption")
WS = re.compile(r"\s+")
MIN_LINE_CHARS = 3


def _is_chrome(el: Tag) -> bool:
    attrs = " ".join(
        [
            el.get("id") or "",
            " ".join(el.get("class") or []),
            el.get("role") or "",
            el.get("aria-label") or "",
        ]
    )
    return bool(attrs.strip()) and bool(CHROME_HINTS.search(attrs))


def html_to_page(html: str, url: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    title = WS.sub(" ", soup.title.get_text()).strip() if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    description = (meta.get("content") or "").strip() if isinstance(meta, Tag) else ""

    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for el in soup.find_all(True):
        if el.parent is not None and el.name not in ("html", "body", "main") and _is_chrome(el):
            el.decompose()

    root = soup.find("main") or soup.body or soup
    lines: list[str] = []
    for el in root.find_all(BLOCK_TAGS):
        if el.find(BLOCK_TAGS):  # parent of other blocks: children will be visited
            continue
        text = WS.sub(" ", el.get_text(" ")).strip()
        if len(text) < MIN_LINE_CHARS:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            text = "#" * int(el.name[1]) + " " + text
        elif el.name == "li":
            text = "- " + text
        lines.append(text)

    if not lines:  # pages built from bare <div>s: fall back to raw text lines
        raw = root.get_text("\n")
        lines = [WS.sub(" ", s).strip() for s in raw.splitlines()]
        lines = [s for s in lines if len(s) >= MIN_LINE_CHARS]

    return Page(url=url, title=title, description=description, text="\n".join(_dedupe(lines)))


def _dedupe(lines: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for line in lines:
        key = line.lower()
        if key not in seen:
            seen.add(key)
            out.append(line)
    return out


def remove_template_lines(pages: Sequence[Page], threshold: float = 0.6) -> list[Page]:
    """Drop lines that repeat on at least ``threshold`` of the pages (min. 2 pages)."""
    if len(pages) < 2:
        return list(pages)
    counts: Counter[str] = Counter()
    for p in pages:
        counts.update({line.lower() for line in p.text.splitlines()})
    cutoff = max(2, int(threshold * len(pages) + 0.999))
    template = {line for line, n in counts.items() if n >= cutoff and not line.startswith("#")}
    return [
        p.model_copy(update={"text": "\n".join(x for x in p.text.splitlines() if x.lower() not in template)})
        for p in pages
    ]


def build_digest(pages: Sequence[Page], max_chars: int) -> str:
    """Concatenate pages into one bounded text block, home page first.

    The budget is shared fairly: each page gets an equal slice, and unused
    budget from short pages flows to the longer ones.
    """
    if not pages:
        return ""
    blocks = []
    for p in pages:
        head = f"### {p.title or p.url}\nURL: {p.url}"
        if p.description:
            head += f"\nMeta description: {p.description}"
        blocks.append((head, p.text))

    remaining = max_chars - sum(len(h) + 2 for h, _ in blocks)
    if remaining <= 0:
        return "\n\n".join(h for h, _ in blocks)[:max_chars]
    order = sorted(range(len(blocks)), key=lambda i: len(blocks[i][1]))
    allowance = [0] * len(blocks)
    for k, i in enumerate(order):
        share = remaining // (len(blocks) - k)
        allowance[i] = min(len(blocks[i][1]), share)
        remaining -= allowance[i]

    out = []
    for (head, text), cap in zip(blocks, allowance, strict=False):
        body = text if len(text) <= cap else text[:cap].rsplit("\n", 1)[0] + "\n[…truncated]"
        out.append(f"{head}\n{body}".strip())
    return "\n\n".join(out)
