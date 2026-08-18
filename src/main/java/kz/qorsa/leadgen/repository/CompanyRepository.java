package kz.qorsa.leadgen.repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import org.springframework.data.jpa.repository.JpaRepository;

public interface CompanyRepository extends JpaRepository<Company, UUID> {

    Optional<Company> findFirstByNormalizedDomain(String normalizedDomain);

    Optional<Company> findFirstByNormalizedPhone(String normalizedPhone);

    List<Company> findByCity(String city);
}
