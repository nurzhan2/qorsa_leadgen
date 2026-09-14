package kz.qorsa.leadgen.web.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.Size;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * Body of PATCH /api/v1/companies/{id}/contacts - what workers/enrich found on
 * a company's website. Every field is optional: an empty body is a valid
 * "tried, found nothing" report and still marks the company as attempted.
 *
 * <p>Size limits mirror the column widths in V1__init.sql, so an oversized
 * value is a 400 here rather than a 500 from the database.
 */
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class ContactsPatchRequest {

    @Email
    @Size(max = 255)
    private String email;

    /**
     * One or several numbers in any RU/KZ format ("+7 (727) 355-10-20; 8 800
     * ..."). PhoneUtil splits, normalizes and picks the one to call, exactly as
     * on ingest - so this is a raw string, not a normalized number.
     */
    @Size(max = 500)
    private String phone;

    @Size(max = 255)
    private String messenger;

    /** Human-readable provenance ("email с /contacts; телефон с главной"), stored in raw.enrich_notes. */
    @Size(max = 1000)
    private String enrichNotes;
}
