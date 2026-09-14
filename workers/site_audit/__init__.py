"""Site audit worker: measures a company's website and reports the result to
the core, which turns it into lead score.

Two independent signals, either of which can stand alone:

  pagespeed  - Lighthouse performance score (0-100) from Google's PageSpeed
               Insights API. Needs PSI_API_KEY. PSI is NOT Places: it's free,
               25k requests/day, and does not require billing to be activated.
  auditFails - a count of failed checks from a local probe that needs no keys
               at all (TLS, redirects, mobile viewport, page weight, response
               time, basic SEO tags).

Run it with a PSI key for both signals, or without one for the local probe
alone. Either way the company is marked as audited, so a run is never
repeated against the same domain by accident.
"""
