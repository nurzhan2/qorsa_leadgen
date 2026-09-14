package kz.qorsa.leadgen.export;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Map;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadSource;
import org.junit.jupiter.api.Test;

/**
 * The bulk of {@link SheetsExporter} is a thin wrapper around the Google
 * Sheets API and isn't worth mocking end-to-end here; {@code sourceLabel}
 * is the one pure, package-visible piece (used both for the "Источник"
 * column and to name per-source tabs), so it gets a real test.
 */
class SheetsExporterTest {

    @Test
    void everySourceHasAReadableRussianLabel() {
        for (LeadSource source : LeadSource.values()) {
            String label = SheetsExporter.sourceLabel(source);
            assertThat(label).isNotBlank();
        }
    }

    @Test
    void labelsMatchExpectedTabNames() {
        assertThat(SheetsExporter.sourceLabel(LeadSource.TELEGRAM_ORDER)).isEqualTo("TG заявки");
        assertThat(SheetsExporter.sourceLabel(LeadSource.TWOGIS)).isEqualTo("2GIS");
        assertThat(SheetsExporter.sourceLabel(LeadSource.OSM)).isEqualTo("OpenStreetMap");
        assertThat(SheetsExporter.sourceLabel(LeadSource.ZAKUPKI)).isEqualTo("Госзакупки");
        assertThat(SheetsExporter.sourceLabel(LeadSource.NEW_DOMAIN)).isEqualTo("Новые домены");
        assertThat(SheetsExporter.sourceLabel(LeadSource.GOOGLE_MAPS)).isEqualTo("Google Maps");
        assertThat(SheetsExporter.sourceLabel(LeadSource.YANDEX_REVIEW)).isEqualTo("Яндекс Отзывы");
        assertThat(SheetsExporter.sourceLabel(LeadSource.VACANCY)).isEqualTo("Вакансии");
        assertThat(SheetsExporter.sourceLabel(LeadSource.AVITO_JOB)).isEqualTo("Avito");
        assertThat(SheetsExporter.sourceLabel(LeadSource.DEMO)).isEqualTo("Демо");
        assertThat(SheetsExporter.sourceLabel(LeadSource.OTHER)).isEqualTo("Прочее");
    }

    @Test
    void formatBudgetGroupsThousandsWithSpaces() {
        assertThat(SheetsExporter.formatBudget(8398003.33)).isEqualTo("8 398 003");
        assertThat(SheetsExporter.formatBudget(999)).isEqualTo("999");
        assertThat(SheetsExporter.formatBudget(1000)).isEqualTo("1 000");
        assertThat(SheetsExporter.formatBudget(1000000)).isEqualTo("1 000 000");
    }

    @Test
    void formatBudgetRoundsToWholeUnits() {
        assertThat(SheetsExporter.formatBudget(728800.49)).isEqualTo("728 800");
        assertThat(SheetsExporter.formatBudget(728800.5)).isEqualTo("728 801");
    }

    @Test
    void formatBudgetHandlesNonNumberGracefully() {
        assertThat(SheetsExporter.formatBudget("не число")).isEqualTo("не число");
    }

    @Test
    void displayReasonAppendsSubjectAndBudgetForZakupkiLeads() {
        Company company = Company.builder()
                .name("Департамент Х")
                .source(LeadSource.ZAKUPKI)
                .raw(Map.of("subject", "Разработка сайта", "budget", 1500000.0))
                .build();
        Lead lead = Lead.builder().company(company).hotReason("упомянут бюджет").build();

        String result = SheetsExporter.displayReason(lead);

        assertThat(result).isEqualTo("упомянут бюджет | Госзакупка: Разработка сайта, бюджет 1 500 000 ₽");
    }

    @Test
    void displayReasonWorksWithoutAnExistingHotReason() {
        Company company = Company.builder()
                .name("Департамент Х")
                .source(LeadSource.ZAKUPKI)
                .raw(Map.of("subject", "Разработка сайта", "budget", 1500000.0))
                .build();
        Lead lead = Lead.builder().company(company).build();

        String result = SheetsExporter.displayReason(lead);

        assertThat(result).isEqualTo("Госзакупка: Разработка сайта, бюджет 1 500 000 ₽");
    }

    @Test
    void displayReasonFallsBackToPlaceholderWhenSubjectMissing() {
        Company company = Company.builder()
                .name("Департамент Х")
                .source(LeadSource.ZAKUPKI)
                .raw(Map.of("budget", 500000.0))
                .build();
        Lead lead = Lead.builder().company(company).build();

        assertThat(SheetsExporter.displayReason(lead)).isEqualTo("Госзакупка: предмет не указан, бюджет 500 000 ₽");
    }

    @Test
    void displayReasonIsUnchangedForNonZakupkiSources() {
        Company company = Company.builder()
                .name("Кофейня")
                .source(LeadSource.TWOGIS)
                .raw(Map.of("subject", "irrelevant here"))
                .build();
        Lead lead = Lead.builder().company(company).hotReason("нет сайта").build();

        assertThat(SheetsExporter.displayReason(lead)).isEqualTo("нет сайта");
    }

    @Test
    void displayReasonIsUnchangedWhenZakupkiLeadHasNeitherSubjectNorBudget() {
        Company company = Company.builder().name("Департамент Х").source(LeadSource.ZAKUPKI).build();
        Lead lead = Lead.builder().company(company).hotReason("упомянут бюджет").build();

        assertThat(SheetsExporter.displayReason(lead)).isEqualTo("упомянут бюджет");
    }

    // --- contact enrichment (workers/enrich) -------------------------------

    @Test
    void displayReasonSaysWhichContactsCameFromTheSiteAndWhere() {
        Company company = Company.builder()
                .name("Ромашка")
                .source(LeadSource.HH)
                .raw(Map.of(
                        "enrich_filled", List.of("email", "phone"),
                        "enrich_notes", "email с /kontakty/; телефон с главной"))
                .build();
        Lead lead = Lead.builder().company(company).hotReason("есть контакт+гео").build();

        assertThat(SheetsExporter.displayReason(lead)).isEqualTo(
                "есть контакт+гео | Контакт с сайта: email, телефон (email с /kontakty/; телефон с главной)");
    }

    @Test
    void anEnrichmentAttemptThatFilledNothingAddsNoNote() {
        Company company = Company.builder()
                .name("Ромашка")
                .source(LeadSource.HH)
                .raw(Map.of(
                        "enrich_attempted", true,
                        "enrich_filled", List.of(),
                        "enrich_notes", "сайт недоступен: ConnectError"))
                .build();
        Lead lead = Lead.builder().company(company).hotReason("прямой интент").build();

        assertThat(SheetsExporter.displayReason(lead)).isEqualTo("прямой интент");
    }

    @Test
    void zakupkiSummaryAndEnrichmentNoteAreBothKept() {
        Company company = Company.builder()
                .name("Департамент Х")
                .source(LeadSource.ZAKUPKI)
                .raw(Map.of("subject", "Разработка сайта", "enrich_filled", List.of("phone")))
                .build();
        Lead lead = Lead.builder().company(company).hotReason("упомянут бюджет").build();

        assertThat(SheetsExporter.displayReason(lead)).isEqualTo(
                "упомянут бюджет | Госзакупка: Разработка сайта | Контакт с сайта: телефон");
    }

    // --- the Email column ----------------------------------------------------

    @Test
    void headerHasAnEmailColumnAndRowsFillItFromTheCompany() {
        Company company = Company.builder()
                .name("Ромашка")
                .email("info@romashka.kz")
                .phone("+77273551020")
                .city("Алматы")
                .source(LeadSource.HH)
                .raw(Map.of("enrich_filled", List.of("email")))
                .build();
        Lead lead = Lead.builder().company(company).score(10).hotReason("есть контакт+гео").build();

        List<List<Object>> rows = SheetsExporter.buildRows(List.of(lead));

        int emailColumn = rows.get(0).indexOf("Email");
        assertThat(emailColumn).isNotNegative();
        assertThat(rows.get(1).get(emailColumn)).isEqualTo("info@romashka.kz");
        assertThat(rows.get(1)).hasSameSizeAs(rows.get(0));
        int reasonColumn = rows.get(0).indexOf("Причина");
        assertThat((String) rows.get(1).get(reasonColumn)).contains("Контакт с сайта: email");
    }

    @Test
    void aCompanyWithoutEmailGetsABlankCellNotNull() {
        Company company = Company.builder().name("Без почты").source(LeadSource.OSM).build();
        Lead lead = Lead.builder().company(company).build();

        List<List<Object>> rows = SheetsExporter.buildRows(List.of(lead));

        assertThat(rows.get(1).get(rows.get(0).indexOf("Email"))).isEqualTo("");
    }
}
