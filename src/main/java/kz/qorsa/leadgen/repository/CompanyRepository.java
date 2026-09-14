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
     * Companies worth a contact-enrichment pass (workers/enrich): they have a
     * domain to visit, are still missing an email or a phone, and have never
     * been attempted. {@code raw.enrich_attempted} is written after EVERY
     * attempt, successful or not (see ContactEnrichmentService), so a site
     * that yields nothing is not handed out again on every run.
     *
     * <p>Native because the flag lives in the JSONB {@code raw} column. Hottest
     * leads first, so a truncated run spends its requests where a contact is
     * worth the most; oldest first among equals.
     */
    @Query(value = """
            select c.* from companies c
            where c.domain is not null and btrim(c.domain) <> ''
              and (c.email is null or btrim(c.email) = '' or c.phone is null or btrim(c.phone) = '')
              and coalesce(c.raw ->> 'enrich_attempted', 'false') <> 'true'
            order by (select max(l.score) from leads l where l.company_id = c.id) desc nulls last,
                     c.created_at asc
            limit :limit
            """, nativeQuery = true)
    List<Company> findPendingEnrichment(@Param("limit") int limit);

    /**
     * Companies worth a site-audit pass (workers/site_audit): they have a
     * domain, and no audit has been attempted yet. {@code raw.audit_attempted}
     * is written after EVERY attempt - including one where the site was
     * unreachable - so a dead domain is not re-measured on every run.
     *
     * <p>Unlike enrichment, this deliberately does NOT require missing
     * contacts: a company with a full contact card and a broken site is
     * exactly the lead worth finding. Ordered the same way, hottest first.
     */
    @Query(value = """
            select c.* from companies c
            where c.domain is not null and btrim(c.domain) <> ''
              and coalesce(c.raw ->> 'audit_attempted', 'false') <> 'true'
            order by (select max(l.score) from leads l where l.company_id = c.id) desc nulls last,
                     c.created_at asc
            limit :limit
            """, nativeQuery = true)
    List<Company> findPendingAudit(@Param("limit") int limit);

    /**
     * Deletes every company from the given source. Dependent leads (and their
     * outreach rows) must be gone first - see
     * {@link LeadRepository#deleteByCompanySource}.
     */
    @Modifying
    @Query("delete from Company c where c.source = :source")
    int deleteBySource(@Param("source") LeadSource source);
}
