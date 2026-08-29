package kz.qorsa.leadgen.repository;

import java.util.List;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.domain.LeadStatus;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface LeadRepository extends JpaRepository<Lead, UUID> {

    List<Lead> findByCompanyId(UUID companyId);

    /**
     * Top leads by score, with {@code company} fetched in the same query.
     * The Lead -&gt; Company association is LAZY (avoids N+1 elsewhere), so
     * anything that reads company fields after the session closes - e.g.
     * LeadController's response mapping, or DemoSeeder's post-ingest log -
     * must go through a fetch-joined query like this one instead of
     * touching lead.getCompany() outside a transaction.
     */
    @Query("select l from Lead l join fetch l.company order by l.score desc")
    List<Lead> findTopWithCompany(Pageable pageable);

    /** Same fetch-join guarantee as {@link #findTopWithCompany}, filtered by status. */
    @Query("select l from Lead l join fetch l.company where l.status = :status order by l.score desc")
    List<Lead> findByStatusWithCompany(@Param("status") LeadStatus status);

    /** All leads with company fetched - used by the Sheets export, which needs the full set. */
    @Query("select l from Lead l join fetch l.company order by l.score desc")
    List<Lead> findAllWithCompany();

    /**
     * Deletes every lead belonging to a company from the given source. Any
     * outreach rows must be gone first (see
     * {@link OutreachRepository#deleteByCompanySource}) - leads.company_id and
     * outreach.lead_id are plain NOT NULL foreign keys with no ON DELETE
     * CASCADE. Written as a subquery because JPQL bulk DELETE cannot join.
     */
    @Modifying
    @Query("delete from Lead l where l.company in (select c from Company c where c.source = :source)")
    int deleteByCompanySource(@Param("source") LeadSource source);
}
