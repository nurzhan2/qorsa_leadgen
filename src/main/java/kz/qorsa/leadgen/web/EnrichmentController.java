package kz.qorsa.leadgen.web;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.ConstraintViolationException;
import jakarta.validation.Validator;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.service.ContactEnrichmentService;
import kz.qorsa.leadgen.service.ContactEnrichmentService.EnrichmentResult;
import kz.qorsa.leadgen.web.dto.ContactsPatchRequest;
import kz.qorsa.leadgen.web.dto.ContactsPatchResponse;
import kz.qorsa.leadgen.web.dto.PendingEnrichCompany;
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
 * The contact-enrichment boundary (workers/enrich): hands out companies whose
 * website hasn't been read for contacts yet, and takes back whatever the
 * worker found there.
 *
 * <p>Kept apart from {@link IngestController} on purpose: ingest creates or
 * merges companies from a source's own data, while this only ever fills
 * blanks on a company that already exists and never creates one.
 */
@RestController
@RequestMapping("/api/v1/companies")
public class EnrichmentController {

    private final ContactEnrichmentService enrichmentService;
    private final Validator validator;

    public EnrichmentController(ContactEnrichmentService enrichmentService, Validator validator) {
        this.enrichmentService = enrichmentService;
        this.validator = validator;
    }

    /** Companies with a domain, a missing email or phone, and no enrichment attempt yet - hottest first. */
    @GetMapping("/pending-enrich")
    public List<PendingEnrichCompany> pendingEnrich(@RequestParam(defaultValue = "50") int limit) {
        return enrichmentService.findPending(limit).stream()
                .map(EnrichmentController::toPending)
                .toList();
    }

    /**
     * Fills only the company's EMPTY contact fields, always marks it as
     * attempted (even for an empty body), and re-scores its lead. 404 for an
     * unknown id, 400 with a field breakdown for an invalid body (same shape
     * as ingest - see {@link GlobalExceptionHandler}).
     */
    @PatchMapping("/{id}/contacts")
    public ResponseEntity<ContactsPatchResponse> patchContacts(@PathVariable UUID id,
                                                               @RequestBody ContactsPatchRequest body) {
        Set<ConstraintViolation<ContactsPatchRequest>> violations = validator.validate(body);
        if (!violations.isEmpty()) {
            throw new ConstraintViolationException(violations);
        }
        return enrichmentService.applyContacts(id, body)
                .map(result -> ResponseEntity.ok(toResponse(result)))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Company " + id + " not found"));
    }

    private static PendingEnrichCompany toPending(Company company) {
        return PendingEnrichCompany.builder()
                .id(company.getId())
                .name(company.getName())
                .domain(company.getDomain())
                .email(company.getEmail())
                .phone(company.getPhone())
                .messenger(company.getMessenger())
                .city(company.getCity())
                .build();
    }

    private static ContactsPatchResponse toResponse(EnrichmentResult result) {
        Company company = result.company();
        return ContactsPatchResponse.builder()
                .companyId(company.getId())
                .filled(result.filled())
                .email(company.getEmail())
                .phone(company.getPhone())
                .messenger(company.getMessenger())
                .score(result.lead().getScore())
                .status(result.lead().getStatus())
                .hotReason(result.lead().getHotReason())
                .build();
    }
}
