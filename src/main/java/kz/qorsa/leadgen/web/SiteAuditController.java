package kz.qorsa.leadgen.web;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.ConstraintViolationException;
import jakarta.validation.Validator;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.service.SiteAuditService;
import kz.qorsa.leadgen.service.SiteAuditService.AuditResult;
import kz.qorsa.leadgen.web.dto.AuditPatchRequest;
import kz.qorsa.leadgen.web.dto.AuditPatchResponse;
import kz.qorsa.leadgen.web.dto.PendingAuditCompany;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * The site-audit boundary (workers/site_audit): hands out companies whose
 * website hasn't been measured yet, and takes back the measurements.
 *
 * <p>Separate from {@link EnrichmentController} because the two passes want
 * different companies. Enrichment wants a company whose contacts are missing;
 * an audit wants any company with a domain, contacts or not - a lead with a
 * full contact card and a broken site is the single best kind this scoring
 * model can find.
 */
@RestController
@RequestMapping("/api/v1/companies")
public class SiteAuditController {

    private final SiteAuditService auditService;
    private final Validator validator;

    public SiteAuditController(SiteAuditService auditService, Validator validator) {
        this.auditService = auditService;
        this.validator = validator;
    }

    /** Companies with a domain and no audit attempt yet - hottest first. */
    @GetMapping("/pending-audit")
    public List<PendingAuditCompany> pendingAudit(@RequestParam(defaultValue = "50") int limit) {
        return auditService.findPending(limit).stream()
                .map(SiteAuditController::toPending)
                .toList();
    }

    /**
     * Stores one audit report and re-scores the lead. Always marks the company
     * as attempted, even for a report carrying no measurements (an unreachable
     * site). 404 for an unknown id, 400 with a field breakdown for an invalid
     * body - same shape as ingest, see {@link GlobalExceptionHandler}.
     */
    @PatchMapping("/{id}/audit")
    public ResponseEntity<AuditPatchResponse> patchAudit(@PathVariable UUID id,
                                                         @RequestBody AuditPatchRequest body) {
        Set<ConstraintViolation<AuditPatchRequest>> violations = validator.validate(body);
        if (!violations.isEmpty()) {
            throw new ConstraintViolationException(violations);
        }
        return auditService.applyAudit(id, body)
                .map(result -> ResponseEntity.ok(toResponse(result)))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Company " + id + " not found"));
    }

    private static PendingAuditCompany toPending(Company company) {
        return PendingAuditCompany.builder()
                .id(company.getId())
                .name(company.getName())
                .domain(company.getDomain())
                .city(company.getCity())
                .build();
    }

    private static AuditPatchResponse toResponse(AuditResult result) {
        Company company = result.company();
        return AuditPatchResponse.builder()
                .companyId(company.getId())
                .pagespeed(intFromRaw(company, SiteAuditService.RAW_PAGESPEED))
                .auditFails(intFromRaw(company, SiteAuditService.RAW_AUDIT_FAILS))
                .score(result.lead().getScore())
                .status(result.lead().getStatus())
                .hotReason(result.lead().getHotReason())
                .build();
    }

    private static Integer intFromRaw(Company company, String key) {
        if (company.getRaw() == null) {
            return null;
        }
        Object value = company.getRaw().get(key);
        return value instanceof Number number ? number.intValue() : null;
    }
}
