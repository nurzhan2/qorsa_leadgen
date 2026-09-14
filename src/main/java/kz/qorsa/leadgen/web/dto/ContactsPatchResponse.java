package kz.qorsa.leadgen.web.dto;

import java.util.List;
import java.util.UUID;
import kz.qorsa.leadgen.domain.LeadStatus;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * Result of PATCH /api/v1/companies/{id}/contacts. {@code filled} lists the
 * fields that were actually written - a value sent for a field the source had
 * already filled is ignored and does NOT appear here - and the contact fields
 * show the company as it now stands, alongside its re-computed score.
 */
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class ContactsPatchResponse {

    private UUID companyId;
    private List<String> filled;
    private String email;
    private String phone;
    private String messenger;
    private int score;
    private LeadStatus status;
    private String hotReason;
}
