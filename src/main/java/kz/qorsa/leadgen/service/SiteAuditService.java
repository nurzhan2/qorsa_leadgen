package kz.qorsa.leadgen.service;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.web.dto.AuditPatchRequest;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Stores what workers/site_audit measured about a company's website and
 * re-scores the lead.
 *
 * <p>This is the missing half of the "has a business, has a bad site" thesis.
 * Without it, an OSM company that owns a site collects at most 25 points -
 * {@code нет сайта +40} doesn't apply to it, OSM carries no direct intent, and
 * the two rules written for exactly this case sat as no-ops. Every one of
 * those companies was permanently cold regardless of how bad its site was.
 *
 * <p>Three rules carry the weight:
 * <ul>
 *   <li><b>Measurements overwrite.</b> Unlike contact enrichment, a newer
 *   measurement replaces an older one - a site gets rebuilt, and last month's
 *   PageSpeed score is then simply wrong. There is no provenance argument for
 *   keeping a stale number.</li>
 *   <li><b>One attempt per company.</b> {@code raw.audit_attempted=true} is
 *   written on EVERY call, including one reporting an unreachable site, so
 *   {@link #findPending} never hands the same dead domain out twice. Clear the
 *   flag by hand to re-measure.</li>
 *   <li><b>Absent is not zero.</b> A null pagespeed is stored as absent, never
 *   as 0. {@link ScoringService} fires its rule on "measured and bad", and a
 *   site nobody could reach must not look like the slowest site in the
 *   database.</li>
 * </ul>
 */
@Service
@Slf4j
public class SiteAuditService {

    public static final String RAW_AUDIT_ATTEMPTED = "audit_attempted";
    public static final String RAW_AUDIT_AT = "audit_at";
    public static final String RAW_PAGESPEED = "pagespeed";
    public static final String RAW_AUDIT_FAILS = "auditFails";
    public static final String RAW_FAILED_CHECKS = "audit_failed_checks";
    public static final String RAW_AUDIT_NOTES = "audit_notes";

    /** Upper bound for one pending-audit page, whatever the caller asks for. */
    static final int MAX_PENDING_LIMIT = 500;

    private final CompanyRepository companyRepository;
    private final IngestService ingestService;

    public SiteAuditService(CompanyRepository companyRepository, IngestService ingestService) {
        this.companyRepository = companyRepository;
        this.ingestService = ingestService;
    }

    /** See {@link CompanyRepository#findPendingAudit}; {@code limit} is clamped to 1..MAX_PENDING_LIMIT. */
    public List<Company> findPending(int limit) {
        int clamped = Math.max(1, Math.min(limit, MAX_PENDING_LIMIT));
        return companyRepository.findPendingAudit(clamped);
    }

    /**
     * Applies one audit report. Empty when no company has this id - an audit
     * only ever updates an existing company, it never creates one.
     */
    @Transactional
    public Optional<AuditResult> applyAudit(UUID companyId, AuditPatchRequest patch) {
        Optional<Company> found = companyRepository.findById(companyId);
        if (found.isEmpty()) {
            return Optional.empty();
        }
        Company company = found.get();
        Map<String, Object> raw = company.getRaw() != null ? new HashMap<>(company.getRaw()) : new HashMap<>();

        if (patch.getPagespeed() != null) {
            raw.put(RAW_PAGESPEED, patch.getPagespeed());
        }
        if (patch.getAuditFails() != null) {
            raw.put(RAW_AUDIT_FAILS, patch.getAuditFails());
        }
        if (patch.getFailedChecks() != null && !patch.getFailedChecks().isEmpty()) {
            raw.put(RAW_FAILED_CHECKS, new ArrayList<>(patch.getFailedChecks()));
        }
        if (patch.getAuditNotes() != null && !patch.getAuditNotes().isBlank()) {
            raw.put(RAW_AUDIT_NOTES, patch.getAuditNotes().trim());
        }

        raw.put(RAW_AUDIT_ATTEMPTED, true);
        raw.put(RAW_AUDIT_AT, Instant.now().toString());
        // A fresh map rather than in-place mutation, so Hibernate always sees
        // the JSONB column as changed.
        company.setRaw(raw);

        Company saved = companyRepository.save(company);
        Lead lead = ingestService.rescore(saved);

        log.info("Site audited: company={}, pagespeed={}, auditFails={}, score={}",
                saved.getId(), patch.getPagespeed(), patch.getAuditFails(), lead.getScore());
        return Optional.of(new AuditResult(saved, lead));
    }

    public record AuditResult(Company company, Lead lead) {
    }
}
