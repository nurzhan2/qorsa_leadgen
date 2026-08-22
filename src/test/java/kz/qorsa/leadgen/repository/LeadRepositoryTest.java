package kz.qorsa.leadgen.repository;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.domain.LeadStatus;
import org.hibernate.LazyInitializationException;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.autoconfigure.orm.jpa.TestEntityManager;
import org.springframework.data.domain.PageRequest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Proves the fix for the LazyInitializationException that used to blow up
 * LeadController.top() / DemoSeeder's post-seed log: Lead -&gt; Company is
 * LAZY, so once the entity is detached (session gone, e.g. after the request
 * finishes) touching lead.getCompany().getXxx() on a plain-loaded Lead
 * throws. findTopWithCompany/findByStatusWithCompany fetch-join the company
 * in the same query, so it's a real initialized instance - not a proxy -
 * and stays readable after detachment.
 *
 * <p>Uses a real Postgres (Testcontainers) rather than an in-memory
 * database because the schema relies on Postgres-specific jsonb columns.
 */
@Testcontainers
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
class LeadRepositoryTest {

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

    @Autowired
    private TestEntityManager entityManager;

    @Autowired
    private LeadRepository leadRepository;

    @Test
    void plainLoad_throwsLazyInitializationExceptionOnDetachedCompanyAccess() {
        Lead lead = persistCompanyAndLead("Кофейня Ромашка", "Almaty");
        entityManager.clear();

        Lead fetched = leadRepository.findById(lead.getId()).orElseThrow();
        entityManager.clear(); // detach - simulates the session being gone by response time

        assertThatThrownBy(() -> fetched.getCompany().getName())
                .isInstanceOf(LazyInitializationException.class);
    }

    @Test
    void findTopWithCompany_keepsCompanyReadableAfterDetach() {
        persistCompanyAndLead("Кофейня Ромашка", "Almaty");
        entityManager.clear();

        List<Lead> topLeads = leadRepository.findTopWithCompany(PageRequest.of(0, 5));
        entityManager.clear(); // detach the fetched result too - proves company is a real instance, not a proxy

        assertThat(topLeads).hasSize(1);
        Lead fetched = topLeads.get(0);
        assertThat(fetched.getCompany().getName()).isEqualTo("Кофейня Ромашка");
        assertThat(fetched.getCompany().getCity()).isEqualTo("Almaty");
    }

    @Test
    void findByStatusWithCompany_keepsCompanyReadableAfterDetach() {
        persistCompanyAndLead("Sunrise Auto", "Astana");
        entityManager.clear();

        List<Lead> hotLeads = leadRepository.findByStatusWithCompany(LeadStatus.HOT);
        entityManager.clear();

        assertThat(hotLeads).hasSize(1);
        assertThat(hotLeads.get(0).getCompany().getName()).isEqualTo("Sunrise Auto");
    }

    private Lead persistCompanyAndLead(String companyName, String city) {
        Company company = Company.builder()
                .name(companyName)
                .city(city)
                .source(LeadSource.DEMO)
                .hasSite(false)
                .build();
        entityManager.persist(company);

        Lead lead = Lead.builder()
                .company(company)
                .score(80)
                .status(LeadStatus.HOT)
                .build();
        entityManager.persist(lead);

        entityManager.flush();
        return lead;
    }
}
