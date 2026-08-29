package kz.qorsa.leadgen.repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.LeadSource;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface CompanyRepository extends JpaRepository<Company, UUID> {

    Optional<Company> findFirstByNormalizedDomain(String normalizedDomain);

    Optional<Company> findFirstByNormalizedPhone(String normalizedPhone);

    List<Company> findByCity(String city);

    long countBySource(LeadSource source);

    /**
     * Deletes every company from the given source. Dependent leads (and their
     * outreach rows) must be gone first - see
     * {@link LeadRepository#deleteByCompanySource}.
     */
    @Modifying
    @Query("delete from Company c where c.source = :source")
    int deleteBySource(@Param("source") LeadSource source);
}
