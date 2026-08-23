package kz.qorsa.leadgen.export;

import static org.assertj.core.api.Assertions.assertThat;

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
}
