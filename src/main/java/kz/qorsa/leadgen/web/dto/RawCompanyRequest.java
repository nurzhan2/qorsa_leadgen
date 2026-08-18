package kz.qorsa.leadgen.web.dto;

import jakarta.validation.constraints.NotBlank;
import java.util.Map;
import kz.qorsa.leadgen.domain.LeadSource;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * The network contract for ingest. This is the ONLY shape a Python scraper
 * worker (Telegram, Google Maps, 2GIS, Avito, ...) needs to know about — see
 * README.md for the full JSON contract and examples.
 */
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class RawCompanyRequest {

    @NotBlank
    private String name;

    private String domain;

    private String phone;

    private String email;

    private String messenger;

    private String address;

    private String city;

    @Builder.Default
    private LeadSource source = LeadSource.OTHER;

    private String sourceUrl;

    @Builder.Default
    private boolean hasSite = true;

    private Map<String, Object> raw;
}
