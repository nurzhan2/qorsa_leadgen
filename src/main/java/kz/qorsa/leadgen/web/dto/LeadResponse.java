package kz.qorsa.leadgen.web.dto;

import java.time.Instant;
import java.util.UUID;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.domain.LeadStatus;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * Read-side view of a {@link kz.qorsa.leadgen.domain.Lead} joined with its
 * company, returned by GET /api/v1/leads*.
 */
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class LeadResponse {

    private UUID id;
    private UUID companyId;
    private String companyName;
    private String domain;
    private String phone;
    private String city;
    private LeadSource source;
    private int score;
    private String hotReason;
    private String niche;
    private LeadStatus status;
    private Instant createdAt;
}
