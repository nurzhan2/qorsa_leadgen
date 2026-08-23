package kz.qorsa.leadgen.service;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import kz.qorsa.leadgen.config.ScoringProperties;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadStatus;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.web.dto.RawCompanyRequest;
import kz.qorsa.leadgen.web.dto.IngestResponse;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * The single network boundary through which raw scraper output becomes leads.
 * Every Python worker (Telegram, Google Maps, 2GIS, Avito, ...) ultimately
 * funnels its findings through {@link #ingest(List)} via POST
 * /api/v1/companies/ingest — nothing else about this service needs to know
 * where the data came from.
 *
 * <p>Pipeline per item: build {@link Company} from the DTO -&gt; normalize -&gt;
 * {@link DedupService#findExisting} -&gt; merge into the existing row or create
 * a new one -&gt; save -&gt; {@link ScoringService#score} -&gt; create/refresh the
 * associated {@link Lead}.
 */
@Service
public class IngestService {

    private final CompanyRepository companyRepository;
    private final LeadRepository leadRepository;
    private final DedupService dedupService;
    private final ScoringService scoringService;
    private final ScoringProperties scoringProperties;

    public IngestService(CompanyRepository companyRepository,
                          LeadRepository leadRepository,
                          DedupService dedupService,
                          ScoringService scoringService,
                          ScoringProperties scoringProperties) {
        this.companyRepository = companyRepository;
        this.leadRepository = leadRepository;
        this.dedupService = dedupService;
        this.scoringService = scoringService;
        this.scoringProperties = scoringProperties;
    }

    @Transactional
    public IngestResponse ingest(List<RawCompanyRequest> batch) {
        int created = 0;
        int merged = 0;
        int leadsScored = 0;
        int hotCount = 0;

        for (RawCompanyRequest raw : batch) {
            Company candidate = toCandidate(raw);

            Optional<Company> existing = dedupService.findExisting(candidate);
            Company saved;
            if (existing.isPresent()) {
                saved = enrich(existing.get(), candidate);
                merged++;
            } else {
                saved = candidate;
                created++;
            }
            saved = companyRepository.save(saved);

            ScoringService.ScoreResult result = scoringService.score(saved);
            Lead lead = upsertLead(saved, result);
            leadsScored++;
            if (lead.getStatus() == LeadStatus.HOT) {
                hotCount++;
            }
        }

        return IngestResponse.builder()
                .created(created)
                .merged(merged)
                .leadsScored(leadsScored)
                .hotCount(hotCount)
                .build();
    }

    private Company toCandidate(RawCompanyRequest raw) {
        // Split/classify BEFORE building the entity: Company.phone becomes the
        // best single number for a cold call (PhoneUtil.pickPrimary), not
        // necessarily whatever the worker sent verbatim - a worker may report
        // several numbers separated by ";"/","/"/" in one string.
        List<String> allPhones = PhoneUtil.normalizePhones(raw.getPhone());
        String primaryPhone = PhoneUtil.pickPrimary(allPhones);

        Map<String, Object> rawMap = raw.getRaw() != null ? new HashMap<>(raw.getRaw()) : new HashMap<>();
        if (!allPhones.isEmpty()) {
            rawMap.put("all_phones", allPhones);
            rawMap.put("primary_phone_type", PhoneUtil.classify(primaryPhone).name());
        }

        Company company = Company.builder()
                .name(raw.getName())
                .domain(raw.getDomain())
                .phone(primaryPhone)
                .email(raw.getEmail())
                .messenger(raw.getMessenger())
                .address(raw.getAddress())
                .city(raw.getCity())
                .source(raw.getSource())
                .sourceUrl(raw.getSourceUrl())
                .hasSite(raw.isHasSite())
                .raw(rawMap)
                .build();

        String domainSource = firstNonBlank(company.getDomain(), company.getEmail());
        company.setNormalizedDomain(NormalizationUtil.normalizeDomain(domainSource));
        company.setNormalizedPhone(NormalizationUtil.normalizePhone(company.getPhone()));
        company.setNormalizedName(NormalizationUtil.normalizeName(company.getName()));
        return company;
    }

    /**
     * Enriches the existing, already-persisted company with any non-blank
     * values from the freshly ingested candidate, without overwriting fields
     * that already have a value. Returns the mutated existing entity.
     */
    private Company enrich(Company existing, Company candidate) {
        existing.setDomain(firstNonBlank(existing.getDomain(), candidate.getDomain()));
        existing.setPhone(firstNonBlank(existing.getPhone(), candidate.getPhone()));
        existing.setEmail(firstNonBlank(existing.getEmail(), candidate.getEmail()));
        existing.setMessenger(firstNonBlank(existing.getMessenger(), candidate.getMessenger()));
        existing.setAddress(firstNonBlank(existing.getAddress(), candidate.getAddress()));
        existing.setCity(firstNonBlank(existing.getCity(), candidate.getCity()));
        existing.setSourceUrl(firstNonBlank(existing.getSourceUrl(), candidate.getSourceUrl()));

        // A confirmed "has a site" observation is more informative than "no site" -
        // once we know a site exists, don't let a later no-site report erase that.
        if (candidate.isHasSite()) {
            existing.setHasSite(true);
        }

        if (candidate.getRaw() != null) {
            if (existing.getRaw() == null) {
                existing.setRaw(new HashMap<>());
            }
            for (var entry : candidate.getRaw().entrySet()) {
                existing.getRaw().putIfAbsent(entry.getKey(), entry.getValue());
            }
        }

        existing.setNormalizedDomain(firstNonBlank(existing.getNormalizedDomain(), candidate.getNormalizedDomain()));
        existing.setNormalizedPhone(firstNonBlank(existing.getNormalizedPhone(), candidate.getNormalizedPhone()));
        existing.setNormalizedName(firstNonBlank(existing.getNormalizedName(), candidate.getNormalizedName()));

        return existing;
    }

    private Lead upsertLead(Company company, ScoringService.ScoreResult result) {
        List<Lead> existingLeads = leadRepository.findByCompanyId(company.getId());
        Lead lead = existingLeads.isEmpty() ? Lead.builder().company(company).build() : existingLeads.get(0);

        lead.setScore(result.score());
        lead.setHotReason(result.reason());
        lead.setStatus(statusFor(result.score()));

        return leadRepository.save(lead);
    }

    private LeadStatus statusFor(int score) {
        if (score >= scoringProperties.getHotThreshold()) {
            return LeadStatus.HOT;
        }
        if (score >= scoringProperties.getQualifiedThreshold()) {
            return LeadStatus.QUALIFIED;
        }
        return LeadStatus.NEW;
    }

    private static String firstNonBlank(String a, String b) {
        if (a != null && !a.isBlank()) {
            return a;
        }
        return (b != null && !b.isBlank()) ? b : a;
    }
}
