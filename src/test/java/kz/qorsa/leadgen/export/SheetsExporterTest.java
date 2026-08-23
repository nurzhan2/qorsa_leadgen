package kz.qorsa.leadgen.export;

import static org.assertj.core.api.Assertions.assertThat;

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
}
