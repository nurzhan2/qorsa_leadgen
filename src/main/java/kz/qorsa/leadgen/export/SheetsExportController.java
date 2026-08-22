package kz.qorsa.leadgen.export;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Manual trigger for {@link SheetsExporter}, on top of its own
 * {@code @Scheduled} sync. No business logic here - just delegates.
 */
@RestController
@RequestMapping("/api/v1/export")
public class SheetsExportController {

    private final SheetsExporter exporter;

    public SheetsExportController(SheetsExporter exporter) {
        this.exporter = exporter;
    }

    @PostMapping("/sheets")
    public ResponseEntity<SheetsExporter.SyncResult> exportToSheets() {
        return ResponseEntity.ok(exporter.syncLeads());
    }
}
