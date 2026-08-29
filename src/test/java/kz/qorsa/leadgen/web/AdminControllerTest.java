package kz.qorsa.leadgen.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import kz.qorsa.leadgen.config.AdminProperties;
import kz.qorsa.leadgen.service.AdminService;
import kz.qorsa.leadgen.web.dto.DemoDataDeletionResponse;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.web.server.ResponseStatusException;

/**
 * The access guard on {@link AdminController} is pure decision logic, so it's
 * tested here without a Spring context or a database - the deletion itself is
 * covered end-to-end by {@link AdminControllerIT}.
 *
 * <p>The important case is the FIRST one: an instance that opted into nothing
 * must refuse, because that's what every production deployment looks like.
 */
@ExtendWith(MockitoExtension.class)
class AdminControllerTest {

    @Mock
    private AdminService adminService;

    private AdminController controllerWith(boolean adminEnabled, String... activeProfiles) {
        AdminProperties properties = new AdminProperties();
        properties.setEnabled(adminEnabled);
        MockEnvironment environment = new MockEnvironment();
        environment.setActiveProfiles(activeProfiles);
        return new AdminController(adminService, properties, environment);
    }

    @Test
    void refusesWithForbiddenWhenNeitherDemoProfileNorAdminEnabledIsSet() {
        AdminController controller = controllerWith(false);

        assertThatThrownBy(controller::deleteDemoData)
                .isInstanceOf(ResponseStatusException.class)
                .satisfies(ex ->
                        assertThat(((ResponseStatusException) ex).getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN));
    }

    @Test
    void doesNotTouchTheDatabaseWhenItRefuses() {
        AdminController controller = controllerWith(false);

        assertThatThrownBy(controller::deleteDemoData).isInstanceOf(ResponseStatusException.class);

        verifyNoInteractions(adminService);
    }

    @Test
    void anUnrelatedActiveProfileDoesNotUnlockIt() {
        AdminController controller = controllerWith(false, "prod");

        assertThatThrownBy(controller::deleteDemoData).isInstanceOf(ResponseStatusException.class);

        verify(adminService, never()).deleteDemoData();
    }

    @Test
    void allowsDeletionWhenAdminEnabledIsTrue() {
        when(adminService.deleteDemoData()).thenReturn(
                DemoDataDeletionResponse.builder().deleted(8).deletedLeads(6).deletedOutreach(0).build());
        AdminController controller = controllerWith(true);

        ResponseEntity<DemoDataDeletionResponse> response = controller.deleteDemoData();

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().getDeleted()).isEqualTo(8);
        verify(adminService).deleteDemoData();
    }

    @Test
    void allowsDeletionWhenTheDemoProfileIsActiveEvenWithAdminEnabledFalse() {
        when(adminService.deleteDemoData()).thenReturn(
                DemoDataDeletionResponse.builder().deleted(3).build());
        AdminController controller = controllerWith(false, "demo");

        ResponseEntity<DemoDataDeletionResponse> response = controller.deleteDemoData();

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody().getDeleted()).isEqualTo(3);
        verify(adminService).deleteDemoData();
    }
}
