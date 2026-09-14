package kz.qorsa.leadgen.service;

import java.util.ArrayList;
import java.util.EnumSet;
import java.util.List;
import java.util.Set;
import kz.qorsa.leadgen.config.ScoringProperties;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.LeadSource;
import org.springframework.stereotype.Service;

/**
 * Turns a {@link Company} into a numeric lead score plus a human-readable
 * explanation of which rules fired. Weights and thresholds come from
 * {@link ScoringProperties} (application.yml, "leadgen.scoring") so tuning
 * the model never requires a code change or redeploy of rule logic.
 *
 * <p>Two rules read signals that only workers/site_audit produces:
 * pagespeed and auditFails. They stay silent until that worker has measured
 * the company - an unmeasured site is not the same as a good one, so a
 * missing value fires nothing rather than defaulting to zero.
 */
@Service
public class ScoringService {

    /**
     * Sources where the company has already stated a need out loud, rather
     * than merely looking like a plausible fit. ZAKUPKI belongs here for the
     * strongest reason of all: a published notice is a funded, dated,
     * legally-binding request to buy exactly this kind of work. Leaving it
     * out capped every procurement lead at 45 against a HOT threshold of 70,
     * so the source with the clearest buying signal could never go hot.
     */
    private static final Set<LeadSource> DIRECT_INTENT_SOURCES =
            EnumSet.of(
                    LeadSource.TELEGRAM_ORDER,
                    LeadSource.INSTAGRAM_CTA,
                    LeadSource.VACANCY,
                    LeadSource.AVITO_JOB,
                    LeadSource.ZAKUPKI);

    private final ScoringProperties properties;

    public ScoringService(ScoringProperties properties) {
        this.properties = properties;
    }

    public ScoreResult score(Company company) {
        List<String> reasons = new ArrayList<>();
        int score = 0;

        if (!company.isHasSite()) {
            score += properties.getNoSiteWeight();
            reasons.add("нет сайта");
        }

        if (company.getSource() != null && DIRECT_INTENT_SOURCES.contains(company.getSource())) {
            score += properties.getDirectIntentWeight();
            reasons.add("прямой интент");
        }

        if (isRawFlagTrue(company, "competitorNegativeReview")) {
            score += properties.getCompetitorNegativeReviewWeight();
            reasons.add("недоволен конкурентом");
        }

        if (isRawFlagTrue(company, "budgetMentioned")) {
            score += properties.getBudgetMentionedWeight();
            reasons.add("упомянут бюджет");
        }

        // A contact is a phone OR an email: a lead with only an email is still
        // reachable. Messenger deliberately doesn't count - on a Telegram lead
        // it's the poster's handle, and on a website it's as often a news
        // channel as a person.
        if ((hasText(company.getPhone()) || hasText(company.getEmail())) && hasText(company.getCity())) {
            score += properties.getContactAndGeoWeight();
            reasons.add("есть контакт+гео");
        }

        // primary_phone_type is set by PhoneUtil during ingest (IngestService).
        // Since PhoneUtil.pickPrimary() always prefers a mobile/city number over
        // a toll-free one when any exists, primary_phone_type == TOLL_FREE
        // already implies toll-free was the only kind of number on file.
        String primaryPhoneType = rawStringValue(company, "primary_phone_type");
        if ("MOBILE".equals(primaryPhoneType)) {
            score += properties.getMobilePhoneWeight();
            reasons.add("мобильный (прямой контакт)");
        } else if ("TOLL_FREE".equals(primaryPhoneType)) {
            score -= properties.getTollFreeOnlyPenalty();
            reasons.add("8-800 (вероятно сеть/колл-центр)");
        }

        // Site-audit signals, written into raw by workers/site_audit. Both are
        // deliberately one-sided: a slow or broken site raises the score, a
        // good one never lowers it. A company with a fast, clean site simply
        // isn't a lead for a studio that rebuilds sites - it scores zero here,
        // which is the honest answer, not a penalty.
        Integer pagespeed = rawIntValue(company, "pagespeed");
        if (pagespeed != null && pagespeed < properties.getPagespeedThreshold()) {
            score += properties.getPagespeedWeight();
            reasons.add("медленный сайт (PageSpeed " + pagespeed + ")");
        }

        Integer auditFails = rawIntValue(company, "auditFails");
        if (auditFails != null && auditFails > properties.getAuditFailsThreshold()) {
            score += properties.getAuditFailsWeight();
            reasons.add("сайт проваливает " + auditFails + " проверок");
        }

        String reason = String.join("; ", reasons);
        return new ScoreResult(score, reason);
    }

    /**
     * Reads a raw value that should be a whole number. Returns null when the
     * key is absent or unparseable - "we never measured this" and "we measured
     * zero" are different things, and only the second may fire a rule.
     */
    private static Integer rawIntValue(Company company, String key) {
        if (company.getRaw() == null) {
            return null;
        }
        Object value = company.getRaw().get(key);
        if (value == null) {
            return null;
        }
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return Integer.valueOf(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static String rawStringValue(Company company, String key) {
        if (company.getRaw() == null) {
            return null;
        }
        Object value = company.getRaw().get(key);
        return value == null ? null : String.valueOf(value);
    }

    private static boolean isRawFlagTrue(Company company, String key) {
        if (company.getRaw() == null) {
            return false;
        }
        Object value = company.getRaw().get(key);
        return Boolean.TRUE.equals(value) || "true".equalsIgnoreCase(String.valueOf(value));
    }

    private static boolean hasText(String value) {
        return value != null && !value.isBlank();
    }

    public record ScoreResult(int score, String reason) {
    }
}
