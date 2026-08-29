package kz.qorsa.leadgen.web;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Map;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.web.dto.IngestResponse;
import kz.qorsa.leadgen.web.dto.LeadResponse;
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
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * End-to-end test of the ingest network boundary: POST a batch containing a
 * duplicate straight to /api/v1/companies/ingest against a real Postgres
 * (via Testcontainers, schema created by the real Flyway migration) and
 * verify dedup + scoring produced the expected result.
 */
@Testcontainers
@SpringBootTest(webEnvironment = WebEnvironment.RANDOM_PORT)
class IngestControllerIT {

    @Container
    static PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16")
            .withDatabaseName("qorsa_leadgen_test")
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

    /**
     * The container is shared by every test in this class, so each test clears
     * the tables first - otherwise assertions about total lead counts would
     * depend on which test happened to run before it.
     */
    @BeforeEach
    void resetDatabase() {
        leadRepository.deleteAll();
        companyRepository.deleteAll();
    }

    @Test
    void ingestBatchWithDuplicateMergesAndScoresCorrectly() {
        RawCompanyRequest original = RawCompanyRequest.builder()
                .name("Кофейня Ромашка")
                .city("Almaty")
                .source(LeadSource.DEMO)
                .hasSite(false)
                .raw(Map.of("competitorNegativeReview", true))
                .build();

        // Fuzzy-name duplicate of `original` (same city, near-identical name),
        // arrives with a phone number the first record didn't have.
        RawCompanyRequest duplicate = RawCompanyRequest.builder()
                .name("ООО Ромашка Кофейня")
                .city("Almaty")
                .phone("+7 701 111 22 33")
                .source(LeadSource.GOOGLE_MAPS)
                .hasSite(false)
                .build();

        RawCompanyRequest unrelated = RawCompanyRequest.builder()
                .name("Строй Мастер")
                .city("Shymkent")
                .source(LeadSource.TELEGRAM_ORDER)
                .hasSite(false)
                .raw(Map.of("budgetMentioned", true))
                .build();

        List<RawCompanyRequest> batch = List.of(original, duplicate, unrelated);

        ResponseEntity<IngestResponse> response = restTemplate.postForEntity(
                url("/api/v1/companies/ingest"), batch, IngestResponse.class);

        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
        IngestResponse body = response.getBody();
        assertThat(body).isNotNull();

        // `original` and `unrelated` are new companies; `duplicate` merges into
        // `original`. created/merged are per batch item, so they still sum to 3.
        assertThat(body.getCreated()).isEqualTo(2);
        assertThat(body.getMerged()).isEqualTo(1);

        // ...but only TWO distinct leads exist after dedup: the merged Ромашка
        // and the unrelated Строй Мастер. `original` and `duplicate` both score
        // the same lead, and it is counted once - not once per batch item.
        assertThat(body.getLeadsScored()).isEqualTo(2);

        // Both of those distinct leads end the batch HOT, each judged on its
        // FINAL score: Ромашка at 80 (no site 40 + competitor review 30 +
        // contact&geo 10, the phone having arrived via the merged duplicate)
        // and Строй Мастер at 95 (no site 40 + direct intent 35 + budget 20).
        assertThat(body.getHotCount()).isEqualTo(2);
        assertThat(body.getHotCount()).isLessThanOrEqualTo(body.getLeadsScored());

        ResponseEntity<LeadResponse[]> topResponse = restTemplate.exchange(
                url("/api/v1/leads/top?limit=20"), HttpMethod.GET, HttpEntity.EMPTY, LeadResponse[].class);
        assertThat(topResponse.getStatusCode().is2xxSuccessful()).isTrue();
        List<LeadResponse> leads = List.of(topResponse.getBody());

        // Only 2 leads should exist: the merged Ромашка and the unrelated Строй Мастер.
        assertThat(leads).hasSize(2);

        LeadResponse topLead = leads.get(0);
        assertThat(leads).isSortedAccordingTo((a, b) -> Integer.compare(b.getScore(), a.getScore()));

        LeadResponse romashka = leads.stream()
                .filter(l -> l.getCompanyName().contains("Ромашка"))
                .findFirst()
                .orElseThrow();
        // no site (40) + competitor review (30) + contact & geo, phone came from the
        // merged duplicate (10) = 80.
        assertThat(romashka.getScore()).isEqualTo(80);
        assertThat(romashka.getPhone()).isNotBlank();
        assertThat(topLead).isNotNull();
    }

    /**
     * Regression test for the metric that used to over-report: a batch of five
     * sightings of the SAME business must report one lead, not five.
     *
     * <p>This is the shape real worker traffic has - a 2GIS/OSM sweep re-finds
     * the same company under slightly different names all day - and the old
     * per-batch-item counting made a single hot lead look like five.
     */
    @Test
    void repeatedSightingsOfOneBusinessCountAsOneLead() {
        List<RawCompanyRequest> batch = List.of(
                sighting("Кофейня Ромашка", LeadSource.GOOGLE_MAPS),
                sighting("ООО Ромашка Кофейня", LeadSource.TWOGIS),
                sighting("Кофейня  Ромашка", LeadSource.OSM),
                sighting("ТОО Ромашка Кофейня", LeadSource.YANDEX_REVIEW),
                sighting("Ромашка Кофейня", LeadSource.GOOGLE_MAPS));

        ResponseEntity<IngestResponse> response = restTemplate.postForEntity(
                url("/api/v1/companies/ingest"), batch, IngestResponse.class);

        IngestResponse body = response.getBody();
        assertThat(body).isNotNull();

        // All five are per-item accounted for: one created, four merged.
        assertThat(body.getCreated()).isEqualTo(1);
        assertThat(body.getMerged()).isEqualTo(4);
        assertThat(body.getCreated() + body.getMerged()).isEqualTo(batch.size());

        // But they are ONE business, so exactly one lead was scored - the old
        // behaviour reported 5 here.
        assertThat(body.getLeadsScored()).isEqualTo(1);
        assertThat(body.getHotCount()).isLessThanOrEqualTo(1);

        // ...and the database agrees, which is the real check.
        assertThat(leadRepository.count()).isEqualTo(1);
        assertThat(body.getLeadsScored()).isEqualTo((int) leadRepository.count());
    }

    private static RawCompanyRequest sighting(String name, LeadSource source) {
        return RawCompanyRequest.builder()
                .name(name)
                .city("Almaty")
                .source(source)
                .hasSite(false)
                .build();
    }

    private String url(String path) {
        return "http://localhost:" + port + path;
    }
}
