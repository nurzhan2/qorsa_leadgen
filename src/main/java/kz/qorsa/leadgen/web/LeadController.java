package kz.qorsa.leadgen.web;

import java.util.List;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadStatus;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.web.dto.LeadResponse;
import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Read-side API for whatever consumes scored leads (a CRM sync job, a sales
 * dashboard, a Slack bot, ...). No business logic lives here - it only maps
 * entities to DTOs.
 */
@RestController
@RequestMapping("/api/v1/leads")
public class LeadController {

    private final LeadRepository leadRepository;

    public LeadController(LeadRepository leadRepository) {
        this.leadRepository = leadRepository;
    }

    @GetMapping("/top")
    public List<LeadResponse> top(@RequestParam(defaultValue = "20") int limit) {
        return leadRepository.findAllByOrderByScoreDesc(PageRequest.of(0, limit))
                .stream()
                .map(this::toResponse)
                .toList();
    }

    @GetMapping
    public List<LeadResponse> byStatus(@RequestParam(required = false) LeadStatus status) {
        List<Lead> leads = status != null
                ? leadRepository.findByStatusOrderByScoreDesc(status)
                : leadRepository.findAllByOrderByScoreDesc(PageRequest.of(0, 100));
        return leads.stream().map(this::toResponse).toList();
    }

    private LeadResponse toResponse(Lead lead) {
        var company = lead.getCompany();
        return LeadResponse.builder()
                .id(lead.getId())
                .companyId(company.getId())
                .companyName(company.getName())
                .domain(company.getDomain())
                .phone(company.getPhone())
                .city(company.getCity())
                .source(company.getSource())
                .score(lead.getScore())
                .hotReason(lead.getHotReason())
                .niche(lead.getNiche())
                .status(lead.getStatus())
                .createdAt(lead.getCreatedAt())
                .build();
    }
}
