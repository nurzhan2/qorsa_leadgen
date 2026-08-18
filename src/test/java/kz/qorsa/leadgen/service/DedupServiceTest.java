package kz.qorsa.leadgen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.Optional;
import kz.qorsa.leadgen.config.ScoringProperties;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.repository.CompanyRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

/**
 * Pure unit tests for {@link DedupService} - the repository is mocked, no
 * database involved.
 */
@ExtendWith(MockitoExtension.class)
class DedupServiceTest {

    @Mock
    private CompanyRepository companyRepository;

    private DedupService dedupService;

    @BeforeEach
    void setUp() {
        ScoringProperties properties = new ScoringProperties();
        properties.setFuzzyNameThreshold(82);
        dedupService = new DedupService(companyRepository, properties);
    }

    @Test
    void findsExistingCompanyByExactNormalizedDomain() {
        Company existing = Company.builder().name("Acme").normalizedDomain("acme.kz").build();
        when(companyRepository.findFirstByNormalizedDomain("acme.kz")).thenReturn(Optional.of(existing));

        Company candidate = Company.builder()
                .name("Acme LLC")
                .normalizedDomain("acme.kz")
                .normalizedPhone("7001112233")
                .build();

        Optional<Company> result = dedupService.findExisting(candidate);

        assertThat(result).contains(existing);
        // Domain match short-circuits - phone/fuzzy lookups must not even run.
        verify(companyRepository).findFirstByNormalizedDomain("acme.kz");
        verifyNoMoreInteractions(companyRepository);
    }

    @Test
    void findsExistingCompanyByExactNormalizedPhoneWhenNoDomainMatch() {
        Company existing = Company.builder().name("Acme").normalizedPhone("7001112233").build();
        when(companyRepository.findFirstByNormalizedDomain(any())).thenReturn(Optional.empty());
        when(companyRepository.findFirstByNormalizedPhone("7001112233")).thenReturn(Optional.of(existing));

        Company candidate = Company.builder()
                .name("Acme")
                .normalizedDomain("acme.kz")
                .normalizedPhone("7001112233")
                .build();

        Optional<Company> result = dedupService.findExisting(candidate);

        assertThat(result).contains(existing);
    }

    @Test
    void findsExistingCompanyByFuzzyNameWithinSameCity() {
        Company existing = Company.builder()
                .name("Кофейня Ромашка")
                .city("Almaty")
                .normalizedName("кофейня ромашка")
                .build();

        when(companyRepository.findByCity("Almaty")).thenReturn(List.of(existing));

        Company candidate = Company.builder()
                .name("ООО Ромашка Кофейня")
                .city("Almaty")
                .normalizedName("ромашка кофейня")
                .source(LeadSource.GOOGLE_MAPS)
                .build();

        Optional<Company> result = dedupService.findExisting(candidate);

        assertThat(result).contains(existing);
    }

    @Test
    void doesNotMatchFuzzyNameBelowThreshold() {
        Company existing = Company.builder()
                .name("Sunrise Auto")
                .city("Astana")
                .normalizedName("sunrise auto")
                .build();

        when(companyRepository.findByCity("Astana")).thenReturn(List.of(existing));

        Company candidate = Company.builder()
                .name("Bright Motors")
                .city("Astana")
                .normalizedName("bright motors")
                .build();

        Optional<Company> result = dedupService.findExisting(candidate);

        assertThat(result).isEmpty();
    }

    @Test
    void returnsEmptyWhenNothingMatchesAndNoCityForFuzzy() {
        Company candidate = Company.builder()
                .name("Nobody Corp")
                .normalizedName("nobody corp")
                .build();

        Optional<Company> result = dedupService.findExisting(candidate);

        assertThat(result).isEmpty();
    }
}
