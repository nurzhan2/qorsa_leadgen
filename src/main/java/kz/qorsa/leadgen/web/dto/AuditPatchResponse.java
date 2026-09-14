package kz.qorsa.leadgen.web.dto;

import java.util.UUID;
import kz.qorsa.leadgen.domain.LeadStatus;
import lombok.Builder;
import lombok.Value;

/** What the core made of one site-audit report. */
@Value
@Builder
public class AuditPatchResponse {

    UUID companyId;

    /** Stored performance score, or null when the worker didn't measure one. */
    Integer pagespeed;

    /** Stored count of failed checks, or null when not measured. */
    Integer auditFails;

    int score;

    LeadStatus status;

    String hotReason;
}
