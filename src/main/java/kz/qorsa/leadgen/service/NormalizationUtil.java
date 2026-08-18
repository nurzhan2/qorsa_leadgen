package kz.qorsa.leadgen.service;

import java.util.regex.Pattern;

/**
 * Pure, static normalization helpers used to build the dedup keys stored on
 * {@link kz.qorsa.leadgen.domain.Company}. No Spring wiring on purpose: these
 * are trivially unit-testable string transforms.
 */
public final class NormalizationUtil {

    private static final Pattern NON_DIGITS = Pattern.compile("\\D+");
    private static final Pattern MULTI_SPACE = Pattern.compile("\\s+");
    // Common punctuation we strip from company names before comparison.
    private static final Pattern PUNCTUATION = Pattern.compile("[\"'.,!?()\\[\\]{}«»„“”\\-_/\\\\|:;]+");
    // Legal-entity forms (RU/KZ/EN) stripped as standalone tokens. UNICODE_CHARACTER_CLASS
    // is required for \b to recognize Cyrillic letters as word characters - by default
    // Java's \b/\w are ASCII-only, so "\bооо\b" would silently never match Cyrillic text.
    private static final Pattern LEGAL_FORMS = Pattern.compile(
            "\\b(ооо|зао|оао|ао|ип|тоо|пк|нко|llc|ltd|llp|inc|corp|co|gmbh|plc)\\b",
            Pattern.UNICODE_CHARACTER_CLASS);

    private NormalizationUtil() {
    }

    /**
     * Extracts and normalizes a bare domain from a URL, an email address, or
     * an already-bare domain: strips scheme, "www.", trailing slash/path, and
     * lowercases the result. Returns null for blank input.
     */
    public static String normalizeDomain(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        String value = raw.trim().toLowerCase();

        // Pull the domain portion out of an email address.
        int at = value.indexOf('@');
        if (at >= 0) {
            value = value.substring(at + 1);
        }

        value = value.replaceFirst("^[a-z]+://", "");
        value = value.replaceFirst("^www\\.", "");

        // Drop everything after the host: path, query, fragment, port.
        int cut = indexOfFirst(value, '/', '?', '#', ':');
        if (cut >= 0) {
            value = value.substring(0, cut);
        }

        value = value.trim();
        return value.isEmpty() ? null : value;
    }

    /**
     * Keeps only digits and returns the last 10 of them (enough to identify a
     * local number regardless of country-code prefix variations like +7 vs 8).
     * Returns null when fewer than 10 digits are present.
     */
    public static String normalizePhone(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        String digits = NON_DIGITS.matcher(raw).replaceAll("");
        if (digits.length() < 10) {
            return null;
        }
        return digits.substring(digits.length() - 10);
    }

    /**
     * Lowercases, strips punctuation and legal-entity suffixes/prefixes
     * (ООО, ИП, ТОО, LLC, ...), and collapses whitespace so that names like
     * '"ООО Ромашка"' and '"Ромашка"' normalize to the same key.
     */
    public static String normalizeName(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        String value = raw.trim().toLowerCase();
        value = PUNCTUATION.matcher(value).replaceAll(" ");
        value = LEGAL_FORMS.matcher(value).replaceAll(" ");
        value = MULTI_SPACE.matcher(value).replaceAll(" ").trim();
        return value.isEmpty() ? null : value;
    }

    private static int indexOfFirst(String value, char... candidates) {
        int min = -1;
        for (char c : candidates) {
            int idx = value.indexOf(c);
            if (idx >= 0 && (min == -1 || idx < min)) {
                min = idx;
            }
        }
        return min;
    }
}
