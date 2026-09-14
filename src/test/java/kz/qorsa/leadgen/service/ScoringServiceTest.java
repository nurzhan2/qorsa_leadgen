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
        properties.setMobilePhoneWeight(15);
        properties.setTollFreeOnlyPenalty(10);
        properties.setHotThreshold(70);
        properties.setQualifiedThreshold(40);
        properties.setPagespeedWeight(30);
        properties.setPagespeedThreshold(50);
        properties.setAuditFailsWeight(20);
        properties.setAuditFailsThreshold(3);
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
    void aProcurementNoticeCountsAsDirectIntent() {
        // A published notice is a funded, dated request to buy this work -
        // the strongest buying signal any source produces. While ZAKUPKI was
        // missing from the set, procurement leads capped at 45 against a HOT
        // threshold of 70 and not one could ever go hot.
        Company company = baseCompany().hasSite(true).source(LeadSource.ZAKUPKI).build();

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
    void aSlowSiteAddsPagespeedWeightAndNamesTheScore() {
        Company company = auditedCompany(Map.of("pagespeed", 23));

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(30);
        assertThat(result.reason()).isEqualTo("медленный сайт (PageSpeed 23)");
    }

    @Test
    void aFastSiteAddsNothingAndIsNotPenalized() {
        Company company = auditedCompany(Map.of("pagespeed", 91));

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(0);
        assertThat(result.reason()).isEmpty();
    }

    @Test
    void anUnmeasuredSiteIsNotTreatedAsAZeroScore() {
        // The whole point of the null check: "nobody looked" must not read as
        // "the slowest site in the database".
        Company company = auditedCompany(Map.of());

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(0);
        assertThat(result.reason()).isEmpty();
    }

    @Test
    void failedChecksAboveThresholdAddAuditFailsWeight() {
        Company company = auditedCompany(Map.of("auditFails", 5));

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(20);
        assertThat(result.reason()).isEqualTo("сайт проваливает 5 проверок");
    }

    @Test
    void failedChecksAtOrBelowThresholdAddNothing() {
        Company company = auditedCompany(Map.of("auditFails", 3));

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(0);
    }

    @Test
    void anAuditSignalArrivingAsAStringIsStillRead() {
        // JSONB round-trips can hand back a string where an int went in.
        Company company = auditedCompany(Map.of("pagespeed", "17"));

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(30);
    }

    @Test
    void anOsmCompanyWithASiteCanNowReachHot() {
        // The case this whole worker exists for: a real business, contactable,
        // with a site bad enough to rebuild. Before the audit signals existed
        // it capped at 25 and was permanently cold.
        Company company = baseCompany()
                .hasSite(true)
                .source(LeadSource.OSM)
                .phone("+79991234567")
                .city("Москва")
                .raw(Map.of(
                        "primary_phone_type", "MOBILE",
                        "pagespeed", 31,
                        "auditFails", 6))
                .build();

        ScoringService.ScoreResult result = scoringService.score(company);

        // 10 contact+geo + 15 mobile + 30 pagespeed + 20 auditFails
        assertThat(result.score()).isEqualTo(75);
        assertThat(result.score()).isGreaterThanOrEqualTo(properties.getHotThreshold());
    }

    /** An OSM company that owns a site, carrying only the given audit signals. */
    private Company auditedCompany(Map<String, Object> raw) {
        return baseCompany().hasSite(true).source(LeadSource.OSM).raw(raw).build();
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
    void anEmailCountsAsTheContactInContactAndGeo() {
        Company emailAndCity = baseCompany().hasSite(true).email("info@romashka.kz").city("Almaty").build();
        Company emailNoCity = baseCompany().hasSite(true).email("info@romashka.kz").city(null).build();
        Company bothContacts = baseCompany().hasSite(true)
                .phone("77011112233").email("info@romashka.kz").city("Almaty").build();

        assertThat(scoringService.score(emailAndCity).score()).isEqualTo(10);
        assertThat(scoringService.score(emailAndCity).reason()).isEqualTo("есть контакт+гео");
        assertThat(scoringService.score(emailNoCity).score()).isZero();
        // Phone AND email is still one contact - the rule fires once, not twice.
        assertThat(scoringService.score(bothContacts).score()).isEqualTo(10);
    }

    @Test
    void aMessengerAloneIsNotAContactForContactAndGeo() {
        Company company = baseCompany().hasSite(true).messenger("https://t.me/romashka").city("Almaty").build();

        assertThat(scoringService.score(company).score()).isZero();
    }

    @Test
    void mobilePrimaryPhoneTypeAddsWeightAndReason() {
        Company company = baseCompany()
                .hasSite(true)
                .raw(Map.of("primary_phone_type", "MOBILE"))
                .build();

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(15);
        assertThat(result.reason()).isEqualTo("мобильный (прямой контакт)");
    }

    @Test
    void tollFreePrimaryPhoneTypeSubtractsWeightAndAddsReason() {
        Company company = baseCompany()
                .hasSite(true)
                .raw(Map.of("primary_phone_type", "TOLL_FREE"))
                .build();

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isEqualTo(-10);
        assertThat(result.reason()).isEqualTo("8-800 (вероятно сеть/колл-центр)");
    }

    @Test
    void cityPrimaryPhoneTypeIsNotPenalizedOrRewarded() {
        Company moscow = baseCompany().hasSite(true).raw(Map.of("primary_phone_type", "CITY_MOSCOW")).build();
        Company spb = baseCompany().hasSite(true).raw(Map.of("primary_phone_type", "CITY_SPB")).build();
        Company other = baseCompany().hasSite(true).raw(Map.of("primary_phone_type", "CITY_OTHER")).build();

        assertThat(scoringService.score(moscow).score()).isZero();
        assertThat(scoringService.score(spb).score()).isZero();
        assertThat(scoringService.score(other).score()).isZero();
        assertThat(scoringService.score(moscow).reason()).isEmpty();
    }

    @Test
    void missingPrimaryPhoneTypeAddsNoPhoneTypeRuleWeight() {
        Company company = baseCompany().hasSite(true).build();

        ScoringService.ScoreResult result = scoringService.score(company);

        assertThat(result.score()).isZero();
        assertThat(result.reason()).isEmpty();
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
