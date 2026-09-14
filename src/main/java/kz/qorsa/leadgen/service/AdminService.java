package kz.qorsa.leadgen.service;

import java.util.List;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.domain.LeadStatus;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.repository.OutreachRepository;
import kz.qorsa.leadgen.web.dto.DemoDataDeletionResponse;
import kz.qorsa.leadgen.web.dto.RescoreResponse;
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
    private final IngestService ingestService;

    public AdminService(CompanyRepository companyRepository,
                        LeadRepository leadRepository,
                        OutreachRepository outreachRepository,
                        IngestService ingestService) {
        this.companyRepository = companyRepository;
        this.leadRepository = leadRepository;
        this.outreachRepository = outreachRepository;
        this.ingestService = ingestService;
    }

    /**
     * Re-runs scoring over every stored company and refreshes its lead.
     *
     * <p>Scores are written once, at ingest. That is the right default - it
     * keeps ingest cheap and the numbers stable - but it means a change to the
     * weights in application.yml or to the rules in {@link ScoringService}
     * only affects companies ingested afterwards, while everything already in
     * the table keeps a score computed under the old model. This endpoint is
     * how the back catalogue catches up.
     *
     * <p>Run it after any scoring change. It is idempotent: running it twice
     * over an unchanged model produces identical scores.
     */
    @Transactional
    public RescoreResponse rescoreAll() {
        List<Company> companies = companyRepository.findAll();
        for (Company company : companies) {
            ingestService.rescore(company);
        }

        long hot = leadRepository.countByStatus(LeadStatus.HOT);
        long qualified = leadRepository.countByStatus(LeadStatus.QUALIFIED);

        log.warn("Rescored {} companies: hot={}, qualified={}", companies.size(), hot, qualified);

        return RescoreResponse.builder()
                .rescored(companies.size())
                .hot(hot)
                .qualified(qualified)
                .cold(companies.size() - hot - qualified)
                .build();
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
