"""讀者權限（docs/02「輸出的對象：場域與個人」）：哪種 Profile 能看場域資料、哪種能看個人資料。

期望值照 docs/02 寫：場域資料給居服員與主管機關；個人資料給居服員、醫師、家屬、長者本人。
"""
import pytest
from test_caregiver import timeline
from test_rewrite import FakeModel

from radar_agent import kg_rules
from radar_agent.audience import check_audience
from radar_agent.device_events import summarize
from radar_agent.rewrite import rewrite

SITE = summarize(timeline("09:00:00", "09:10:00"))
PERSON = kg_rules.assess(kg_rules.load(), "來回行走", "5.4")

# (Profile, 能看場域, 能看個人)
DOCS_02 = [
    ("居服員", True, True),
    ("醫師", False, True),
    ("家屬", False, True),
    ("長者本人", False, True),
    ("主管機關", True, False),
]


@pytest.mark.parametrize(("profile", "site", "person"), DOCS_02)
def test_who_may_read_site_and_personal_data(profile, site, person):
    assert (check_audience(profile, SITE) == [], check_audience(profile, PERSON) == []) == (site, person)


def test_refusal_says_who_and_what():
    assert check_audience("主管機關", PERSON) == ["主管機關不能看個人資料（docs/02「輸出的對象：場域與個人」）"]
    assert check_audience("家屬", SITE) == ["家屬不能看場域資料（docs/02「輸出的對象：場域與個人」）"]


def test_unknown_profile_is_refused():
    assert check_audience("訪客", SITE) == ["沒有「訪客」這種 Profile（只有 居服員、醫師、家屬、長者本人、主管機關）"]


@pytest.mark.parametrize(("profile", "source"), [("主管機關", PERSON), ("家屬", SITE), ("訪客", SITE)])
def test_refused_data_never_reaches_the_template_or_the_model(profile, source):
    model = FakeModel("不該被呼叫")
    with pytest.raises(ValueError, match="讀者權限不符"):
        rewrite(source, profile=profile, place="1 號廁所", model=model)
    assert model.prompts == []


def test_allowed_profile_without_a_template_yet_is_not_silently_given_the_caregiver_text():
    with pytest.raises(ValueError, match="還沒有醫師的範本"):
        rewrite(PERSON, profile="醫師")


def test_the_profile_is_recorded_with_the_output():
    assert rewrite(SITE, place="1 號廁所").profile == "居服員"
    assert rewrite(PERSON, model=FakeModel("")).profile == "居服員"
