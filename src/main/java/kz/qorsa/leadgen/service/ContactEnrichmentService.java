package kz.qorsa.leadgen.service;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.web.dto.ContactsPatchRequest;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Fills in contacts that workers/enrich found on a company's own website.
 *
 * <p>Two rules carry the weight:
 * <ul>
 *   <li><b>Blanks only.</b> A value the source reported (2GIS, OSM, HH, ...) is
 *   never overwritten by one scraped from a website - the source's value is
 *   the one with known provenance. Only empty fields are filled, and
 *   {@code raw.enrich_filled} records which ones were.</li>
 *   <li><b>One attempt per company.</b> {@code raw.enrich_attempted=true} (plus
 *   {@code raw.enrich_at}) is written on EVERY call, whether or not anything
 *   was found, so {@link #findPending} never hands the same site out twice. A
 *   site with no public contacts today is not re-crawled on every run.</li>
 * </ul>
 *
 * <p>A phone goes through the same {@link PhoneUtil} path as ingest (split,
 * normalize, pick the best number to call, classify), and the lead is
 * re-scored via {@link IngestService#rescore}, so a contact found here moves
 * the score exactly as it would have if the source had reported it.
 */
@Service
@Slf4j
public class ContactEnrichmentService {

    public static final String RAW_ENRICH_ATTEMPTED = "enrich_attempted";
    public static final String RAW_ENRICH_AT = "enrich_at";
    public static final String RAW_ENRICH_FILLED = "enrich_filled";
    public static final String RAW_ENRICH_NOTES = "enrich_notes";

    public static final String FIELD_EMAIL = "email";
    public static final String FIELD_PHONE = "phone";
    public static final String FIELD_MESSENGER = "messenger";

    /** Upper bound for one pending-enrich page, whatever the caller asks for. */
    static final int MAX_PENDING_LIMIT = 500;

    private final CompanyRepository companyRepository;
    private final IngestService ingestService;

    public ContactEnrichmentService(CompanyRepository companyRepository, IngestService ingestService) {
        this.companyRepository = companyRepository;
        this.ingestService = ingestService;
    }

    /** See {@link CompanyRepository#findPendingEnrichment}; {@code limit} is clamped to 1..MAX_PENDING_LIMIT. */
    public List<Company> findPending(int limit) {
        int clamped = Math.max(1, Math.min(limit, MAX_PENDING_LIMIT));
        return companyRepository.findPendingEnrichment(clamped);
    }

    /**
     * Applies one enrichment report. Empty when no company has this id -
     * enrichment only ever updates an existing company, it never creates one.
     */
    @Transactional
    public Optional<EnrichmentResult> applyContacts(UUID companyId, ContactsPatchRequest patch) {
        Optional<Company> found = companyRepository.findById(companyId);
        if (found.isEmpty()) {
            return Optional.empty();
        }
        Company company = found.get();
        Map<String, Object> raw = company.getRaw() != null ? new HashMap<>(company.getRaw()) : new HashMap<>();
        List<String> filled = new ArrayList<>();

        if (isBlank(company.getEmail()) && !isBlank(patch.getEmail())) {
            company.setEmail(patch.getEmail().trim().toLowerCase(Locale.ROOT));
            filled.add(FIELD_EMAIL);
        }

        if (isBlank(company.getPhone())) {
            List<String> phones = PhoneUtil.normalizePhones(patch.getPhone());
            String primary = PhoneUtil.pickPrimary(phones);
            if (primary != null) {
                company.setPhone(primary);
                company.setNormalizedPhone(NormalizationUtil.normalizePhone(primary));
                // Same raw keys IngestService writes, so ScoringService and the
                // Sheets "Тип телефона" column treat a site-found phone like any other.
                raw.put("all_phones", phones);
                raw.put("primary_phone_type", PhoneUtil.classify(primary).name());
                filled.add(FIELD_PHONE);
            }
        }

        if (isBlank(company.getMessenger()) && !isBlank(patch.getMessenger())) {
            company.setMessenger(patch.getMessenger().trim());
            filled.add(FIELD_MESSENGER);
        }

        raw.put(RAW_ENRICH_ATTEMPTED, true);
        raw.put(RAW_ENRICH_AT, Instant.now().toString());
        raw.put(RAW_ENRICH_FILLED, mergeFilled(raw.get(RAW_ENRICH_FILLED), filled));
        if (!isBlank(patch.getEnrichNotes())) {
            raw.put(RAW_ENRICH_NOTES, patch.getEnrichNotes().trim());
        }
        // A fresh map rather than in-place mutation, so Hibernate always sees
        // the JSONB column as changed.
        company.setRaw(raw);

        Company saved = companyRepository.save(company);
        Lead lead = ingestService.rescore(saved);

        log.info("Contacts enriched: company={}, filled={}, score={}", saved.getId(), filled, lead.getScore());
        return Optional.of(new EnrichmentResult(saved, List.copyOf(filled), lead));
    }

    /**
     * Union of what earlier attempts filled and what this one did. Normally
     * there is only one attempt, but if the flag is cleared by hand and the
     * site re-crawled, the Sheets note about where a contact came from must
     * not be lost.
     */
    private static List<String> mergeFilled(Object previous, List<String> now) {
        Set<String> merged = new LinkedHashSet<>();
        if (previous instanceof Collection<?> earlier) {
            earlier.forEach(field -> merged.add(String.valueOf(field)));
        }
        merged.addAll(now);
        return new ArrayList<>(merged);
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    /** {@code filled} = fields actually written by this call (empty when nothing new was applied). */
    public record EnrichmentResult(Company company, List<String> filled, Lead lead) {
    }
}
