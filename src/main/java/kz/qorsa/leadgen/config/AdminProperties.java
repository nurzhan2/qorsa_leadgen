package kz.qorsa.leadgen.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Switch for the destructive admin endpoints under {@code /api/v1/admin}.
 * Bound from the "leadgen.admin" prefix in application.yml, which reads the
 * {@code ADMIN_ENABLED} environment variable.
 *
 * <p>Defaults to {@code false} on purpose: these endpoints delete data, so
 * production has to opt IN rather than remember to opt out. See
 * {@link kz.qorsa.leadgen.web.AdminController} for the other way in (the
 * "demo" profile).
 */
@Getter
@Setter
@ConfigurationProperties(prefix = "leadgen.admin")
public class AdminProperties {

    /**
     * Whether destructive admin endpoints are callable. False = every such
     * endpoint answers 403 regardless of who is calling.
     */
    private boolean enabled = false;
}
