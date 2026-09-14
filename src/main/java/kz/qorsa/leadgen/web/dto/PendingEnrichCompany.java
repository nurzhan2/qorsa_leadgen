package kz.qorsa.leadgen.web.dto;

import java.util.UUID;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * One company handed to workers/enrich by GET /api/v1/companies/pending-enrich:
 * just enough to visit its site and to know which contacts are still missing
 * (a null {@code email}/{@code phone}/{@code messenger} is what the worker
 * looks for).
 */
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class PendingEnrichCompany {

    private UUID id;
    private String name;
    private String domain;
    private String email;
    private String phone;
    private String messenger;
    private String city;
}
