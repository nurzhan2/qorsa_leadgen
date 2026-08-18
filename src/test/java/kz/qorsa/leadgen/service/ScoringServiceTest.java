package kz.qorsa.leadgen.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import kz.qorsa.leadgen.config.ScoringProperties;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.LeadSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Pure unit tests for {@link ScoringService}: no Spring context, no
 * database - just the rules against a plain {@link ScoringProperties}
 * instance built with the same defaults as application.yml.
 */
class ScoringServiceTest {

    private ScoringProperties properties;
    private ScoringService scoringService;

    @BeforeEach
    void setUp() {
        properties = new ScoringProperties();
        properties.setNoSiteWeight(40);
        properties.setDirectIntentWeight(35);
        properties.setCompetitorNegativeReviewWeight(30);
        properties.setBudgetMentionedWeight(20);
        properties.setContactAndGeoWeight(10);
        properties.setHotThreshold(70);
        properties.setQualifiedThreshold(40);
        scoringService = new ScoringService(properties);
    }

    @Test
    void noSiteRuleAddsWeightAndReason() {
        Company company = baseCompany().hasSite(false).build();

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(40);
        assertThat(result.reason()).isEqualTo("нет сайта");
    }

    @Test
    void directIntentSourceAddsWeightAndReason() {
        Company company = baseCompany().hasSite(true).source(LeadSource.TELEGRAM_ORDER).build();

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(35);
        assertThat(result.reason()).isEqualTo("прямой интент");
    }

    @Test
    void nonDirectIntentSourceDoesNotAddWeight() {
        Company company = baseCompany().hasSite(true).source(LeadSource.GOOGLE_MAPS).build();

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(0);
        assertThat(result.reason()).isEmpty();
    }

    @Test
    void competitorNegativeReviewRuleAddsWeightAndReason() {
        Company company = baseCompany()
                .hasSite(true)
                .raw(Map.of("competitorNegativeReview", true))
                .build();

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(30);
        assertThat(result.reason()).isEqualTo("недоволен конкурентом");
    }

    @Test
    void budgetMentionedRuleAddsWeightAndReason() {
        Company company = baseCompany()
                .hasSite(true)
                .raw(Map.of("budgetMentioned", true))
                .build();

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(20);
        assertThat(result.reason()).isEqualTo("упомянут бюджет");
    }

    @Test
    void contactAndGeoRuleRequiresBothPhoneAndCity() {
        Company withBoth = baseCompany().hasSite(true).phone("77011112233").city("Almaty").build();
        Company onlyPhone = baseCompany().hasSite(true).phone("77011112233").city(null).build();
        Company onlyCity = baseCompany().hasSite(true).phone(null).city("Almaty").build();

        assertThat(scoringService.score(withBoth).score()).isEqualTo(10);
        assertThat(scoringService.score(withBoth).reason()).isEqualTo("есть контакт+гео");
        assertThat(scoringService.score(onlyPhone).score()).isZero();
        assertThat(scoringService.score(onlyCity).score()).isZero();
    }

    @Test
    void reasonsAreJoinedInRuleEvaluationOrder() {
        Company company = baseCompany()
                .hasSite(false)
                .source(LeadSource.VACANCY)
                .raw(Map.of("competitorNegativeReview", true, "budgetMentioned", true))
                .phone("77011112233")
                .city("Almaty")
                .build();

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(40 + 35 + 30 + 20 + 10);
        assertThat(result.reason()).isEqualTo(
                "нет сайта; прямой интент; недоволен конкурентом; упомянут бюджет; есть контакт+гео");
    }

    @Test
    void scoreAtOrAboveHotThresholdMeansHot() {
        assertThat(properties.getHotThreshold()).isEqualTo(70);
        Company hot = baseCompany()
                .hasSite(false) // 40
                .source(LeadSource.AVITO_JOB) // +35 = 75
                .build();

        assertThat(scoringService.score(hot).score()).isGreaterThanOrEqualTo(properties.getHotThreshold());
    }

    @Test
    void scoreAtOrAboveQualifiedThresholdButBelowHotMeansQualified() {
        Company qualified = baseCompany()
                .hasSite(false) // 40
                .build();

        int score = scoringService.score(qualified).score();
        assertThat(score).isEqualTo(40);
        assertThat(score).isGreaterThanOrEqualTo(properties.getQualifiedThreshold());
        assertThat(score).isLessThan(properties.getHotThreshold());
    }

    @Test
    void scoreBelowQualifiedThresholdMeansNew() {
        Company plain = baseCompany().hasSite(true).source(LeadSource.GOOGLE_MAPS).build();

        int score = scoringService.score(plain).score();
        assertThat(score).isZero();
        assertThat(score).isLessThan(properties.getQualifiedThreshold());
    }

    private Company.CompanyBuilder baseCompany() {
        return Company.builder()
                .name("Test Company")
                .source(LeadSource.OTHER);
    }
}
