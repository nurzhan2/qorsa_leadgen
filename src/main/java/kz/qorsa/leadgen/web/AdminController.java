package kz.qorsa.leadgen.web;

import kz.qorsa.leadgen.config.AdminProperties;
import kz.qorsa.leadgen.service.AdminService;
import kz.qorsa.leadgen.web.dto.DemoDataDeletionResponse;
import kz.qorsa.leadgen.web.dto.RescoreResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.env.Environment;
import org.springframework.core.env.Profiles;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * Administrative, destructive operations - kept off the ingest boundary and
 * behind an explicit switch.
 *
 * <p><b>Why 403 and not a missing route:</b> the controller is always
 * registered, and refuses at call time, rather than being annotated
 * {@code @Profile("demo")} (which would 404 when disabled). A 403 tells an
 * operator "this exists, your instance isn't allowed to use it", which is the
 * honest answer and far easier to debug than a 404 that looks like a typo.
 *
 * <p><b>What "allowed" means:</b> either the {@code demo} profile is active
 * (a throwaway instance whose whole point is seeded fake data), or
 * {@code ADMIN_ENABLED=true} was set explicitly. A production instance that
 * sets neither - the default - can never reach the deletion logic.
 *
 * <p>This is a kill-switch, not authentication. It stops the endpoint being
 * live in prod by default; it does not identify the caller. If this service
 * ever gets exposed beyond a trusted network, this endpoint needs real
 * authentication in front of it.
 */
@RestController
@RequestMapping("/api/v1/admin")
@Slf4j
public class AdminController {

    private static final Profiles DEMO_PROFILE = Profiles.of("demo");

    private final AdminService adminService;
    private final AdminProperties adminProperties;
    private final Environment environment;

    public AdminController(AdminService adminService,
                           AdminProperties adminProperties,
                           Environment environment) {
        this.adminService = adminService;
        this.adminProperties = adminProperties;
        this.environment = environment;
    }

    /**
     * Deletes every company with {@code source=DEMO} (and the leads/outreach
     * hanging off them) - the DemoSeeder fixtures, which otherwise sit mixed in
     * with real scraped leads forever.
     *
     * @return {@code {"deleted": N, ...}} on success, 403 when this instance
     *         isn't allowed to run admin operations.
     */
    @DeleteMapping("/demo-data")
    public ResponseEntity<DemoDataDeletionResponse> deleteDemoData() {
        requireAdminEnabled();
        return ResponseEntity.ok(adminService.deleteDemoData());
    }

    /**
     * Re-scores every stored company under the current rules and weights.
     *
     * <p>Needed because a score is written once, at ingest: without this, a
     * change to the scoring model silently applies only to companies ingested
     * after it, and the back catalogue keeps its old numbers forever.
     *
     * <p>Not destructive, but gated the same way - it rewrites every lead row
     * in the table and is expensive enough that it shouldn't be reachable by
     * accident.
     *
     * @return counts by status after the sweep, or 403 when admin operations
     *         are disabled.
     */
    @PostMapping("/rescore")
    public ResponseEntity<RescoreResponse> rescore() {
        requireAdminEnabled();
        return ResponseEntity.ok(adminService.rescoreAll());
    }

    private void requireAdminEnabled() {
        if (adminEnabled()) {
            return;
        }
        log.warn("Rejected admin request: neither the 'demo' profile nor ADMIN_ENABLED=true is set");
        throw new ResponseStatusException(
                HttpStatus.FORBIDDEN,
                "Admin operations are disabled. Activate the 'demo' profile or set ADMIN_ENABLED=true.");
    }

    private boolean adminEnabled() {
        return adminProperties.isEnabled() || environment.acceptsProfiles(DEMO_PROFILE);
    }
}
