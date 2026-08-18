package kz.qorsa.leadgen.web.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * Summary returned to the calling scraper worker after a batch of
 * {@link RawCompanyRequest} has been processed by the ingest pipeline.
 */
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class IngestResponse {

    /** Number of brand-new companies created. */
    private int created;

    /** Number of candidates that matched an existing company and were merged into it. */
    private int merged;

    /** Total number of leads scored during this batch (one per created/merged company). */
    private int leadsScored;

    /** How many of those leads came out HOT. */
    private int hotCount;
}
