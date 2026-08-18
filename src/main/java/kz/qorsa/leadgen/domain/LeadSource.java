package kz.qorsa.leadgen.domain;

/**
 * Where a raw company record originated from. Real scrapers (Telegram, Google
 * Maps, 2GIS, ...) live as separate Python workers outside this service; they
 * only need to tag their payload with the right source when POSTing to
 * /api/v1/companies/ingest.
 */
public enum LeadSource {
    TELEGRAM_ORDER,
    GOOGLE_MAPS,
    TWOGIS,
    YANDEX_REVIEW,
    VACANCY,
    AVITO_JOB,
    DEMO,
    OTHER
}
