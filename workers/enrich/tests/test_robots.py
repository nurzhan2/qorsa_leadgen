"""Tests for robots.py - RFC 9309 parsing and matching, and what each
robots.txt response status means. Pure - no network (the HTTP side is in
test_fetcher.py / test_enricher.py)."""

import pytest

from workers.enrich.robots import parse_robots, rules_for_status

TOKEN = "qorsa-leadgen-enrich"


def rules(text: str):
    return parse_robots(text, TOKEN)


# --- Disallow / Allow basics ------------------------------------------------------


def test_disallow_blocks_the_path_and_everything_under_it():
    r = rules("User-agent: *\nDisallow: /kontakty/\n")
    assert not r.can_fetch("/kontakty/")
    assert not r.can_fetch("/kontakty/almaty/")
    assert r.can_fetch("/kontakty")  # "/kontakty/" is not a prefix of "/kontakty"
    assert r.can_fetch("/")


def test_disallow_root_blocks_everything():
    r = rules("User-agent: *\nDisallow: /\n")
    assert not r.can_fetch("/")
    assert not r.can_fetch("/contacts/")
    assert r.blocking_rule("/contacts/") == "/"


def test_empty_disallow_allows_everything():
    assert rules("User-agent: *\nDisallow:\n").can_fetch("/anything")


def test_an_empty_file_allows_everything():
    assert rules("").can_fetch("/contacts/")


def test_robots_txt_itself_is_always_allowed():
    assert rules("User-agent: *\nDisallow: /\n").can_fetch("/robots.txt")


def test_query_strings_are_part_of_the_match():
    r = rules("User-agent: *\nDisallow: /search?\n")
    assert not r.can_fetch("/search?q=contacts")
    assert r.can_fetch("/search")


# --- groups --------------------------------------------------------------------------


def test_rules_for_other_bots_do_not_apply_to_us():
    assert rules("User-agent: Googlebot\nDisallow: /\n").can_fetch("/contacts/")


def test_a_group_naming_us_wins_over_the_star_group():
    r = rules("User-agent: *\nAllow: /\n\nUser-agent: qorsa-leadgen-enrich\nDisallow: /\n")
    assert not r.can_fetch("/")


def test_a_group_naming_us_can_also_be_more_permissive_than_star():
    r = rules("User-agent: *\nDisallow: /\n\nUser-agent: qorsa-leadgen-enrich\nDisallow: /admin/\n")
    assert r.can_fetch("/contacts/")
    assert not r.can_fetch("/admin/")


def test_our_name_matches_case_insensitively_and_ignores_a_version():
    r = rules("User-agent: Qorsa-Leadgen-Enrich/2.0\nDisallow: /private/\n")
    assert not r.can_fetch("/private/x")


def test_a_different_bot_whose_name_contains_ours_is_not_us():
    # Matching is on the product token, not on substrings.
    assert rules("User-agent: qorsa\nDisallow: /\n").can_fetch("/")


def test_consecutive_user_agent_lines_share_one_group():
    r = rules("User-agent: Googlebot\nUser-agent: *\nDisallow: /private/\n")
    assert not r.can_fetch("/private/")


def test_groups_for_the_same_agent_are_merged():
    r = rules("User-agent: *\nDisallow: /a/\n\nUser-agent: Yandex\nDisallow: /\n\n"
              "User-agent: *\nDisallow: /b/\n")
    assert not r.can_fetch("/a/")
    assert not r.can_fetch("/b/")
    assert r.can_fetch("/c/")


def test_rules_before_any_user_agent_line_belong_to_no_group():
    assert rules("Disallow: /\nUser-agent: *\nAllow: /\n").can_fetch("/contacts/")


# --- RFC 9309 matching: longest match, wildcards, encoding --------------------------


def test_longest_match_wins():
    r = rules("User-agent: *\nDisallow: /catalog/\nAllow: /catalog/contacts/\n")
    assert r.can_fetch("/catalog/contacts/")
    assert not r.can_fetch("/catalog/items/")


def test_allow_wins_a_tie():
    assert rules("User-agent: *\nDisallow: /page\nAllow: /page\n").can_fetch("/page")


def test_star_wildcard_and_dollar_anchor():
    r = rules("User-agent: *\nDisallow: /*?\nDisallow: /*.pdf$\n")
    assert not r.can_fetch("/catalog/?sort=price")
    assert not r.can_fetch("/files/price.pdf")
    assert r.can_fetch("/files/price.pdf.html")
    assert r.can_fetch("/catalog/")


def test_wildcard_in_the_middle():
    r = rules("User-agent: *\nDisallow: /*/print/\n")
    assert not r.can_fetch("/news/print/")
    assert r.can_fetch("/print/")


def test_percent_encoded_rules_match_decoded_paths():
    # "/контакты" percent-encoded, as a site owner would paste it from a browser.
    r = rules("User-agent: *\nDisallow: /%D0%BA%D0%BE%D0%BD%D1%82%D0%B0%D0%BA%D1%82%D1%8B\n")
    assert not r.can_fetch("/контакты/")
    assert not r.can_fetch("/%D0%BA%D0%BE%D0%BD%D1%82%D0%B0%D0%BA%D1%82%D1%8B/")


def test_a_rule_without_a_leading_slash_is_treated_as_rooted():
    assert not rules("User-agent: *\nDisallow: private\n").can_fetch("/private/")


# --- syntax tolerance ----------------------------------------------------------------


def test_comments_bom_crlf_and_odd_case_are_tolerated():
    text = "﻿USER-AGENT: *   # everyone\r\nDISALLOW: /private/ # keep out\r\n"
    r = rules(text)
    assert not r.can_fetch("/private/")
    assert r.can_fetch("/public/")


def test_unknown_lines_are_ignored():
    r = rules("User-agent: *\nSitemap: https://romashka.kz/sitemap.xml\nHost: romashka.kz\n"
              "Clean-param: utm_source /\nDisallow: /tmp/\n")
    assert not r.can_fetch("/tmp/")
    assert r.can_fetch("/")


def test_crawl_delay_is_read():
    assert rules("User-agent: *\nCrawl-delay: 5\n").crawl_delay == 5.0
    assert rules("User-agent: *\nCrawl-delay: 0,5\n").crawl_delay == 0.5
    assert rules("User-agent: *\nCrawl-delay: soon\n").crawl_delay is None
    assert rules("User-agent: Yandex\nCrawl-delay: 9\n").crawl_delay is None  # not our group


# --- what the response STATUS means --------------------------------------------------


def test_200_is_parsed():
    r = rules_for_status(200, b"User-agent: *\nDisallow: /kontakty/\n", TOKEN)
    assert not r.can_fetch("/kontakty/")
    assert r.can_fetch("/")


@pytest.mark.parametrize("status", [404, 410, 400])
def test_a_missing_robots_txt_means_everything_is_allowed(status):
    r = rules_for_status(status, b"", TOKEN)
    assert r.can_fetch("/kontakty/")
    assert str(status) in r.detail


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_refused_robots_txt_means_keep_out(status):
    r = rules_for_status(status, b"", TOKEN)
    assert not r.can_fetch("/")
    assert str(status) in r.detail


@pytest.mark.parametrize("status", [500, 502, 503])
def test_an_unreachable_robots_txt_means_complete_disallow(status):
    r = rules_for_status(status, b"", TOKEN)
    assert not r.can_fetch("/")
    assert "RFC 9309" in r.detail


def test_an_html_page_served_as_robots_txt_yields_no_rules():
    # Some sites answer /robots.txt with their homepage and a 200.
    r = rules_for_status(200, b"<html><body>Welcome: to our site</body></html>", TOKEN)
    assert r.can_fetch("/kontakty/")
