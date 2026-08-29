package kz.qorsa.leadgen.web.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * Result of DELETE /api/v1/admin/demo-data.
 *
 * <p>{@code deleted} is the headline number - how many DEMO companies were
 * removed. The two breakdown fields are there so an operator can see that the
 * cascade actually reached the dependent rows rather than having to go look in
 * the database afterwards.
 */
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class DemoDataDeletionResponse {

    /** Companies with source=DEMO that were deleted. */
    private int deleted;

    /** Leads that hung off those companies and were deleted with them. */
    private int deletedLeads;

    /** Outreach rows that hung off those leads and were deleted with them. */
    private int deletedOutreach;
}
