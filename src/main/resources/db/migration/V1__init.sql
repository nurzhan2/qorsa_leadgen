-- Core schema for the leadgen engine: companies discovered by any ingest
-- source, leads scored off of them, and outreach touches sent against leads.

CREATE TABLE companies (
    id                 UUID PRIMARY KEY,
    name               VARCHAR(255) NOT NULL,
    domain             VARCHAR(255),
    phone              VARCHAR(64),
    email              VARCHAR(255),
    messenger          VARCHAR(255),
    address            VARCHAR(500),
    city               VARCHAR(255),
    source             VARCHAR(32)  NOT NULL,
    source_url         VARCHAR(1000),
    has_site           BOOLEAN      NOT NULL DEFAULT FALSE,
    normalized_domain  VARCHAR(255),
    normalized_phone   VARCHAR(32),
    normalized_name    VARCHAR(255),
    raw                JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at         TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE INDEX idx_companies_normalized_domain ON companies (normalized_domain);
CREATE INDEX idx_companies_normalized_phone ON companies (normalized_phone);
CREATE INDEX idx_companies_city ON companies (city);

CREATE TABLE leads (
    id           UUID PRIMARY KEY,
    company_id   UUID         NOT NULL REFERENCES companies (id),
    score        INTEGER      NOT NULL DEFAULT 0,
    hot_reason   VARCHAR(1000),
    niche        VARCHAR(255),
    status       VARCHAR(32)  NOT NULL DEFAULT 'NEW',
    audit        JSONB,
    created_at   TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at   TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE INDEX idx_leads_company_id ON leads (company_id);
CREATE INDEX idx_leads_score ON leads (score DESC);
CREATE INDEX idx_leads_status ON leads (status);

CREATE TABLE outreach (
    id         UUID PRIMARY KEY,
    lead_id    UUID        NOT NULL REFERENCES leads (id),
    channel    VARCHAR(64) NOT NULL,
    message    TEXT,
    status     VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    created_at TIMESTAMP   NOT NULL DEFAULT now()
);

CREATE INDEX idx_outreach_lead_id ON outreach (lead_id);
