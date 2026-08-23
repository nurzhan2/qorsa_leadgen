package kz.qorsa.leadgen.service;

/**
 * Classification of a normalized Russian phone number, used to prioritize
 * which of a company's several phone numbers is worth calling first for a
 * cold outreach: a mobile number reaches the owner/decision-maker directly,
 * while an 8-800 toll-free number usually reaches a call center or
 * franchise network rather than the actual business.
 */
public enum PhoneType {
    /** +79XXXXXXXXX - a personal mobile number, the best cold-contact target. */
    MOBILE,
    /** +7495XXXXXXX or +7499XXXXXXX - Moscow city/landline. */
    CITY_MOSCOW,
    /** +7812XXXXXXX - Saint Petersburg city/landline. */
    CITY_SPB,
    /** Any other Russian city/landline code - a normal office business line. */
    CITY_OTHER,
    /** +78XXXXXXXXX other than the two city codes above (e.g. 8-800-...) - usually a call center/network line. */
    TOLL_FREE,
    /** Couldn't be parsed into a recognizable Russian phone number at all. */
    UNKNOWN
}
