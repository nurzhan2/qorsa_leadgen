package kz.qorsa.leadgen.service;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Pure, static helpers for splitting, normalizing, and classifying Russian
 * phone numbers, so a manager calling a fresh lead can dial the mobile
 * number first instead of an 8-800 line or a receptionist desk.
 *
 * <p>Distinct from {@link NormalizationUtil#normalizePhone}, which reduces a
 * phone to its last 10 digits purely as a dedup key. This class produces a
 * real, classifiable {@code +7XXXXXXXXXX} number (or several, for a raw
 * string listing multiple numbers) meant for actually placing a call.
 */
public final class PhoneUtil {

    /**
     * Matches one Russian phone number anywhere in a string: an optional
     * {@code +7}/{@code 7}/{@code 8} prefix followed by 10 digits, grouped
     * however the source formatted them (3-3-2-2 is the Russian
     * convention, but any punctuation/spacing between digits is accepted
     * since each separator class matches zero or more times) - or a bare
     * 10-digit mobile number with no prefix at all. Using {@code find()} in
     * a loop naturally treats ";", ",", "/", and plain whitespace between
     * complete numbers as separators, without needing to split on them
     * explicitly: none of those characters belong to the pattern, so the
     * matcher just skips over whatever separates two numbers and resumes
     * scanning for the next one.
     */
    private static final Pattern PHONE_TOKEN = Pattern.compile(
            "(?:\\+?7|8)[\\s\\-.()]*\\d{3}[\\s\\-.()]*\\d{3}[\\s\\-.()]*\\d{2}[\\s\\-.()]*\\d{2}"
                    + "|9\\d{9}\\b");

    private PhoneUtil() {
    }

    /**
     * Splits a raw string possibly containing several phone numbers
     * (separated by {@code ;}, {@code ,}, {@code /}, or plain whitespace)
     * into a list of individually normalized {@code +7XXXXXXXXXX} numbers.
     * Returns an empty list for blank/unparseable input.
     */
    public static List<String> normalizePhones(String raw) {
        List<String> result = new ArrayList<>();
        if (raw == null || raw.isBlank()) {
            return result;
        }

        Matcher matcher = PHONE_TOKEN.matcher(raw);
        while (matcher.find()) {
            String normalized = toE164(matcher.group());
            if (normalized != null) {
                result.add(normalized);
            }
        }
        return result;
    }

    /**
     * Classifies a single phone number (raw or already-normalized - this
     * re-extracts the first phone-shaped token from whatever is passed in,
     * so either works) into a {@link PhoneType}.
     */
    public static PhoneType classify(String phone) {
        if (phone == null) {
            return PhoneType.UNKNOWN;
        }

        Matcher matcher = PHONE_TOKEN.matcher(phone);
        if (!matcher.find()) {
            return PhoneType.UNKNOWN;
        }

        String normalized = toE164(matcher.group());
        if (normalized == null) {
            return PhoneType.UNKNOWN;
        }

        String local = normalized.substring(2); // 10 digits after "+7"
        String areaCode = local.substring(0, 3);

        if (local.charAt(0) == '9') {
            return PhoneType.MOBILE;
        }
        if (areaCode.equals("495") || areaCode.equals("499")) {
            return PhoneType.CITY_MOSCOW;
        }
        if (areaCode.equals("812")) {
            return PhoneType.CITY_SPB;
        }
        if (local.charAt(0) == '8') {
            return PhoneType.TOLL_FREE;
        }
        return PhoneType.CITY_OTHER;
    }

    /**
     * Picks the best number for a cold-outreach call: a mobile number if
     * any is present, otherwise any city/landline number, otherwise a
     * toll-free number, otherwise whatever's left. Returns null for an
     * empty/null list.
     */
    public static String pickPrimary(List<String> phones) {
        if (phones == null || phones.isEmpty()) {
            return null;
        }
        return phones.stream()
                .min(Comparator.comparingInt(phone -> priority(classify(phone))))
                .orElse(null);
    }

    private static int priority(PhoneType type) {
        return switch (type) {
            case MOBILE -> 0;
            case CITY_MOSCOW, CITY_SPB, CITY_OTHER -> 1;
            case TOLL_FREE -> 2;
            case UNKNOWN -> 3;
        };
    }

    /** Strips everything but digits and settles on a leading "+7". Null if the digit count is wrong. */
    private static String toE164(String rawToken) {
        String digits = rawToken.replaceAll("\\D", "");
        if (digits.length() == 11 && (digits.charAt(0) == '7' || digits.charAt(0) == '8')) {
            return "+7" + digits.substring(1);
        }
        if (digits.length() == 10) {
            return "+7" + digits;
        }
        return null;
    }
}
