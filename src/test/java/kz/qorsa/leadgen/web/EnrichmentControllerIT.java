package kz.qorsa.leadgen.web;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.web.dto.ContactsPatchRequest;
import kz.qorsa.leadgen.web.dto.ContactsPatchResponse;
import kz.qorsa.leadgen.web.dto.LeadResponse;
import kz.qorsa.leadgen.web.dto.PendingEnrichCompany;
import kz.qorsa.leadgen.web.dto.RawCompanyRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.SpringBootTest.WebEnvironment;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * End-to-end test of the enrichment boundary against a real Postgres:
 * GET /api/v1/companies/pending-enrich (a native JSONB query, so it has to
 * run on the real database to mean anything) and
 * PATCH /api/v1/companies/{id}/contacts.
 *
 * <p>Companies are created through the real ingest endpoint, so the rows look
 * exactly like what a worker produces.
 */
@Testcontainers
@SpringBootTest(webEnvironment = WebEnvironment.RANDOM_PORT)
class EnrichmentControllerIT {

    @Container
    static PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16")
            .withDatabaseName("qorsa_leadgen_enrich_test")
            .withUsername("qorsa")
            .withPassword("qorsa");

    @DynamicPropertySource
    static void datasourceProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
    }

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate restTemplate;

    @Autowired
    private CompanyRepository companyRepository;

    @Autowired
    private LeadRepository leadRepository;

    @BeforeEach
    void resetDatabase() {
        // The default HttpURLConnection-based factory cannot send PATCH at all
        // ("Invalid HTTP method: PATCH"); the JDK HttpClient one can.
        restTemplate.getRestTemplate().setRequestFactory(new JdkClientHttpRequestFactory());
        leadRepository.deleteAll();
        companyRepository.deleteAll();
    }

    // --- GET /pending-enrich --------------------------------------------------

    @Test
    void pendingReturnsOnlyCompaniesWithADomainAndAMissingContact() {
        ingest(List.of(
                company("Ромашка", "romashka.kz", null, null),              // no email, no phone -> pending
                company("Только почта", "pochta.kz", "boss@pochta.kz", null), // phone missing   -> pending
                company("Полный комплект", "full.kz", "info@full.kz", "+7 701 111 22 33"), // nothing missing
                company("Без сайта", null, null, null)));                    // nothing to visit

        List<String> names = pendingNames(50);

        assertThat(names).containsExactlyInAnyOrder("Ромашка", "Только почта");
    }

    @Test
    void pendingRespectsTheLimit() {
        ingest(List.of(
                company("Первая", "first.kz", null, null),
                company("Вторая", "second.kz", null, null),
                company("Третья", "third.kz", null, null)));

        assertThat(pendingNames(2)).hasSize(2);
    }

    @Test
    void pendingCarriesWhatTheWorkerNeedsToKnow() {
        ingest(List.of(company("Только почта", "pochta.kz", "boss@pochta.kz", null)));

        PendingEnrichCompany pending = pending(50).get(0);

        assertThat(pending.getId()).isNotNull();
        assertThat(pending.getDomain()).isEqualTo("pochta.kz");
        assertThat(pending.getEmail()).isEqualTo("boss@pochta.kz");
        assertThat(pending.getPhone()).isNull();
    }

    // --- PATCH /{id}/contacts --------------------------------------------------

    @Test
    void patchFillsOnlyEmptyFieldsAndNeverOverwritesTheSource() {
        ingest(List.of(company("Только почта", "pochta.kz", "boss@pochta.kz", null)));
        UUID id = idOf("Только почта");

        ResponseEntity<ContactsPatchResponse> response = patch(id, ContactsPatchRequest.builder()
                .email("info@pochta.kz")
                .phone("+7 916 123 45 67")
                .enrichNotes("email с /contacts; телефон с главной")
                .build());

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody().getFilled()).containsExactly("phone");
        assertThat(response.getBody().getEmail()).isEqualTo("boss@pochta.kz");

        Company stored = companyRepository.findById(id).orElseThrow();
        assertThat(stored.getEmail()).isEqualTo("boss@pochta.kz");
        assertThat(stored.getPhone()).isEqualTo("+79161234567");
        assertThat(stored.getRaw()).containsEntry("primary_phone_type", "MOBILE");
        assertThat(stored.getRaw()).containsEntry("enrich_attempted", true);
        assertThat(stored.getRaw()).containsEntry("enrich_filled", List.of("phone"));
    }

    @Test
    void anAttemptedCompanyLeavesThePendingListEvenWhenNothingWasFound() {
        ingest(List.of(company("Ромашка", "romashka.kz", null, null)));
        UUID id = idOf("Ромашка");
        assertThat(pendingNames(50)).contains("Ромашка");

        ResponseEntity<ContactsPatchResponse> response = patch(id, ContactsPatchRequest.builder()
                .enrichNotes("контакты не найдены (страниц просмотрено: 3)")
                .build());

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody().getFilled()).isEmpty();
        assertThat(pendingNames(50)).doesNotContain("Ромашка");
        Company stored = companyRepository.findById(id).orElseThrow();
        assertThat(stored.getRaw()).containsKey("enrich_at");
        assertThat(stored.getRaw()).containsEntry("enrich_notes", "контакты не найдены (страниц просмотрено: 3)");
    }

    @Test
    void patchRescoresTheLead() {
        ingest(List.of(company("Ромашка", "romashka.kz", null, null)));
        UUID id = idOf("Ромашка");
        assertThat(topLeadScore()).isZero(); // has a site, HH is not direct intent, no contact

        ResponseEntity<ContactsPatchResponse> response = patch(id, ContactsPatchRequest.builder()
                .phone("+7 916 123 45 67")
                .build());

        // contact & geo (10) + mobile (15) = 25 - and the stored lead agrees.
        assertThat(response.getBody().getScore()).isEqualTo(25);
        assertThat(response.getBody().getHotReason()).contains("есть контакт+гео");
        assertThat(topLeadScore()).isEqualTo(25);
        assertThat(leadRepository.count()).isEqualTo(1);
    }

    @Test
    void patchForAnUnknownCompanyIs404AndCreatesNothing() {
        ResponseEntity<String> response = restTemplate.exchange(
                url("/api/v1/companies/" + UUID.randomUUID() + "/contacts"), HttpMethod.PATCH,
                new HttpEntity<>(ContactsPatchRequest.builder().email("info@x.kz").build()), String.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(companyRepository.count()).isZero();
    }

    @Test
    void anInvalidEmailIsRejectedWith400AndChangesNothing() {
        ingest(List.of(company("Ромашка", "romashka.kz", null, null)));
        UUID id = idOf("Ромашка");

        ResponseEntity<Map> response = restTemplate.exchange(
                url("/api/v1/companies/" + id + "/contacts"), HttpMethod.PATCH,
                new HttpEntity<>(ContactsPatchRequest.builder().email("not an email").build()), Map.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(response.getBody()).containsEntry("error", "validation_failed");
        // Rejected outright: not even marked as attempted.
        assertThat(pendingNames(50)).contains("Ромашка");
    }

    // --- helpers -------------------------------------------------------------

    private static RawCompanyRequest company(String name, String domain, String email, String phone) {
        return RawCompanyRequest.builder()
                .name(name)
                .domain(domain)
                .email(email)
                .phone(phone)
                .city("Алматы")
                .source(LeadSource.HH)
                .hasSite(domain != null)
                .build();
    }

    private void ingest(List<RawCompanyRequest> batch) {
        ResponseEntity<Object> response = restTemplate.postForEntity(url("/api/v1/companies/ingest"), batch, Object.class);
        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
    }

    private List<PendingEnrichCompany> pending(int limit) {
        ResponseEntity<PendingEnrichCompany[]> response = restTemplate.getForEntity(
                url("/api/v1/companies/pending-enrich?limit=" + limit), PendingEnrichCompany[].class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        return List.of(response.getBody());
    }

    private List<String> pendingNames(int limit) {
        return pending(limit).stream().map(PendingEnrichCompany::getName).toList();
    }

    private UUID idOf(String name) {
        return companyRepository.findAll().stream()
                .filter(c -> c.getName().equals(name))
                .findFirst()
                .orElseThrow()
                .getId();
    }

    private ResponseEntity<ContactsPatchResponse> patch(UUID id, ContactsPatchRequest body) {
        return restTemplate.exchange(url("/api/v1/companies/" + id + "/contacts"), HttpMethod.PATCH,
                new HttpEntity<>(body), ContactsPatchResponse.class);
    }

    private int topLeadScore() {
        LeadResponse[] leads = restTemplate.getForObject(url("/api/v1/leads/top?limit=5"), LeadResponse[].class);
        return leads[0].getScore();
    }

    private String url(String path) {
        return "http://localhost:" + port + path;
    }
}
