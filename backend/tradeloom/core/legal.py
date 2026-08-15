"""Terms and privacy documents, and the versions users accepted.

The documents themselves live in ``content/legal/`` as Markdown so they can be edited without a
code change. This module knows three things about them: which version is current, what the text
is, and whether it is still the placeholder that ships with the repository.

That last point is the reason this file exists rather than a constant somewhere. **Nobody should
write your terms of service for you, least of all a language model.** The repository ships
documents that say exactly that, in the document itself, so a deployment cannot accidentally
present generated text as a binding agreement — and
:meth:`~tradeloom.core.config.Settings.validate_for_production` refuses to boot a production
process while they are still unwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Marker on the first line of a document that has not been written yet. Removing it is the act of
#: publishing: it is what tells the application that a lawyer has been near the text.
PLACEHOLDER_MARKER = "<!-- UNWRITTEN-PLACEHOLDER -->"

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "content" / "legal"

TERMS = "terms"
PRIVACY = "privacy"
DOCUMENTS = (TERMS, PRIVACY)


@dataclass(frozen=True, slots=True)
class LegalDocument:
    """One published policy."""

    slug: str
    title: str
    #: The date the text last changed. Users are recorded as accepting *this* version, so bumping
    #: it is what makes a previous acceptance stale.
    version: str
    body: str
    is_placeholder: bool

    @property
    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "title": self.title,
            "version": self.version,
            "body": self.body,
            "is_placeholder": self.is_placeholder,
        }


_TITLES = {TERMS: "Terms of Service", PRIVACY: "Privacy Policy"}

#: Bump when the corresponding document changes. A date is used rather than a counter because the
#: question a user asks is "which version did I agree to, and when".
VERSIONS: dict[str, str] = {TERMS: "2026-08-15", PRIVACY: "2026-08-15"}


@lru_cache(maxsize=len(DOCUMENTS))
def load(slug: str) -> LegalDocument:
    """Read a document off disk. Cached: these change on deploy, not per request."""
    if slug not in DOCUMENTS:
        raise KeyError(slug)

    path = CONTENT_ROOT / f"{slug}.md"
    try:
        body = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # A missing file is treated as unwritten rather than as an error: the deployment check
        # below is what surfaces it, and one clear failure is better than two.
        body = f"{PLACEHOLDER_MARKER}\n\n# {_TITLES[slug]}\n\nThis document has not been written."

    return LegalDocument(
        slug=slug,
        title=_TITLES[slug],
        version=VERSIONS[slug],
        body=body,
        is_placeholder=PLACEHOLDER_MARKER in body,
    )


def unwritten() -> list[str]:
    """Which documents are still the shipped placeholder."""
    return [slug for slug in DOCUMENTS if load(slug).is_placeholder]


def reset_cache() -> None:
    """For tests that write documents to disk."""
    load.cache_clear()


__all__ = [
    "DOCUMENTS",
    "PLACEHOLDER_MARKER",
    "PRIVACY",
    "TERMS",
    "VERSIONS",
    "LegalDocument",
    "load",
    "reset_cache",
    "unwritten",
]
