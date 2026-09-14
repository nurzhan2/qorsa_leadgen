package kz.qorsa.leadgen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import kz.qorsa.leadgen.config.ScoringProperties;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.domain.LeadStatus;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.service.ContactEnrichmentService.EnrichmentResult;
import kz.qorsa.leadgen.web.dto.ContactsPatchRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

/**
 * Unit tests for {@link ContactEnrichmentService} with the repositories faked
 * in memory. {@link IngestService} and {@link ScoringService} are real, so
 * the re-scoring assertions reflect the production rules, not a stub.
 *
 * <p>The case that matters most is "never overwrite": a website is a weaker
 * source than the directory/API that reported the company, so a value that
 * source gave us must survive enrichment untouched.
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class ContactEnrichmentServiceTest {

    @Mock
    private CompanyRepository companyRepository;

    @Mock
    private LeadRepository leadRepository;

    @Mock
    private DedupService dedupService;

    private ContactEnrichmentService service;

    private Map<UUID, List<Lead>> leadsByCompany;

    @BeforeEach
    void setUp() {
        ScoringProperties properties = new ScoringProperties();
        IngestService ingestService = new IngestService(
                companyRepository, leadRepository, dedupService, new ScoringService(properties), properties);
        service = new ContactEnrichmentService(companyRepository, ingestService);

        leadsByCompany = new HashMap<>();
        when(companyRepository.save(any(Company.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(leadRepository.findByCompanyId(any(UUID.class))).thenAnswer(invocation ->
                leadsByCompany.getOrDefault(invocation.getArgument(0), List.of()));
        when(leadRepository.save(any(Lead.class))).thenAnswer(invocation -> {
            Lead lead = invocation.getArgument(0);
            List<Lead> leads = leadsByCompany.computeIfAbsent(lead.getCompany().getId(), key -> new ArrayList<>());
            if (!leads.contains(lead)) {
                leads.add(lead);
            }
            return lead;
        });
    }

    private Company stored(Company company) {
        when(companyRepository.findById(company.getId())).thenReturn(Optional.of(company));
        return company;
    }

    private static Company.CompanyBuilder hhCompany() {
        return Company.builder()
                .name("ТОО Ромашка")
                .domain("romashka.kz")
                .normalizedDomain("romashka.kz")
                .city("Алматы")
                .source(LeadSource.HH)
                .hasSite(true)
                .raw(new HashMap<>(Map.of("employer_id", "100")));
    }

    // --- never overwrite ----------------------------------------------------

    @Test
    void fillsOnlyTheBlankFieldsAndLeavesTheSourcesEmailAlone() {
        Company company = stored(hhCompany().email("boss@romashka.kz").build());

        EnrichmentResult result = service.applyContacts(company.getId(), ContactsPatchRequest.builder()
                .email("info@romashka.kz")
                .phone("+7 916 123 45 67")
                .messenger("https://wa.me/79161234567")
                .build()).orElseThrow();

        assertThat(company.getEmail()).isEqualTo("boss@romashka.kz");
        assertThat(company.getPhone()).isEqualTo("+79161234567");
        assertThat(company.getMessenger()).isEqualTo("https://wa.me/79161234567");
        // `filled` reports what was WRITTEN, not what was sent.
        assertThat(result.filled()).containsExactly("phone", "messenger");
    }

    @Test
    void neverOverwritesAnExistingPhoneOrItsClassification() {
        Map<String, Object> raw = new HashMap<>(Map.of("primary_phone_type", "CITY_OTHER",
                "all_phones", List.of("+77273551020")));
        Company company = stored(hhCompany().phone("+77273551020").normalizedPhone("7273551020").raw(raw).build());

        EnrichmentResult result = service.applyContacts(company.getId(), ContactsPatchRequest.builder()
                .phone("+7 916 123 45 67")
                .build()).orElseThrow();

        assertThat(company.getPhone()).isEqualTo("+77273551020");
        assertThat(company.getNormalizedPhone()).isEqualTo("7273551020");
        assertThat(company.getRaw()).containsEntry("primary_phone_type", "CITY_OTHER");
        assertThat(company.getRaw().get("all_phones")).isEqualTo(List.of("+77273551020"));
        assertThat(result.filled()).isEmpty();
    }

    @Test
    void keepsTheSourcesRawKeysWhenAddingEnrichmentOnes() {
        Company company = stored(hhCompany().build());

        service.applyContacts(company.getId(), ContactsPatchRequest.builder().email("info@romashka.kz").build());

        assertThat(company.getRaw()).containsEntry("employer_id", "100");
    }

    // --- the attempted flag -------------------------------------------------

    @Test
    void marksTheCompanyAttemptedEvenWhenNothingWasFound() {
        Company company = stored(hhCompany().build());

        EnrichmentResult result = service.applyContacts(company.getId(), ContactsPatchRequest.builder()
                .enrichNotes("сайт недоступен: ConnectError")
                .build()).orElseThrow();

        assertThat(result.filled()).isEmpty();
        assertThat(company.getRaw()).containsEntry("enrich_attempted", true);
        assertThat(company.getRaw()).containsEntry("enrich_notes", "сайт недоступен: ConnectError");
        assertThat(company.getRaw()).containsEntry("enrich_filled", List.of());
        assertThat(Instant.parse(String.valueOf(company.getRaw().get("enrich_at"))))
                .isBeforeOrEqualTo(Instant.now());
    }

    @Test
    void anEmptyBodyIsAValidTriedAndFoundNothingReport() {
        Company company = stored(hhCompany().build());

        EnrichmentResult result = service.applyContacts(company.getId(), new ContactsPatchRequest()).orElseThrow();

        assertThat(result.filled()).isEmpty();
        assertThat(company.getRaw()).containsEntry("enrich_attempted", true);
        assertThat(company.getRaw()).doesNotContainKey("enrich_notes");
    }

    @Test
    void anUnknownCompanyIsNotCreated() {
        UUID unknown = UUID.randomUUID();
        when(companyRepository.findById(unknown)).thenReturn(Optional.empty());

        assertThat(service.applyContacts(unknown, ContactsPatchRequest.builder().email("a@b.kz").build())).isEmpty();
    }

    // --- phone classification + re-scoring ----------------------------------

    @Test
    void aPhoneFromTheSiteIsClassifiedAndRescoresTheLead() {
        Company company = stored(hhCompany().build());

        EnrichmentResult result = service.applyContacts(company.getId(), ContactsPatchRequest.builder()
                .phone("+7 916 123 45 67")
                .build()).orElseThrow();

        assertThat(company.getRaw()).containsEntry("primary_phone_type", "MOBILE");
        assertThat(company.getNormalizedPhone()).isEqualTo("9161234567");
        // has site (0) + HH is not a direct-intent source (0)
        // + contact & geo (10) + mobile (15) = 25
        assertThat(result.lead().getScore()).isEqualTo(25);
        assertThat(result.lead().getHotReason()).isEqualTo("есть контакт+гео; мобильный (прямой контакт)");
        assertThat(result.lead().getStatus()).isEqualTo(LeadStatus.NEW);
    }

    @Test
    void severalPhonesGoThroughPhoneUtilWhichPicksTheOneToCall() {
        Company company = stored(hhCompany().build());

        service.applyContacts(company.getId(), ContactsPatchRequest.builder()
                .phone("+7 800 555-35-35; +7 916 123 45 67")
                .build());

        // Mobile beats toll-free, whatever order the worker listed them in.
        assertThat(company.getPhone()).isEqualTo("+79161234567");
        assertThat(company.getRaw().get("all_phones")).isEqualTo(List.of("+78005553535", "+79161234567"));
    }

    @Test
    void anUnparseablePhoneIsIgnoredButTheAttemptStillCounts() {
        Company company = stored(hhCompany().build());

        EnrichmentResult result = service.applyContacts(company.getId(), ContactsPatchRequest.builder()
                .phone("звоните в офис")
                .build()).orElseThrow();

        assertThat(company.getPhone()).isNull();
        assertThat(company.getRaw()).doesNotContainKey("primary_phone_type");
        assertThat(result.filled()).isEmpty();
        assertThat(company.getRaw()).containsEntry("enrich_attempted", true);
    }

    @Test
    void anEmailAloneIsEnoughForContactAndGeo() {
        Company company = stored(hhCompany().build());

        EnrichmentResult result = service.applyContacts(company.getId(), ContactsPatchRequest.builder()
                .email("  Info@Romashka.KZ ")
                .build()).orElseThrow();

        assertThat(company.getEmail()).isEqualTo("info@romashka.kz");
        assertThat(result.lead().getScore()).isEqualTo(10);
        assertThat(result.lead().getHotReason()).isEqualTo("есть контакт+гео");
    }

    @Test
    void rescoringUpdatesTheExistingLeadInsteadOfCreatingASecondOne() {
        Company company = stored(hhCompany().build());
        Lead existing = Lead.builder().company(company).score(0).build();
        leadsByCompany.put(company.getId(), new ArrayList<>(List.of(existing)));

        EnrichmentResult result = service.applyContacts(company.getId(), ContactsPatchRequest.builder()
                .email("info@romashka.kz")
                .build()).orElseThrow();

        assertThat(result.lead()).isSameAs(existing);
        assertThat(existing.getScore()).isEqualTo(10);
        assertThat(leadsByCompany.get(company.getId())).hasSize(1);
    }

    @Test
    void aSecondAttemptKeepsTheRecordOfWhatTheFirstFilled() {
        Company company = stored(hhCompany().build());
        service.applyContacts(company.getId(), ContactsPatchRequest.builder().email("info@romashka.kz").build());

        service.applyContacts(company.getId(), ContactsPatchRequest.builder().phone("+7 916 123 45 67").build());

        assertThat(company.getRaw().get("enrich_filled")).isEqualTo(List.of("email", "phone"));
    }

    // --- pending ------------------------------------------------------------

    @Test
    void pendingLimitIsClampedToASaneRange() {
        when(companyRepository.findPendingEnrichment(anyInt())).thenReturn(List.of());

        service.findPending(10_000);
        verify(companyRepository).findPendingEnrichment(ContactEnrichmentService.MAX_PENDING_LIMIT);

        service.findPending(0);
        verify(companyRepository).findPendingEnrichment(1);
    }
}
