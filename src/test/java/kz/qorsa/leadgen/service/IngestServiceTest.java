package kz.qorsa.leadgen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import kz.qorsa.leadgen.config.ScoringProperties;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.web.dto.IngestResponse;
import kz.qorsa.leadgen.web.dto.RawCompanyRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

/**
 * Unit test for the batch counting in {@link IngestService#ingest}, with the
 * repositories faked in memory - no database, so it runs everywhere rather
 * than only where Docker/Testcontainers is available.
 *
 * <p>The behaviour under test is the distinction between "how many items were
 * in the batch" (created + merged) and "how many distinct leads came out of
 * it" (leadsScored/hotCount). Those used to be the same number, which
 * over-reported both metrics on any batch containing duplicates - and batches
 * containing duplicates are the normal case, since dedup is the whole point.
 *
 * <p>{@link ScoringService} is used for real (it's pure) so the HOT
 * classification here reflects the actual production scoring rules.
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class IngestServiceTest {

    @Mock
    private CompanyRepository companyRepository;

    @Mock
    private LeadRepository leadRepository;

    @Mock
    private DedupService dedupService;

    private IngestService ingestService;

    /** company id -> leads, standing in for the leads table. */
    private Map<UUID, List<Lead>> leadsByCompany;

    /** The first company persisted in a test - what dedup "finds" afterwards. */
    private AtomicReference<Company> firstSaved;

    @BeforeEach
    void setUp() {
        ScoringProperties properties = new ScoringProperties();
        ingestService = new IngestService(
                companyRepository, leadRepository, dedupService, new ScoringService(properties), properties);

        leadsByCompany = new HashMap<>();
        firstSaved = new AtomicReference<>();

        when(companyRepository.save(any(Company.class))).thenAnswer(invocation -> {
            Company company = invocation.getArgument(0);
            firstSaved.compareAndSet(null, company);
            return company;
        });

        when(leadRepository.findByCompanyId(any(UUID.class))).thenAnswer(invocation ->
                leadsByCompany.getOrDefault(invocation.getArgument(0), List.of()));

        when(leadRepository.save(any(Lead.class))).thenAnswer(invocation -> {
            Lead lead = invocation.getArgument(0);
            leadsByCompany
                    .computeIfAbsent(lead.getCompany().getId(), key -> new ArrayList<>())
                    .add(lead);
            return lead;
        });
    }

    /** Every candidate is a duplicate of the first one persisted. */
    private void dedupAlwaysMatchesTheFirstCompany() {
        when(dedupService.findExisting(any(Company.class)))
                .thenAnswer(invocation -> Optional.ofNullable(firstSaved.get()));
    }

    /** Nothing ever matches - every candidate is a brand-new company. */
    private void dedupNeverMatches() {
        when(dedupService.findExisting(any(Company.class))).thenReturn(Optional.empty());
    }

    @Test
    void countsOneLeadWhenEveryBatchItemDedupsOntoTheSameCompany() {
        dedupAlwaysMatchesTheFirstCompany();

        IngestResponse response = ingestService.ingest(List.of(
                sighting("Кофейня Ромашка"),
                sighting("ООО Ромашка Кофейня"),
                sighting("Ромашка Кофейня")));

        // Per-item accounting is unchanged and still sums to the batch size.
        assertThat(response.getCreated()).isEqualTo(1);
        assertThat(response.getMerged()).isEqualTo(2);
        assertThat(response.getCreated() + response.getMerged()).isEqualTo(3);

        // ...but there is only ONE lead. This is the assertion that used to fail.
        assertThat(response.getLeadsScored()).isEqualTo(1);
        assertThat(leadsByCompany).hasSize(1);
    }

    @Test
    void doesNotCountTheSameHotLeadOncePerBatchItem() {
        dedupAlwaysMatchesTheFirstCompany();

        // Every sighting scores HOT on its own (no site 40 + direct intent 35
        // + budget 20 = 95), so the old per-item counting reported hotCount=3.
        IngestResponse response = ingestService.ingest(List.of(
                hotSighting("Строй Мастер ТОО"),
                hotSighting("Строй Мастер"),
                hotSighting("ТОО Строй Мастер")));

        assertThat(response.getHotCount()).isEqualTo(1);
        assertThat(response.getLeadsScored()).isEqualTo(1);
    }

    @Test
    void countsEachDistinctCompanySeparately() {
        dedupNeverMatches();

        IngestResponse response = ingestService.ingest(List.of(
                sighting("Первая"),
                sighting("Вторая"),
                sighting("Третья")));

        assertThat(response.getCreated()).isEqualTo(3);
        assertThat(response.getMerged()).isZero();
        assertThat(response.getLeadsScored()).isEqualTo(3);
    }

    @Test
    void hotCountNeverExceedsLeadsScored() {
        dedupAlwaysMatchesTheFirstCompany();

        IngestResponse response = ingestService.ingest(List.of(
                hotSighting("Строй Мастер ТОО"),
                hotSighting("Строй Мастер"),
                sighting("Строй Мастер Групп")));

        assertThat(response.getHotCount()).isLessThanOrEqualTo(response.getLeadsScored());
    }

    @Test
    void anEmptyBatchReportsAllZeroes() {
        IngestResponse response = ingestService.ingest(List.of());

        assertThat(response.getCreated()).isZero();
        assertThat(response.getMerged()).isZero();
        assertThat(response.getLeadsScored()).isZero();
        assertThat(response.getHotCount()).isZero();
    }

    @Test
    void aLeadIsJudgedHotOnItsFinalScoreNotAnIntermediateOne() {
        dedupAlwaysMatchesTheFirstCompany();

        // First sighting alone scores 40 (no site) - NOT hot. The second adds
        // the budget signal (+20) and a phone+city (+10) plus mobile (+15),
        // pushing the same lead over the HOT threshold. One lead, counted once,
        // judged on where it ended up.
        IngestResponse response = ingestService.ingest(List.of(
                RawCompanyRequest.builder()
                        .name("Тихая Компания")
                        .city("Almaty")
                        .source(LeadSource.GOOGLE_MAPS)
                        .hasSite(false)
                        .build(),
                RawCompanyRequest.builder()
                        .name("Тихая Компания")
                        .city("Almaty")
                        .phone("+7 701 111 22 33")
                        .source(LeadSource.GOOGLE_MAPS)
                        .hasSite(false)
                        .raw(Map.of("budgetMentioned", true))
                        .build()));

        assertThat(response.getLeadsScored()).isEqualTo(1);
        assertThat(response.getHotCount()).isEqualTo(1);
    }

    private static RawCompanyRequest sighting(String name) {
        return RawCompanyRequest.builder()
                .name(name)
                .city("Almaty")
                .source(LeadSource.GOOGLE_MAPS)
                .hasSite(false)
                .build();
    }

    private static RawCompanyRequest hotSighting(String name) {
        return RawCompanyRequest.builder()
                .name(name)
                .city("Shymkent")
                .source(LeadSource.TELEGRAM_ORDER)
                .hasSite(false)
                .raw(Map.of("budgetMentioned", true))
                .build();
    }
}
