"""Newly-registered-domains worker (dormant until a paid feed is configured).

A fresh domain registration with no real site up yet is a strong "just
started a business, hasn't built a site" signal. There is no free stream
of newly-registered .ru/.com domains, though - this requires a paid feed
(WhoisXML's "Newly Registered Domains" product is wired in as a concrete
example; see README.md for cost and how to enable it).

Without DOMAINS_PROVIDER/DOMAINS_API_KEY configured, this worker logs a
friendly message and exits cleanly (exit code 0, no stack trace) - see
main.py. Once you pay for and configure a real feed, it starts working
without any code changes.
"""
