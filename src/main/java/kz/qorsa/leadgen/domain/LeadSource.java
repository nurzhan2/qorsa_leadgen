package kz.qorsa.leadgen.domain;

/**
 * Where a raw company record originated from. Real scrapers (Telegram, Google
 * Maps, 2GIS, OpenStreetMap, ...) live as separate Python workers outside
 * this service; they only need to tag their payload with the right source
 * when POSTing to /api/v1/companies/ingest.
 *
 * <p>Stored as a plain VARCHAR (see V1__init.sql, {@code @Enumerated(STRING)}
 * on {@link Company#source}) with no DB-level check constraint, so adding a
 * new constant here never requires a migration.
 */
public enum LeadSource {
    TELEGRAM_ORDER,
    GOOGLE_MAPS,
    TWOGIS,
    OSM,
    ZAKUPKI,
    NEW_DOMAIN,
    YANDEX_REVIEW,
    /** Generic/other job-board vacancies. */
    VACANCY,
    /** hh.ru vacancies specifically (workers/hh) - kept separate from VACANCY
     *  because its leads carry the stale-vacancy signals in raw. */
    HH,
    AVITO_JOB,
    DEMO,
    OTHER
}
