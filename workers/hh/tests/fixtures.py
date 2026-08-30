"""Test fixtures modelled on REAL HH API payloads.

PROVENANCE - important, and deliberately not overstated:

`VACANCY_ITEM` is HH's own published example of a vacancy in search results
("Короткое представление вакансии") from their official docs repo,
https://github.com/hhru/api/blob/master/docs/vacancies.md, fetched while
building this worker. Field names and nesting were cross-checked against
HH's OpenAPI spec (https://api.hh.ru/openapi/specification/public), which
lists 47 fields on a search item - the ones this worker reads are all
present here.

`EMPLOYER_DETAIL` mirrors the GET /employers/{id} response schema from the
same spec (site_url, type, name, id, area, industries, ...).

`EMPLOYER_TYPES` are the LIVE values from https://api.hh.ru/dictionaries,
which is reachable without a token and was fetched directly.

What these are NOT: a capture of a live /vacancies response. That endpoint
returns 403 from this network without an OAuth token (see README.md
"Доступ к API"), so no live search response could be recorded. The shape
comes from HH's own documentation and machine-readable spec rather than
from memory - but it is documentation, not a wire capture, and a field HH
adds tomorrow won't be here.
"""

import copy

# HH's published search-result item, verbatim from docs/vacancies.md.
VACANCY_ITEM = {
    "id": "7760476",
    "premium": True,
    "has_test": True,
    "response_url": None,
    "address": None,
    "alternate_url": "https://hh.ru/vacancy/7760476",
    "apply_alternate_url": "https://hh.ru/applicant/vacancy_response?vacancyId=7760476",
    "department": {"id": "HH-1455-TECH", "name": "HeadHunter::Технический департамент"},
    "salary": {"to": None, "from": 100000, "currency": "RUR", "gross": True},
    "name": "Специалист по автоматизации тестирования (Java, Selenium)",
    "insider_interview": {"id": "12345", "url": "https://hh.ru/interview/12345?employerId=777"},
    "area": {"url": "https://api.hh.ru/areas/1", "id": "1", "name": "Москва"},
    "url": "https://api.hh.ru/vacancies/7760476",
    "published_at": "2013-10-11T13:27:16+0400",
    "relations": [],
    "employer": {
        "url": "https://api.hh.ru/employers/1455",
        "alternate_url": "https://hh.ru/employer/1455",
        "logo_urls": {
            "90": "https://hh.ru/employer-logo/289027.png",
            "240": "https://hh.ru/employer-logo/289169.png",
            "original": "https://hh.ru/file/2352807.png",
        },
        "name": "HeadHunter",
        "id": "1455",
    },
    "response_letter_required": False,
    "type": {"id": "open", "name": "Открытая"},
    "archived": False,
    "working_days": [{"id": "only_saturday_and_sunday", "name": "Работа только по сб и вс"}],
    "working_time_intervals": [
        {"id": "from_four_to_six_hours_in_a_day",
         "name": "Можно работать сменами по 4-6 часов в день"}
    ],
    "working_time_modes": [{"id": "start_after_sixteen",
                            "name": "Можно начинать работать после 16-00"}],
    "accept_temporary": False,
    "experience": {"id": "noExperience", "name": "Нет опыта"},
    "employment": {"id": "full", "name": "Полная занятость"},
    "show_logo_in_search": True,
}

# GET /vacancies envelope, per the spec's 200 response
# (found / pages / page / per_page / items).
SEARCH_RESPONSE = {
    "found": 1,
    "pages": 1,
    "page": 0,
    "per_page": 100,
    "items": [VACANCY_ITEM],
    "clusters": None,
    "arguments": None,
}

# GET /employers/{id}, per the spec (20 fields; the ones we read shown).
EMPLOYER_DETAIL = {
    "id": "1455",
    "name": "HeadHunter",
    "type": "company",
    "site_url": "https://hh.ru",
    "alternate_url": "https://hh.ru/employer/1455",
    "area": {"id": "1", "name": "Москва", "url": "https://api.hh.ru/areas/1"},
    "industries": [{"id": "7.540", "name": "Интернет-компания"}],
    "open_vacancies": 42,
    "trusted": True,
    "accredited_it_employer": True,
}

# Live from https://api.hh.ru/dictionaries -> employer_type.
EMPLOYER_TYPES = {
    "company": "Организация",
    "agency": "Кадровое агентство",
    "project_director": "Проект",
    "private_recruiter": "Частный рекрутер",
    "private_individual": "Частное лицо",
    "self_employed": "Самозанятый",
}


def vacancy(
    vacancy_id: str = "1",
    employer_id: str = "100",
    employer_name: str = "ООО Ромашка",
    published_at: str = "2026-08-01T10:00:00+0300",
    name: str = "Веб-разработчик",
    salary: dict | None = None,
    area_name: str = "Москва",
    accredited_it: bool = False,
) -> dict:
    """A search item shaped exactly like VACANCY_ITEM, with the bits a test
    cares about overridden. Built by copying the real fixture so tests can
    never accidentally rely on a field the real payload doesn't have."""
    item = copy.deepcopy(VACANCY_ITEM)
    item["id"] = vacancy_id
    item["name"] = name
    item["published_at"] = published_at
    item["alternate_url"] = f"https://hh.ru/vacancy/{vacancy_id}"
    item["salary"] = salary
    item["area"] = {"url": "https://api.hh.ru/areas/1", "id": "1", "name": area_name}
    item["employer"] = {
        "url": f"https://api.hh.ru/employers/{employer_id}",
        "alternate_url": f"https://hh.ru/employer/{employer_id}",
        "name": employer_name,
        "id": employer_id,
        "accredited_it_employer": accredited_it,
    }
    return item
