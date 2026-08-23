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
 * <p>Two rules are deliberately reserved as no-ops for now: pagespeed and
 * auditFails, which depend on a future site-audit worker that doesn't exist
 * yet. Their weights/thresholds already live in {@link ScoringProperties} so
 * wiring them in later is a one-line change here, not a schema change.
 */
@Service
public class ScoringService {

    private static final Set<LeadSource> DIRECT_INTENT_SOURCES =
            EnumSet.of(LeadSource.TELEGRAM_ORDER, LeadSource.VACANCY, LeadSource.AVITO_JOB);

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

        if (hasText(company.getPhone()) && hasText(company.getCity())) {
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

        // --- Future site-audit hooks (not evaluated yet, no raw data source exists) ---
        // pagespeed < properties.getPagespeedThreshold() -> + properties.getPagespeedWeight()
        // auditFails > properties.getAuditFailsThreshold() -> + properties.getAuditFailsWeight()

        String reason = String.join("; ", reasons);
        return new ScoreResult(score, reason);
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
