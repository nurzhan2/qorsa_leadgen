package kz.qorsa.leadgen.web.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import java.util.List;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * One site-audit report from workers/site_audit.
 *
 * <p>Every field is optional on purpose. A site that couldn't be reached
 * still produces a valid report - it carries no numbers, only notes - and
 * still marks the company as attempted, so a dead domain isn't re-measured
 * on every run.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AuditPatchRequest {

    /** Lighthouse performance score, 0-100. Null when not measured. */
    @Min(0)
    @Max(100)
    private Integer pagespeed;

    /** How many audit checks the site failed. Null when not measured. */
    @Min(0)
    private Integer auditFails;

    /** Which checks failed, for the "Причина" column. Short labels, not prose. */
    private List<@Size(max = 60) String> failedChecks;

    /** Free-form note: why the site was unreachable, which strategy was used, etc. */
    @Size(max = 500)
    private String auditNotes;
}
