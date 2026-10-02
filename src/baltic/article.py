"""One GDELT GKG 2.1 row -> one Article: who published it, about which countries, on what topic, in what tone.

Pure functions without I/O, so the stream, the Spark job and the AI step classify articles identically.
"""

import html
import re
from enum import IntEnum
from typing import TypedDict

BALTIC_COUNTRIES = frozenset({"LH", "LG", "EN"})  # FIPS codes: Lithuania, Latvia, Estonia
BALTIC_TLDS = (".lt", ".lv", ".ee")
RU_BY_TLDS = (".ru", ".by")
RU_BY_DOMAINS = frozenset({"rt.com", "tass.com", "ura.news", "ruptly.tv"})
RU_BY_PREFIXES = ("sputnik", "baltnews", "rubaltic")
# the Baltics' neighbours: second control group
REGIONAL_TLDS = (".pl", ".fi", ".se", ".no", ".dk", ".de")
SECURITY_THEMES = frozenset(
    {
        "MILITARY",
        "ARMEDCONFLICT",
        "WMD",
        "BORDER",
        "ALLIANCE",
        "MARITIME_INCIDENT",
        "TERROR",
        "CYBER_ATTACK",
        "TAX_FNCACT_TROOPS",
        "SECURITY_SERVICES",
    }
)
GROUPS = ("ru_by", "regional", "other", "baltic", "baltic_rus")

_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
_SOURCE_LANG = re.compile(r"srclc:(\w+)")


class Col(IntEnum):
    """Column positions in a GKG 2.1 row (GDELT GKG codebook)."""

    ID = 0
    DATE = 1
    DOMAIN = 3
    URL = 4
    THEMES = 8
    LOCATIONS = 9
    V2_LOCATIONS = 10
    PERSONS = 11
    V2_PERSONS = 12
    ORGS = 13
    V2_ORGS = 14
    TONE = 15
    TRANSLATION = 25
    EXTRAS = 26


class Article(TypedDict):
    """The compact record every layer passes around (Kafka message body, Spark row, AI input)."""

    id: str
    ts: str
    feed: str
    domain: str
    lang: str
    group: str
    url: str
    title: str
    countries: list[str]
    about_baltic: bool
    security: bool
    themes: list[str]
    persons: list[str]
    orgs: list[str]
    tone: float | None


def group(domain: str, lang: str) -> str:
    """Classify a source domain into one of GROUPS by its top-level domain or known name."""
    d = domain.removeprefix("www.")
    if d.endswith(BALTIC_TLDS):
        return "baltic_rus" if lang == "rus" else "baltic"
    if d.endswith(RU_BY_TLDS) or d in RU_BY_DOMAINS or d.startswith(RU_BY_PREFIXES):
        return "ru_by"
    if d.endswith(REGIONAL_TLDS):
        return "regional"
    return "other"


def is_security(themes: list[str]) -> bool:
    """True when any theme is military, conflict, border, terror, cyber or weapons related."""
    return any(t in SECURITY_THEMES or t.startswith("TAX_WEAPONS_") for t in themes)


def is_relevant(a: Article) -> bool:
    """Kept by the batch layer: about a Baltic country, or published by a Baltic outlet."""
    return a["about_baltic"] or a["domain"].endswith(BALTIC_TLDS)


def _names(v2: str, v1: str) -> list[str]:
    """Unique names from a V2 field ('name,offset;...'), falling back to the V1 field."""
    return sorted({x.rsplit(",", 1)[0] for x in (v2 or v1).split(";") if x})


def parse(row: list[str], feed: str) -> Article | None:
    """Turn one tab-separated GKG row into an Article; None for a malformed row."""
    if len(row) <= Col.EXTRAS:
        return None
    locations = (row[Col.V2_LOCATIONS] or row[Col.LOCATIONS]).split(";")
    countries = sorted({loc.split("#")[2] for loc in locations if loc.count("#") >= 3})
    themes = sorted({t.split(",")[0] for t in row[Col.THEMES].split(";") if t})
    source_lang = _SOURCE_LANG.search(row[Col.TRANSLATION])
    lang = source_lang.group(1) if source_lang else "eng"  # the English feed carries no srclc tag
    title = _TITLE.search(row[Col.EXTRAS])
    domain = row[Col.DOMAIN].lower()
    return Article(
        id=row[Col.ID],
        ts=row[Col.DATE],
        feed=feed,
        domain=domain,
        lang=lang,
        group=group(domain, lang),
        url=row[Col.URL],
        title=html.unescape(title.group(1)).strip() if title else "",
        countries=countries,
        about_baltic=not BALTIC_COUNTRIES.isdisjoint(countries),
        security=is_security(themes),
        themes=themes,
        persons=_names(row[Col.V2_PERSONS], row[Col.PERSONS]),
        orgs=_names(row[Col.V2_ORGS], row[Col.ORGS]),
        tone=_tone(row[Col.TONE]),
    )


def _tone(field: str) -> float | None:
    """The average tone, the first number of the field; None when empty or unreadable."""
    try:
        return float(field.split(",")[0])
    except ValueError:
        return None
