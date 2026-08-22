package kz.qorsa.leadgen.seed;

import java.util.List;
import java.util.Map;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.service.IngestService;
import kz.qorsa.leadgen.web.dto.IngestResponse;
import kz.qorsa.leadgen.web.dto.RawCompanyRequest;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.CommandLineRunner;
import org.springframework.context.annotation.Profile;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Component;

/**
 * Stand-in for a real scraper worker. Runs only under the "demo" profile and
 * feeds a handful of fake companies through the exact same
 * {@link IngestService#ingest(List)} entry point a Python worker would use
 * over HTTP - proving the pipeline (normalize -&gt; dedup -&gt; score -&gt; lead)
 * end to end without any real source integration.
 *
 * <p>The fixture deliberately includes: companies without a website, a
 * domain-based duplicate, a fuzzy-name duplicate, a direct-intent Telegram
 * order with a mentioned budget, and a competitor-complaint case - so the
 * logged top-5 output visibly demonstrates dedup collapsing and scoring
 * differentiating leads.
 */
@Component
@Profile("demo")
@Slf4j
public class DemoSeeder implements CommandLineRunner {

    private final IngestService ingestService;
    private final LeadRepository leadRepository;

    public DemoSeeder(IngestService ingestService, LeadRepository leadRepository) {
        this.ingestService = ingestService;
        this.leadRepository = leadRepository;
    }

    @Override
    public void run(String... args) {
        List<RawCompanyRequest> batch = List.of(
                // 1) No site, direct evidence of pain -> should score high.
                RawCompanyRequest.builder()
                        .name("Кофейня Ромашка")
                        .phone("+7 701 111 22 33")
                        .city("Almaty")
                        .address("ул. Абая 10")
                        .source(LeadSource.DEMO)
                        .hasSite(false)
                        .raw(Map.of("competitorNegativeReview", true))
                        .build(),

                // 2) Fuzzy-name duplicate of #1 (same city, near-identical name, legal form
                //    prefix added) but arrives with a phone number, so the merge should
                //    fill in the phone gap on the very first record.
                RawCompanyRequest.builder()
                        .name("ООО Ромашка Кофейня")
                        .city("Almaty")
                        .source(LeadSource.GOOGLE_MAPS)
                        .hasSite(false)
                        .build(),

                // 3) First sighting of this business: no phone captured, site unknown.
                RawCompanyRequest.builder()
                        .name("Sunrise Auto")
                        .domain("https://www.sunrise-auto.kz/")
                        .city("Astana")
                        .source(LeadSource.TWOGIS)
                        .hasSite(false)
                        .build(),

                // 4) Domain-based duplicate of #3 (formatted differently: no scheme/www) -
                //    proves domain dedup is order-independent and enrichment fills the
                //    phone gap while confirming hasSite=true.
                RawCompanyRequest.builder()
                        .name("Sunrise Auto Service")
                        .domain("sunrise-auto.kz")
                        .phone("+7 707 555 44 33")
                        .city("Astana")
                        .source(LeadSource.YANDEX_REVIEW)
                        .hasSite(true)
                        .build(),

                // 5) Direct intent via Telegram order with a mentioned budget -> hot lead.
                RawCompanyRequest.builder()
                        .name("Строй Мастер ТОО")
                        .phone("+7 702 333 44 55")
                        .city("Shymkent")
                        .source(LeadSource.TELEGRAM_ORDER)
                        .hasSite(false)
                        .raw(Map.of("budgetMentioned", true))
                        .build(),

                // 6) Vacancy source (direct intent) with contact + geo present.
                RawCompanyRequest.builder()
                        .name("Клининг Сервис Плюс")
                        .phone("+7 705 222 11 00")
                        .city("Karaganda")
                        .source(LeadSource.VACANCY)
                        .hasSite(false)
                        .build(),

                // 7) Avito job posting, direct intent, but already has a site -> lower score.
                RawCompanyRequest.builder()
                        .name("ИП Дизайн Студия Норд")
                        .email("contact@designnord.kz")
                        .city("Almaty")
                        .source(LeadSource.AVITO_JOB)
                        .hasSite(true)
                        .build(),

                // 8) Ordinary, unremarkable listing with a site and no other signals.
                RawCompanyRequest.builder()
                        .name("Book Cafe Central")
                        .domain("bookcafe-central.kz")
                        .phone("+7 700 999 88 77")
                        .city("Almaty")
                        .source(LeadSource.GOOGLE_MAPS)
                        .hasSite(true)
                        .build()
        );

        IngestResponse response = ingestService.ingest(batch);
        log.info("Demo seed ingested: created={}, merged={}, leadsScored={}, hotCount={}",
                response.getCreated(), response.getMerged(), response.getLeadsScored(), response.getHotCount());

        log.info("Top 5 leads after seeding:");
        List<Lead> top5 = leadRepository.findTopWithCompany(PageRequest.of(0, 5));
        for (Lead lead : top5) {
            log.info(" - [{}] {} score={} status={} reason=\"{}\"",
                    lead.getCompany().getCity(),
                    lead.getCompany().getName(),
                    lead.getScore(),
                    lead.getStatus(),
                    lead.getHotReason());
        }
    }
}
