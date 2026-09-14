package kz.qorsa.leadgen.web.dto;

import java.util.UUID;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * One company handed to workers/site_audit by
 * GET /api/v1/companies/pending-audit: the domain to measure, plus a name and
 * city so a run log is readable by a human.
 */
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class PendingAuditCompany {

    private UUID id;
    private String name;
    private String domain;
    private String city;
}
