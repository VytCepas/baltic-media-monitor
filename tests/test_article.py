import pytest
from fakes import rows

from baltic import article
from baltic.article import Col


def test_every_fixture_row_parses_with_a_decoded_title():
    parsed = [article.parse(r, "en") for r in rows("sample.gkg.csv")]
    assert len(parsed) == 300 and all(parsed)
    assert all(a["title"] and "&#x" not in a["title"] for a in parsed)


@pytest.mark.parametrize(
    ("domain", "lang", "group"),
    [
        ("www.lrt.lt", "lit", "baltic"),
        ("rus.delfi.lv", "rus", "baltic_rus"),
        ("err.ee", "est", "baltic"),
        ("ria.ru", "rus", "ru_by"),
        ("sb.by", "rus", "ru_by"),
        ("rt.com", "eng", "ru_by"),
        ("sputniknews.com", "eng", "ru_by"),
        ("travelmarketreport.com", "eng", "other"),  # contains "rt.com" as a substring
        ("yle.fi", "fin", "regional"),
        ("www.spiegel.de", "deu", "regional"),
        ("bbc.co.uk", "eng", "other"),
    ],
)
def test_group(domain, lang, group):
    assert article.group(domain, lang) == group


def test_about_baltic_comes_from_locations_v2_then_v1():
    row = rows("sample.gkg.csv")[0][:]
    row[Col.V2_LOCATIONS], row[Col.LOCATIONS] = "1#Lithuania#LH#LH#55#24#LH#0", ""
    assert article.parse(row, "en")["about_baltic"]
    row[Col.V2_LOCATIONS], row[Col.LOCATIONS] = "", "1#Riga#LG#LG#56#24#LG"
    assert article.parse(row, "en")["about_baltic"]
    row[Col.LOCATIONS] = "1#France#FR#FR#46#2#FR"
    assert not article.parse(row, "en")["about_baltic"]


def test_security_themes_include_weapon_taxonomy():
    assert article.is_security(["TAX_WEAPONS_MISSILE"]) and article.is_security(["MILITARY"])
    assert not article.is_security(["EDUCATION", "TAX_FNCACT_TEACHER"])


def test_translated_rows_carry_source_language_and_tone():
    parsed = [article.parse(r, "tr") for r in rows("baltic_tr.gkg.csv")]
    assert {a["lang"] for a in parsed} > {"rus"}
    assert all(isinstance(a["tone"], float) for a in parsed)
    assert {a["group"] for a in parsed} == set(article.GROUPS)


def test_relevant_means_about_the_baltics_or_from_a_baltic_outlet():
    a = article.parse(rows("sample.gkg.csv")[0], "en")
    assert not article.is_relevant(a)
    assert article.is_relevant({**a, "domain": "lrt.lt"})
    assert article.is_relevant({**a, "about_baltic": True})


def test_malformed_row_is_none():
    assert article.parse(["only", "three", "fields"], "en") is None
