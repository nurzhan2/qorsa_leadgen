package kz.qorsa.leadgen.service;

import kz.qorsa.leadgen.config.ScoringProperties;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.repository.CompanyRepository;
import java.util.List;
import java.util.Optional;
import me.xdrop.fuzzywuzzy.FuzzySearch;
import org.springframework.stereotype.Service;

/**
 * Finds an already-stored {@link Company} that a freshly-ingested candidate
 * most likely refers to, so {@code IngestService} can merge instead of
 * creating a duplicate row.
 *
 * <p>Match order (first hit wins):
 * <ol>
 *   <li>exact {@code normalizedDomain}</li>
 *   <li>exact {@code normalizedPhone}</li>
 *   <li>fuzzy {@code normalizedName} (token-sort ratio) among companies in the same city</li>
 * </ol>
 */
@Service
public class DedupService {

    private final CompanyRepository companyRepository;
    private final ScoringProperties scoringProperties;

    public DedupService(CompanyRepository companyRepository, ScoringProperties scoringProperties) {
        this.companyRepository = companyRepository;
        this.scoringProperties = scoringProperties;
    }

    public Optional<Company> findExisting(Company candidate) {
        if (candidate.getNormalizedDomain() != null) {
            Optional<Company> byDomain = companyRepository.findFirstByNormalizedDomain(candidate.getNormalizedDomain());
            if (byDomain.isPresent()) {
                return byDomain;
            }
        }

        if (candidate.getNormalizedPhone() != null) {
            Optional<Company> byPhone = companyRepository.findFirstByNormalizedPhone(candidate.getNormalizedPhone());
            if (byPhone.isPresent()) {
                return byPhone;
            }
        }

        return findByFuzzyName(candidate);
    }

    private Optional<Company> findByFuzzyName(Company candidate) {
        if (candidate.getNormalizedName() == null || candidate.getCity() == null || candidate.getCity().isBlank()) {
            return Optional.empty();
        }

        List<Company> sameCity = companyRepository.findByCity(candidate.getCity());
        int threshold = scoringProperties.getFuzzyNameThreshold();

        Company best = null;
        int bestScore = -1;
        for (Company existing : sameCity) {
            if (existing.getNormalizedName() == null) {
                continue;
            }
            int ratio = FuzzySearch.tokenSortRatio(candidate.getNormalizedName(), existing.getNormalizedName());
            if (ratio >= threshold && ratio > bestScore) {
                best = existing;
                bestScore = ratio;
            }
        }
        return Optional.ofNullable(best);
    }
}
