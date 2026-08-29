package kz.qorsa.leadgen.repository;

import java.util.UUID;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.domain.Outreach;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface OutreachRepository extends JpaRepository<Outreach, UUID> {

    /**
     * Deletes every outreach row belonging to a lead of a company from the
     * given source. Must run BEFORE the leads themselves are deleted -
     * outreach.lead_id is a NOT NULL foreign key (V1__init.sql) with no
     * ON DELETE CASCADE, so removing leads first would fail.
     *
     * <p>Written as a subquery rather than a join because JPQL bulk DELETE
     * statements cannot join.
     */
    @Modifying
    @Query("delete from Outreach o where o.lead in "
            + "(select l from Lead l where l.company in (select c from Company c where c.source = :source))")
    int deleteByCompanySource(@Param("source") LeadSource source);
}
