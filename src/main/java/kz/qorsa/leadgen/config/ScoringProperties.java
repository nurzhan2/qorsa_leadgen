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

    // --- Hooks reserved for the future site-audit worker. Not applied yet. ---

    /** Future: points added when PageSpeed score is below pagespeedThreshold. */
    private int pagespeedWeight = 30;

    /** Future: PageSpeed score below which the pagespeedWeight hook fires. */
    private int pagespeedThreshold = 50;

    /** Future: points added when auditFails exceeds auditFailsThreshold. */
    private int auditFailsWeight = 20;

    /** Future: audit-fail count above which the auditFailsWeight hook fires. */
    private int auditFailsThreshold = 10;
}
