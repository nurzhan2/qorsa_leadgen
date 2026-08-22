package kz.qorsa.leadgen.export;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Config for the Google Sheets export, bound from "leadgen.sheets" in
 * application.yml (in turn backed by GOOGLE_CREDENTIALS_PATH and
 * SHEETS_SPREADSHEET_ID env vars). Both must be set for the export to do
 * anything - see {@link #isConfigured()} - otherwise {@link SheetsExporter}
 * disables itself at startup and every sync call becomes a no-op.
 */
@Getter
@Setter
@ConfigurationProperties(prefix = "leadgen.sheets")
public class SheetsExportProperties {

    /** Path to the service-account JSON key file. Empty/unset = export disabled. */
    private String credentialsPath;

    /** Target spreadsheet ID (the long id segment in the sheet's URL). */
    private String spreadsheetId;

    /** How often {@link SheetsExporter#syncScheduled()} runs, in milliseconds. */
    private long fixedDelayMs = 300_000;

    public boolean isConfigured() {
        return hasText(credentialsPath) && hasText(spreadsheetId);
    }

    private static boolean hasText(String value) {
        return value != null && !value.isBlank();
    }
}
