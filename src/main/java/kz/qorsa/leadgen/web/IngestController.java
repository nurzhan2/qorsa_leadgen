package kz.qorsa.leadgen.web;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.ConstraintViolationException;
import jakarta.validation.Validator;
import java.util.List;
import java.util.Set;
import kz.qorsa.leadgen.service.IngestService;
import kz.qorsa.leadgen.web.dto.IngestResponse;
import kz.qorsa.leadgen.web.dto.RawCompanyRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * The network boundary between this core engine and any scraper worker
 * (Telegram, Google Maps, 2GIS, Avito, ...). Workers are expected to live as
 * independent Python processes that POST here; this controller has no
 * knowledge of, or dependency on, where the data came from.
 *
 * <p>The request body is a bare JSON array of {@link RawCompanyRequest} (not
 * wrapped in an envelope) so a worker only ever needs to serialize a list of
 * dicts. Each element is validated explicitly here: plain {@code @Valid} on a
 * {@code List<T>} @RequestBody does not reliably cascade per Bean Validation
 * semantics, so we validate every element with the injected {@link Validator}
 * and fail the whole batch with 400 if any element is invalid.
 */
@RestController
@RequestMapping("/api/v1/companies")
public class IngestController {

    private final IngestService ingestService;
    private final Validator validator;

    public IngestController(IngestService ingestService, Validator validator) {
        this.ingestService = ingestService;
        this.validator = validator;
    }

    @PostMapping("/ingest")
    public ResponseEntity<IngestResponse> ingest(@RequestBody List<RawCompanyRequest> companies) {
        for (RawCompanyRequest company : companies) {
            Set<ConstraintViolation<RawCompanyRequest>> violations = validator.validate(company);
            if (!violations.isEmpty()) {
                throw new ConstraintViolationException(violations);
            }
        }
        return ResponseEntity.ok(ingestService.ingest(companies));
    }
}
