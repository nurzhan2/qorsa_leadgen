"""Collapses many vacancies into one lead per employer.

A company hiring a frontend dev, a backend dev and a designer shows up three
times in search results - but it is ONE company and must reach the core once.
Sending it three times would inflate the batch and lean on the core's dedup
for something we can settle here for free.

Which vacancy represents the employer matters: the **stalest** one wins.
That's the vacancy that best evidences "we can't hire for this", so it's the
one a salesperson should open, and it's what `sourceUrl` points at.

Pure - no network, no HH import.
"""

from dataclasses import dataclass, field


@dataclass
class EmployerBucket:
    """Everything collected about one employer during a run."""

    employer_id: str
    employer_name: str
    #: The vacancy chosen to represent this employer (the stalest one).
    best: dict = field(default_factory=dict)
    #: Age in days of `best`; None when no vacancy had a usable date.
    best_age: int | None = None
    vacancy_count: int = 0
    #: True if ANY of this employer's vacancies is stale.
    has_stale: bool = False
    #: True if ANY of them quoted a salary.
    has_salary: bool = False
    #: Keywords that surfaced this employer, for the raw payload.
    keywords: list[str] = field(default_factory=list)


class EmployerAggregator:
    """Groups vacancies by employer_id, keeping the stalest as representative.

    Vacancies with no employer id are dropped: without it there's nothing to
    group on, and an anonymous HH posting has no company to sell to anyway.
    """

    def __init__(self):
        self._buckets: dict[str, EmployerBucket] = {}
        self.skipped_no_employer = 0

    def add(self, vacancy: dict, age: int | None, stale: bool, has_salary: bool, keyword: str) -> None:
        employer = vacancy.get("employer") or {}
        employer_id = str(employer.get("id") or "").strip()
        if not employer_id:
            self.skipped_no_employer += 1
            return

        bucket = self._buckets.get(employer_id)
        if bucket is None:
            bucket = EmployerBucket(
                employer_id=employer_id,
                employer_name=employer.get("name") or "",
                best=vacancy,
                best_age=age,
            )
            self._buckets[employer_id] = bucket
        elif _is_stalER(age, bucket.best_age):
            # Strictly older wins; ties keep the first seen, so the result is
            # deterministic for a given input order.
            bucket.best = vacancy
            bucket.best_age = age

        bucket.vacancy_count += 1
        bucket.has_stale = bucket.has_stale or stale
        bucket.has_salary = bucket.has_salary or has_salary
        if keyword and keyword not in bucket.keywords:
            bucket.keywords.append(keyword)

    def buckets(self) -> list[EmployerBucket]:
        """Stalest employers first - the hottest leads lead the batch, which
        matters when TARGET_PER_DAY truncates the run."""
        return sorted(
            self._buckets.values(),
            key=lambda b: (b.best_age is None, -(b.best_age or 0)),
        )

    def __contains__(self, employer_id: str) -> bool:
        return str(employer_id) in self._buckets

    def __len__(self) -> int:
        return len(self._buckets)


def _is_stalER(candidate: int | None, current: int | None) -> bool:
    """Is `candidate` older than `current`? A known age always beats an
    unknown one - an undated vacancy is a worse representative than any
    dated one."""
    if candidate is None:
        return False
    if current is None:
        return True
    return candidate > current
