package kz.qorsa.leadgen.web;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Map;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.web.dto.DemoDataDeletionResponse;
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
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * End-to-end test of DELETE /api/v1/admin/demo-data against a real Postgres:
 * ingest a mix of DEMO and real-source companies, purge, and verify the DEMO
 * ones (and their leads) are gone while everything else is untouched.
 *
 * <p>Runs with {@code leadgen.admin.enabled=true}; the refusal path is a pure
 * decision and is covered without a container in {@link AdminControllerTest}.
 */
@Testcontainers
@SpringBootTest(
        webEnvironment = WebEnvironment.RANDOM_PORT,
        properties = "leadgen.admin.enabled=true")
class AdminControllerIT {

    @Container
    static PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16")
            .withDatabaseName("qorsa_leadgen_admin_test")
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
        leadRepository.deleteAll();
        companyRepository.deleteAll();
    }

    @Test
    void deletesOnlyDemoCompaniesAndTheirLeads() {
        ingest(List.of(
                demoCompany("Кофейня Ромашка", "+7 701 111 22 33"),
                demoCompany("Строй Мастер ТОО", "+7 702 333 44 55"),
                RawCompanyRequest.builder()
                        .name("Реальный Клиент")
                        .phone("+7 705 999 00 11")
                        .city("Astana")
                        .source(LeadSource.TELEGRAM_ORDER)
                        .hasSite(false)
                        .raw(Map.of("budgetMentioned", true))
                        .build()));

        assertThat(companyRepository.countBySource(LeadSource.DEMO)).isEqualTo(2);
        long totalBefore = companyRepository.count();

        ResponseEntity<DemoDataDeletionResponse> response = restTemplate.exchange(
                url("/api/v1/admin/demo-data"), HttpMethod.DELETE, HttpEntity.EMPTY,
                DemoDataDeletionResponse.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        DemoDataDeletionResponse body = response.getBody();
        assertThat(body).isNotNull();
        assertThat(body.getDeleted()).isEqualTo(2);
        assertThat(body.getDeletedLeads()).isEqualTo(2);

        // DEMO rows gone, the real lead untouched.
        assertThat(companyRepository.countBySource(LeadSource.DEMO)).isZero();
        assertThat(companyRepository.count()).isEqualTo(totalBefore - 2);
        assertThat(companyRepository.findAll())
                .extracting("name")
                .containsExactly("Реальный Клиент");
        // No lead was left orphaned behind a deleted company.
        assertThat(leadRepository.count()).isEqualTo(1);
    }

    @Test
    void purgingWhenThereIsNothingToPurgeIsANoOpNotAnError() {
        ingest(List.of(RawCompanyRequest.builder()
                .name("Только реальный")
                .phone("+7 707 123 45 67")
                .city("Almaty")
                .source(LeadSource.OSM)
                .hasSite(true)
                .build()));

        ResponseEntity<DemoDataDeletionResponse> response = restTemplate.exchange(
                url("/api/v1/admin/demo-data"), HttpMethod.DELETE, HttpEntity.EMPTY,
                DemoDataDeletionResponse.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody().getDeleted()).isZero();
        assertThat(response.getBody().getDeletedLeads()).isZero();
        assertThat(companyRepository.count()).isEqualTo(1);
    }

    @Test
    void isIdempotentAcrossRepeatedCalls() {
        ingest(List.of(demoCompany("Кофейня Ромашка", "+7 701 111 22 33")));

        DemoDataDeletionResponse first = purge();
        DemoDataDeletionResponse second = purge();

        assertThat(first.getDeleted()).isEqualTo(1);
        assertThat(second.getDeleted()).isZero();
        assertThat(companyRepository.countBySource(LeadSource.DEMO)).isZero();
    }

    private DemoDataDeletionResponse purge() {
        return restTemplate.exchange(
                url("/api/v1/admin/demo-data"), HttpMethod.DELETE, HttpEntity.EMPTY,
                DemoDataDeletionResponse.class).getBody();
    }

    private void ingest(List<RawCompanyRequest> batch) {
        restTemplate.postForEntity(url("/api/v1/companies/ingest"), batch, Object.class);
    }

    private static RawCompanyRequest demoCompany(String name, String phone) {
        return RawCompanyRequest.builder()
                .name(name)
                .phone(phone)
                .city("Almaty")
                .source(LeadSource.DEMO)
                .hasSite(false)
                .build();
    }

    private String url(String path) {
        return "http://localhost:" + port + path;
    }
}
