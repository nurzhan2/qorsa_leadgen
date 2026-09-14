package kz.qorsa.leadgen.web.dto;

import lombok.Builder;
import lombok.Value;

/**
 * Result of a full rescore sweep: how many companies were walked, and how the
 * leads landed across the status thresholds afterwards.
 */
@Value
@Builder
public class RescoreResponse {

    /** Companies re-scored (every row in the table). */
    int rescored;

    /** Leads at or above the HOT threshold after the sweep. */
    long hot;

    /** Leads at or above QUALIFIED but below HOT. */
    long qualified;

    /** Leads below the QUALIFIED threshold. */
    long cold;
}
