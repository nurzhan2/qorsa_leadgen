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

    /** Number of brand-new companies created. Counted per batch item. */
    private int created;

    /**
     * Number of candidates that matched an existing company and were merged
     * into it. Counted per batch item, so {@code created + merged} always
     * equals the batch size.
     */
    private int merged;

    /**
     * Number of DISTINCT leads scored during this batch. Not the same as
     * {@code created + merged}: several items of one batch can collapse onto a
     * single lead via dedup, and such a lead is counted once here.
     */
    private int leadsScored;

    /**
     * How many of those distinct leads ended the batch HOT - judged on each
     * lead's FINAL score, after every item that touched it has been merged in.
     */
    private int hotCount;
}
