"""2GIS Catalog worker.

Pulls companies out of the 2GIS Places/Catalog API for a configured list of
(city, business category) pairs and forwards them to the qorsa_leadgen Java
core via POST /api/v1/companies/ingest. A company with no website is a
strong "needs a site" signal, so it's flagged hasSite=false and the core's
own scoring gives it +40 - this worker does no scoring itself.
"""
