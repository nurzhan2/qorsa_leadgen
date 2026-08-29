package kz.qorsa.leadgen.service;

import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.repository.OutreachRepository;
import kz.qorsa.leadgen.web.dto.DemoDataDeletionResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Destructive maintenance operations. Deliberately separate from the ingest
 * pipeline: nothing here is on the hot path, and nothing on the hot path calls
 * into here.
 *
 * <p>Access control is NOT this class's job - {@link kz.qorsa.leadgen.web.AdminController}
 * gates every entry point before it gets this far.
 */
@Service
@Slf4j
public class AdminService {

    private final CompanyRepository companyRepository;
    private final LeadRepository leadRepository;
    private final OutreachRepository outreachRepository;

    public AdminService(CompanyRepository companyRepository,
                        LeadRepository leadRepository,
                        OutreachRepository outreachRepository) {
        this.companyRepository = companyRepository;
        this.leadRepository = leadRepository;
        this.outreachRepository = outreachRepository;
    }

    /**
     * Removes every company whose source is {@link LeadSource#DEMO}, together
     * with the leads derived from them and any outreach sent against those
     * leads.
     *
     * <p>Deletion order is deliberate and load-bearing: outreach -&gt; leads -&gt;
     * companies. V1__init.sql declares plain NOT NULL foreign keys with no
     * ON DELETE CASCADE, so going the other way round fails on a constraint
     * violation. The whole thing runs in one transaction, so a failure part-way
     * leaves the database exactly as it was rather than half-cleaned.
     *
     * <p>Only DEMO rows are touched. Leads that a real worker produced are
     * never in scope, even if a demo company happened to dedup-merge with one -
     * in that case the surviving company row carries the source of whichever
     * record created it, and only a DEMO-sourced one is deleted here.
     */
    @Transactional
    public DemoDataDeletionResponse deleteDemoData() {
        int outreachDeleted = outreachRepository.deleteByCompanySource(LeadSource.DEMO);
        int leadsDeleted = leadRepository.deleteByCompanySource(LeadSource.DEMO);
        int companiesDeleted = companyRepository.deleteBySource(LeadSource.DEMO);

        log.warn("Demo data purged: companies={}, leads={}, outreach={}",
                companiesDeleted, leadsDeleted, outreachDeleted);

        return DemoDataDeletionResponse.builder()
                .deleted(companiesDeleted)
                .deletedLeads(leadsDeleted)
                .deletedOutreach(outreachDeleted)
                .build();
    }
}
