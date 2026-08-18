package kz.qorsa.leadgen.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.annotations.UpdateTimestamp;
import org.hibernate.type.SqlTypes;

/**
 * A business entity discovered by any ingest source (scraper worker). Holds
 * both the original values as reported by the source and normalized copies
 * used for deduplication.
 */
@Entity
@Table(name = "companies")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class Company {

    @Id
    @Builder.Default
    private UUID id = UUID.randomUUID();

    @Column(nullable = false)
    private String name;

    private String domain;

    private String phone;

    private String email;

    private String messenger;

    private String address;

    private String city;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private LeadSource source;

    private String sourceUrl;

    @Column(name = "has_site")
    private boolean hasSite;

    @Column(name = "normalized_domain")
    private String normalizedDomain;

    @Column(name = "normalized_phone")
    private String normalizedPhone;

    @Column(name = "normalized_name")
    private String normalizedName;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    @Builder.Default
    private Map<String, Object> raw = new HashMap<>();

    @CreationTimestamp
    @Column(name = "created_at", updatable = false)
    private Instant createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at")
    private Instant updatedAt;
}
