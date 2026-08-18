package kz.qorsa.leadgen;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class LeadgenApplication {

    public static void main(String[] args) {
        SpringApplication.run(LeadgenApplication.class, args);
    }
}
