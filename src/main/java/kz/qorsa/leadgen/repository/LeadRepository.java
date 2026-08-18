package kz.qorsa.leadgen.repository;

import java.util.List;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadStatus;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface LeadRepository extends JpaRepository<Lead, UUID> {

    List<Lead> findByCompanyId(UUID companyId);

    List<Lead> findAllByOrderByScoreDesc(Pageable pageable);

    List<Lead> findByStatusOrderByScoreDesc(LeadStatus status);
}
