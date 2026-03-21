from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

log = logging.getLogger(__name__)

EASA_REF_PATTERN = re.compile(
    r'(?:AMC\d?|GM\d?|IR|CS|CAT|ORO|SPA|ARO|ORA|ARA|MED|FCL|ATCO|ATS|ADR|SERA)'
    r'(?:\.[A-Z]+)*(?:\.\d+)?(?:\([a-z]\))?'
)


@dataclass
class _FAQDomainInfo:
    slug: str
    title: str
    url: str


@dataclass
class _FAQCandidate:
    question: str
    url: str
    category: str = ""
    detected_refs: list[str] = field(default_factory=list)


@dataclass
class _FAQEntry:
    question: str
    answer_text: str
    url: str
    category: str = ""
    detected_refs: list[str] = field(default_factory=list)


class FAQRegulationsRootParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.domains: list[_FAQDomainInfo] = []
        self._in_link = False
        self._current_href = ""
        self._current_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            attr_dict = dict(attrs)
            href = attr_dict.get("href", "")
            if href and "faq" in href.lower():
                self._in_link = True
                self._current_href = urljoin(self.base_url, href)
                self._current_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._current_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            self._in_link = False
            text = self._current_text.strip()
            if text and self._current_href:
                slug = self._current_href.rstrip("/").rsplit("/", 1)[-1]
                self.domains.append(_FAQDomainInfo(
                    slug=slug,
                    title=text,
                    url=self._current_href,
                ))


class FAQIndexParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.candidates: list[_FAQCandidate] = []
        self._in_link = False
        self._current_href = ""
        self._current_text = ""
        self._current_category = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "a":
            href = attr_dict.get("href", "")
            if href:
                self._in_link = True
                self._current_href = urljoin(self.base_url, href)
                self._current_text = ""
        if tag in ("h2", "h3", "h4"):
            self._current_category = ""

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._current_text += data
        else:
            stripped = data.strip()
            if stripped:
                self._current_category = stripped

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            self._in_link = False
            text = self._current_text.strip()
            if text and "?" in text and len(text) > 15:
                detected = EASA_REF_PATTERN.findall(text)
                self.candidates.append(_FAQCandidate(
                    question=text,
                    url=self._current_href,
                    category=self._current_category,
                    detected_refs=detected,
                ))


class FAQDetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._text_parts: list[str] = []
        self._in_content = False
        self._depth = 0

    def reset(self) -> None:
        super().reset()
        self._text_parts = []
        self._in_content = False
        self._depth = 0

    def feed(self, data: str) -> None:
        self.reset()
        super().feed(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        css_class = attr_dict.get("class", "")
        if "field--name-body" in css_class or "faq-answer" in css_class:
            self._in_content = True
            self._depth = 0
        if self._in_content:
            self._depth += 1
            if tag in ("br", "p"):
                self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_content:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._in_content:
            self._depth -= 1
            if self._depth <= 0:
                self._in_content = False
            if tag == "p":
                self._text_parts.append("\n")

    def build(self, url: str, category: str = "") -> _FAQEntry:
        answer_text = "".join(self._text_parts).strip()
        answer_text = re.sub(r'\n{3,}', '\n\n', answer_text)
        detected = EASA_REF_PATTERN.findall(answer_text)
        return _FAQEntry(
            question="",
            answer_text=answer_text,
            url=url,
            category=category,
            detected_refs=detected,
        )
