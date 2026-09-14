package kz.qorsa.leadgen.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Weights and thresholds for {@link kz.qorsa.leadgen.service.ScoringService}.
 * Bound from the "leadgen.scoring" prefix in application.yml so tuning never
 * requires touching code.
 */
@Getter
@Setter
@ConfigurationProperties(prefix = "leadgen.scoring")
public class ScoringProperties {

    /** Points added when the company has no website. */
    private int noSiteWeight = 40;

    /** Points added when the source implies direct buying intent (orders, vacancies, avito jobs). */
    private int directIntentWeight = 35;

    /** Points added when raw.competitorNegativeReview == true. */
    private int competitorNegativeReviewWeight = 30;

    /** Points added when raw.budgetMentioned == true. */
    private int budgetMentionedWeight = 20;

    /** Points added when a contact (phone or email) and a city are both present. */
    private int contactAndGeoWeight = 10;

    /** Points added when the primary phone is a mobile number - a direct line to the owner/decision-maker. */
    private int mobilePhoneWeight = 15;

    /** Points SUBTRACTED when the only phone(s) on file are toll-free (8-800) - usually a call center/network line. */
    private int tollFreeOnlyPenalty = 10;

    /** Fuzzy-name dedup threshold (tokenSortRatio, 0-100). */
    private int fuzzyNameThreshold = 82;

    /** Score threshold (inclusive) at which a lead becomes HOT. */
    private int hotThreshold = 70;

    /** Score threshold (inclusive) at which a lead becomes QUALIFIED. */
    private int qualifiedThreshold = 40;

    // --- Site-audit signals, written into raw by workers/site_audit. ---

    /** Points added when the PageSpeed score is below pagespeedThreshold. */
    private int pagespeedWeight = 30;

    /** PageSpeed score below which pagespeedWeight fires. Google paints below-50 red. */
    private int pagespeedThreshold = 50;

    /** Points added when the failed-check count exceeds auditFailsThreshold. */
    private int auditFailsWeight = 20;

    /**
     * Failed-check count above which auditFailsWeight fires. Calibrated
     * against a live run: real small-business sites score 1-3 failures out of
     * the ~15 checks the local probe runs, so the original value of 10 - meant
     * for a full Lighthouse audit list - could never have fired. Above 2 means
     * three independent problems at once.
     */
    private int auditFailsThreshold = 2;
}
