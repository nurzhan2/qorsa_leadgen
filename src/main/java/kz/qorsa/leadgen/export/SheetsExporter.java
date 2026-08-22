package kz.qorsa.leadgen.export;

import com.google.api.client.googleapis.javanet.GoogleNetHttpTransport;
import com.google.api.client.http.HttpRequestInitializer;
import com.google.api.client.json.gson.GsonFactory;
import com.google.api.services.sheets.v4.Sheets;
import com.google.api.services.sheets.v4.SheetsScopes;
import com.google.api.services.sheets.v4.model.AddBandingRequest;
import com.google.api.services.sheets.v4.model.AddConditionalFormatRuleRequest;
import com.google.api.services.sheets.v4.model.AddSheetRequest;
import com.google.api.services.sheets.v4.model.AutoResizeDimensionsRequest;
import com.google.api.services.sheets.v4.model.BandedRange;
import com.google.api.services.sheets.v4.model.BandingProperties;
import com.google.api.services.sheets.v4.model.BasicFilter;
import com.google.api.services.sheets.v4.model.BatchUpdateSpreadsheetRequest;
import com.google.api.services.sheets.v4.model.BatchUpdateSpreadsheetResponse;
import com.google.api.services.sheets.v4.model.BooleanCondition;
import com.google.api.services.sheets.v4.model.BooleanRule;
import com.google.api.services.sheets.v4.model.CellData;
import com.google.api.services.sheets.v4.model.CellFormat;
import com.google.api.services.sheets.v4.model.ClearValuesRequest;
import com.google.api.services.sheets.v4.model.Color;
import com.google.api.services.sheets.v4.model.ConditionValue;
import com.google.api.services.sheets.v4.model.ConditionalFormatRule;
import com.google.api.services.sheets.v4.model.DimensionRange;
import com.google.api.services.sheets.v4.model.GridProperties;
import com.google.api.services.sheets.v4.model.GridRange;
import com.google.api.services.sheets.v4.model.Request;
import com.google.api.services.sheets.v4.model.RepeatCellRequest;
import com.google.api.services.sheets.v4.model.SetBasicFilterRequest;
import com.google.api.services.sheets.v4.model.Sheet;
import com.google.api.services.sheets.v4.model.SheetProperties;
import com.google.api.services.sheets.v4.model.Spreadsheet;
import com.google.api.services.sheets.v4.model.TextFormat;
import com.google.api.services.sheets.v4.model.UpdateSheetPropertiesRequest;
import com.google.api.services.sheets.v4.model.ValueRange;
import com.google.auth.http.HttpCredentialsAdapter;
import com.google.auth.oauth2.GoogleCredentials;
import jakarta.annotation.PostConstruct;
import java.io.FileInputStream;
import java.io.IOException;
import java.security.GeneralSecurityException;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.domain.LeadStatus;
import kz.qorsa.leadgen.repository.LeadRepository;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

/**
 * Pushes the current lead list into a Google Sheet as a set of tabs, using a
 * service account (no interactive OAuth, no MCP - a plain scheduled/REST
 * server-to-server sync). Disables itself cleanly when
 * {@link SheetsExportProperties#isConfigured()} is false: the core keeps
 * working, this just becomes a no-op with one WARN at startup.
 *
 * <p>Each sync fully rewrites every tab's data (clear + write) rather than
 * appending, which is what keeps re-running this every few minutes
 * idempotent without ever producing duplicate rows. Structural formatting
 * (banding, conditional format rules) is only ever added once - the first
 * time a tab is created - using a generous fixed row range, so Google
 * Sheets keeps applying it to whatever data occupies those rows on every
 * later sync without needing to be re-added (re-adding banding/conditional
 * rules on an existing range throws, so doing that every 5 minutes would
 * break after the first run).
 */
@Service
@Slf4j
public class SheetsExporter {

    private static final String APPLICATION_NAME = "qorsa-leadgen";
    private static final int COLUMN_COUNT = 12;
    // Fixed upper bound for banding/conditional-format ranges so they never
    // need to be recreated as the data grows or shrinks between syncs.
    private static final int MAX_FORMAT_ROWS = 10_000;

    private static final List<Object> HEADER = List.of(
            "Дата", "Компания", "Ниша", "Телефон", "Email", "Мессенджер", "Город",
            "Источник", "Score", "Статус", "Причина", "Ссылка");

    private static final DateTimeFormatter DATE_FORMAT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(ZoneOffset.UTC);

    private static final Color HEADER_BG = rgb(0.102, 0.102, 0.180);
    private static final Color WHITE = rgb(1, 1, 1);
    private static final Color BAND_GRAY = rgb(0.945, 0.945, 0.945);
    private static final Color SCORE_GREEN = rgb(0.714, 0.882, 0.722);
    private static final Color SCORE_YELLOW = rgb(1.0, 0.949, 0.6);
    private static final Color SCORE_GRAY = rgb(0.90, 0.90, 0.90);
    private static final Color STATUS_RED = rgb(0.859, 0.204, 0.204);
    private static final Color STATUS_ORANGE = rgb(0.902, 0.494, 0.133);
    private static final Color STATUS_GRAY = rgb(0.45, 0.45, 0.45);

    private final SheetsExportProperties properties;
    private final LeadRepository leadRepository;
    private Sheets sheetsService;

    public SheetsExporter(SheetsExportProperties properties, LeadRepository leadRepository) {
        this.properties = properties;
        this.leadRepository = leadRepository;
    }

    @PostConstruct
    void init() {
        if (!properties.isConfigured()) {
            log.warn("Sheets export disabled: set GOOGLE_CREDENTIALS_PATH and SHEETS_SPREADSHEET_ID to enable it");
            return;
        }
        try {
            this.sheetsService = buildSheetsService(properties.getCredentialsPath());
            log.info("Sheets export enabled, target spreadsheet {}", properties.getSpreadsheetId());
        } catch (IOException | GeneralSecurityException e) {
            log.error("Failed to initialize Google Sheets client - export disabled", e);
            this.sheetsService = null;
        }
    }

    @Scheduled(fixedDelayString = "${leadgen.sheets.fixed-delay-ms:300000}")
    public void syncScheduled() {
        if (sheetsService == null) {
            return; // disabled - the reason was already logged once at startup
        }
        SyncResult result = syncLeads();
        log.info("Scheduled Sheets sync: tabs={}, leads={}", result.tabsUpdated(), result.leadsExported());
    }

    /** Entry point for both the scheduled job and the manual REST trigger. */
    public SyncResult syncLeads() {
        if (sheetsService == null) {
            return new SyncResult(false, 0, 0,
                    "Sheets export disabled: GOOGLE_CREDENTIALS_PATH/SHEETS_SPREADSHEET_ID not set");
        }
        try {
            return doSync();
        } catch (IOException e) {
            log.error("Sheets sync failed", e);
            return new SyncResult(true, 0, 0, "Sheets sync failed: " + e.getMessage());
        }
    }

    private SyncResult doSync() throws IOException {
        List<Lead> leads = leadRepository.findAllWithCompany();

        Map<String, List<Lead>> tabs = new LinkedHashMap<>();
        tabs.put("🔥 Тёплые", leads.stream().filter(l -> isWarm(l.getStatus())).toList());
        tabs.put("❄️ Холодные", leads.stream().filter(l -> !isWarm(l.getStatus())).toList());
        tabs.put("📊 Все", leads);
        for (Lead lead : leads) {
            tabs.computeIfAbsent(sourceLabel(lead.getCompany().getSource()), k -> new ArrayList<>()).add(lead);
        }

        Spreadsheet spreadsheet = sheetsService.spreadsheets().get(properties.getSpreadsheetId()).execute();
        Map<String, Integer> titleToSheetId = new LinkedHashMap<>();
        for (Sheet sheet : spreadsheet.getSheets()) {
            titleToSheetId.put(sheet.getProperties().getTitle(), sheet.getProperties().getSheetId());
        }

        List<String> missingTitles = tabs.keySet().stream().filter(t -> !titleToSheetId.containsKey(t)).toList();
        if (!missingTitles.isEmpty()) {
            titleToSheetId.putAll(createSheets(missingTitles));
        }

        int leadsExported = 0;
        for (Map.Entry<String, List<Lead>> entry : tabs.entrySet()) {
            String title = entry.getKey();
            List<Lead> tabLeads = entry.getValue();
            int sheetId = titleToSheetId.get(title);
            boolean isNewSheet = missingTitles.contains(title);
            writeSheet(title, sheetId, tabLeads, isNewSheet);
            leadsExported += tabLeads.size();
        }

        return new SyncResult(true, tabs.size(), leadsExported, "ok");
    }

    private Map<String, Integer> createSheets(List<String> titles) throws IOException {
        List<Request> requests = titles.stream()
                .map(title -> new Request().setAddSheet(new AddSheetRequest()
                        .setProperties(new SheetProperties().setTitle(title))))
                .toList();

        BatchUpdateSpreadsheetResponse response = sheetsService.spreadsheets()
                .batchUpdate(properties.getSpreadsheetId(), new BatchUpdateSpreadsheetRequest().setRequests(requests))
                .execute();

        Map<String, Integer> created = new LinkedHashMap<>();
        for (var reply : response.getReplies()) {
            if (reply.getAddSheet() != null) {
                SheetProperties props = reply.getAddSheet().getProperties();
                created.put(props.getTitle(), props.getSheetId());
            }
        }
        return created;
    }

    private void writeSheet(String title, int sheetId, List<Lead> leads, boolean isNewSheet) throws IOException {
        String spreadsheetId = properties.getSpreadsheetId();
        List<List<Object>> rows = buildRows(leads);

        sheetsService.spreadsheets().values()
                .clear(spreadsheetId, quoted(title) + "!A1:L" + (MAX_FORMAT_ROWS + 1), new ClearValuesRequest())
                .execute();

        sheetsService.spreadsheets().values()
                .update(spreadsheetId, quoted(title) + "!A1", new ValueRange().setValues(rows))
                .setValueInputOption("RAW")
                .execute();

        List<Request> formatting = buildFormattingRequests(sheetId, isNewSheet);
        sheetsService.spreadsheets()
                .batchUpdate(spreadsheetId, new BatchUpdateSpreadsheetRequest().setRequests(formatting))
                .execute();
    }

    private List<Request> buildFormattingRequests(int sheetId, boolean isNewSheet) {
        List<Request> requests = new ArrayList<>();

        requests.add(new Request().setRepeatCell(new RepeatCellRequest()
                .setRange(fullWidthRange(sheetId, 0, 1))
                .setCell(new CellData().setUserEnteredFormat(new CellFormat()
                        .setBackgroundColor(HEADER_BG)
                        .setTextFormat(new TextFormat().setBold(true).setForegroundColor(WHITE))))
                .setFields("userEnteredFormat(backgroundColor,textFormat)")));

        requests.add(new Request().setUpdateSheetProperties(new UpdateSheetPropertiesRequest()
                .setProperties(new SheetProperties().setSheetId(sheetId)
                        .setGridProperties(new GridProperties().setFrozenRowCount(1)))
                .setFields("gridProperties.frozenRowCount")));

        requests.add(new Request().setSetBasicFilter(new SetBasicFilterRequest()
                .setFilter(new BasicFilter().setRange(fullWidthRange(sheetId, 0, MAX_FORMAT_ROWS)))));

        requests.add(new Request().setAutoResizeDimensions(new AutoResizeDimensionsRequest()
                .setDimensions(new DimensionRange().setSheetId(sheetId).setDimension("COLUMNS")
                        .setStartIndex(0).setEndIndex(COLUMN_COUNT))));

        if (isNewSheet) {
            requests.add(new Request().setAddBanding(new AddBandingRequest()
                    .setBandedRange(new BandedRange()
                            .setRange(fullWidthRange(sheetId, 0, MAX_FORMAT_ROWS))
                            .setRowProperties(new BandingProperties()
                                    .setHeaderColor(HEADER_BG)
                                    .setFirstBandColor(WHITE)
                                    .setSecondBandColor(BAND_GRAY)))));

            requests.addAll(scoreConditionalFormatRules(sheetId));
            requests.addAll(statusConditionalFormatRules(sheetId));
        }

        return requests;
    }

    // Score column (index 8, "I"): >=70 green, 40-69 yellow, <40 gray.
    private List<Request> scoreConditionalFormatRules(int sheetId) {
        GridRange range = columnRange(sheetId, 8, 9);
        return List.of(
                addConditionalFormat(range, "NUMBER_GREATER_THAN_EQ", List.of("70"), null, SCORE_GREEN),
                addConditionalFormat(range, "NUMBER_BETWEEN", List.of("40", "69"), null, SCORE_YELLOW),
                addConditionalFormat(range, "NUMBER_LESS", List.of("40"), null, SCORE_GRAY));
    }

    // Status column (index 9, "J"): HOT red+bold, QUALIFIED orange, NEW gray.
    private List<Request> statusConditionalFormatRules(int sheetId) {
        GridRange range = columnRange(sheetId, 9, 10);
        return List.of(
                addConditionalFormat(range, "TEXT_EQ", List.of("HOT"), STATUS_RED, null),
                addConditionalFormat(range, "TEXT_EQ", List.of("QUALIFIED"), STATUS_ORANGE, null),
                addConditionalFormat(range, "TEXT_EQ", List.of("NEW"), STATUS_GRAY, null));
    }

    private Request addConditionalFormat(GridRange range, String conditionType, List<String> values,
                                          Color textColor, Color backgroundColor) {
        List<ConditionValue> conditionValues = values.stream()
                .map(v -> new ConditionValue().setUserEnteredValue(v))
                .toList();

        CellFormat format = new CellFormat();
        if (textColor != null) {
            format.setTextFormat(new TextFormat().setForegroundColor(textColor).setBold(true));
        }
        if (backgroundColor != null) {
            format.setBackgroundColor(backgroundColor);
        }

        ConditionalFormatRule rule = new ConditionalFormatRule()
                .setRanges(List.of(range))
                .setBooleanRule(new BooleanRule()
                        .setCondition(new BooleanCondition().setType(conditionType).setValues(conditionValues))
                        .setFormat(format));

        return new Request().setAddConditionalFormatRule(new AddConditionalFormatRuleRequest()
                .setRule(rule)
                .setIndex(0));
    }

    private List<List<Object>> buildRows(List<Lead> leads) {
        List<List<Object>> rows = new ArrayList<>();
        rows.add(HEADER);
        for (Lead lead : leads) {
            Company company = lead.getCompany();
            rows.add(List.of(
                    formatDate(lead.getCreatedAt()),
                    nullToEmpty(company.getName()),
                    nullToEmpty(lead.getNiche()),
                    nullToEmpty(company.getPhone()),
                    nullToEmpty(company.getEmail()),
                    nullToEmpty(company.getMessenger()),
                    nullToEmpty(company.getCity()),
                    sourceLabel(company.getSource()),
                    lead.getScore(),
                    lead.getStatus().name(),
                    nullToEmpty(lead.getHotReason()),
                    nullToEmpty(company.getSourceUrl())));
        }
        return rows;
    }

    private static boolean isWarm(LeadStatus status) {
        return status == LeadStatus.HOT || status == LeadStatus.QUALIFIED;
    }

    /** Friendly Russian tab/column label for a source - also used to name per-source tabs. */
    static String sourceLabel(LeadSource source) {
        return switch (source) {
            case TELEGRAM_ORDER -> "TG заявки";
            case GOOGLE_MAPS -> "Google Maps";
            case TWOGIS -> "2GIS";
            case YANDEX_REVIEW -> "Яндекс Отзывы";
            case VACANCY -> "Вакансии";
            case AVITO_JOB -> "Avito";
            case DEMO -> "Демо";
            case OTHER -> "Прочее";
        };
    }

    private static String formatDate(Instant instant) {
        return instant == null ? "" : DATE_FORMAT.format(instant);
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String quoted(String sheetTitle) {
        return "'" + sheetTitle.replace("'", "''") + "'";
    }

    private static GridRange fullWidthRange(int sheetId, int startRow, int endRow) {
        return new GridRange().setSheetId(sheetId)
                .setStartRowIndex(startRow).setEndRowIndex(endRow)
                .setStartColumnIndex(0).setEndColumnIndex(COLUMN_COUNT);
    }

    private static GridRange columnRange(int sheetId, int startCol, int endCol) {
        return new GridRange().setSheetId(sheetId)
                .setStartRowIndex(1).setEndRowIndex(MAX_FORMAT_ROWS)
                .setStartColumnIndex(startCol).setEndColumnIndex(endCol);
    }

    private static Color rgb(double r, double g, double b) {
        return new Color().setRed((float) r).setGreen((float) g).setBlue((float) b);
    }

    private static Sheets buildSheetsService(String credentialsPath) throws IOException, GeneralSecurityException {
        GoogleCredentials credentials;
        try (FileInputStream in = new FileInputStream(credentialsPath)) {
            credentials = GoogleCredentials.fromStream(in).createScoped(List.of(SheetsScopes.SPREADSHEETS));
        }
        HttpRequestInitializer requestInitializer = new HttpCredentialsAdapter(credentials);
        return new Sheets.Builder(GoogleNetHttpTransport.newTrustedTransport(), GsonFactory.getDefaultInstance(), requestInitializer)
                .setApplicationName(APPLICATION_NAME)
                .build();
    }

    public record SyncResult(boolean enabled, int tabsUpdated, int leadsExported, String message) {
    }
}
