package kz.qorsa.leadgen.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

class PhoneUtilTest {

    // --- normalizePhones(): splitting + normalization -----------------

    @Test
    void normalizesASingleFormattedNumberToE164() {
        assertThat(PhoneUtil.normalizePhones("+7 (916) 123-45-67")).containsExactly("+79161234567");
        assertThat(PhoneUtil.normalizePhones("8 916 123 45 67")).containsExactly("+79161234567");
        assertThat(PhoneUtil.normalizePhones("89161234567")).containsExactly("+79161234567");
        assertThat(PhoneUtil.normalizePhones("+79161234567")).containsExactly("+79161234567");
    }

    @Test
    void normalizesABarePrefixlessMobileNumber() {
        assertThat(PhoneUtil.normalizePhones("9161234567")).containsExactly("+79161234567");
    }

    @Test
    void splitsMultipleNumbersSeparatedBySemicolon() {
        assertThat(PhoneUtil.normalizePhones("+7 916 123-45-67; +7 495 123-45-67"))
                .containsExactly("+79161234567", "+74951234567");
    }

    @Test
    void splitsMultipleNumbersSeparatedByComma() {
        assertThat(PhoneUtil.normalizePhones("+79161234567, 84951234567"))
                .containsExactly("+79161234567", "+74951234567");
    }

    @Test
    void splitsMultipleNumbersSeparatedBySlash() {
        assertThat(PhoneUtil.normalizePhones("+79161234567/+78121234567"))
                .containsExactly("+79161234567", "+78121234567");
    }

    @Test
    void splitsMultipleNumbersSeparatedByPlainWhitespace() {
        assertThat(PhoneUtil.normalizePhones("+7 916 123-45-67 +7 495 123-45-67"))
                .containsExactly("+79161234567", "+74951234567");
    }

    @Test
    void blankOrNullInputYieldsEmptyList() {
        assertThat(PhoneUtil.normalizePhones(null)).isEmpty();
        assertThat(PhoneUtil.normalizePhones("")).isEmpty();
        assertThat(PhoneUtil.normalizePhones("   ")).isEmpty();
    }

    @Test
    void garbageTextWithNoDigitsYieldsEmptyList() {
        assertThat(PhoneUtil.normalizePhones("звоните нам!")).isEmpty();
    }

    // --- classify() -----------------------------------------------------

    @Test
    void classifiesMobileNumbers() {
        assertThat(PhoneUtil.classify("+79161234567")).isEqualTo(PhoneType.MOBILE);
        assertThat(PhoneUtil.classify("89031234567")).isEqualTo(PhoneType.MOBILE);
    }

    @Test
    void classifiesMoscowCityNumbers() {
        assertThat(PhoneUtil.classify("+74951234567")).isEqualTo(PhoneType.CITY_MOSCOW);
        assertThat(PhoneUtil.classify("+74991234567")).isEqualTo(PhoneType.CITY_MOSCOW);
    }

    @Test
    void classifiesSpbCityNumbers() {
        assertThat(PhoneUtil.classify("+78121234567")).isEqualTo(PhoneType.CITY_SPB);
    }

    @Test
    void classifiesOtherCityNumbers() {
        assertThat(PhoneUtil.classify("+73831234567")).isEqualTo(PhoneType.CITY_OTHER); // Novosibirsk
        assertThat(PhoneUtil.classify("+73431234567")).isEqualTo(PhoneType.CITY_OTHER); // Yekaterinburg
    }

    @Test
    void classifiesTollFreeNumbers() {
        assertThat(PhoneUtil.classify("+78001234567")).isEqualTo(PhoneType.TOLL_FREE);
        assertThat(PhoneUtil.classify("8-800-123-45-67")).isEqualTo(PhoneType.TOLL_FREE);
    }

    @Test
    void classifiesUnparseableInputAsUnknown() {
        assertThat(PhoneUtil.classify(null)).isEqualTo(PhoneType.UNKNOWN);
        assertThat(PhoneUtil.classify("")).isEqualTo(PhoneType.UNKNOWN);
        assertThat(PhoneUtil.classify("нет телефона")).isEqualTo(PhoneType.UNKNOWN);
        assertThat(PhoneUtil.classify("12345")).isEqualTo(PhoneType.UNKNOWN);
    }

    // --- pickPrimary(): priority MOBILE > CITY_* > TOLL_FREE > UNKNOWN --

    @Test
    void pickPrimaryPrefersMobileOverEverythingElse() {
        List<String> phones = List.of("+78001234567", "+74951234567", "+79161234567");

        assertThat(PhoneUtil.pickPrimary(phones)).isEqualTo("+79161234567");
    }

    @Test
    void pickPrimaryPrefersCityOverTollFreeWhenNoMobile() {
        List<String> phones = List.of("+78001234567", "+74951234567");

        assertThat(PhoneUtil.pickPrimary(phones)).isEqualTo("+74951234567");
    }

    @Test
    void pickPrimaryFallsBackToTollFreeWhenItsTheOnlyOption() {
        List<String> phones = List.of("+78001234567");

        assertThat(PhoneUtil.pickPrimary(phones)).isEqualTo("+78001234567");
    }

    @Test
    void pickPrimaryKeepsFirstMobileWhenSeveralMobilesPresent() {
        List<String> phones = List.of("+79161234567", "+79031234567");

        assertThat(PhoneUtil.pickPrimary(phones)).isEqualTo("+79161234567");
    }

    @Test
    void pickPrimaryOfEmptyOrNullListIsNull() {
        assertThat(PhoneUtil.pickPrimary(List.of())).isNull();
        assertThat(PhoneUtil.pickPrimary(null)).isNull();
    }
}
