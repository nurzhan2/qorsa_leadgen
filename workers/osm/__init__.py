"""OpenStreetMap worker.

Pulls companies out of OpenStreetMap (via the public Overpass API) for a
configured list of (city bbox, OSM tag) pairs and forwards them to the
qorsa_leadgen Java core via POST /api/v1/companies/ingest. Free, no API
key - but the public Overpass instance is rate-limited, so this worker
paces its requests deliberately (see README.md).

A company with no `website`/`contact:website` tag is a genuinely reliable
"needs a site" signal here (unlike some other sources): OSM contributors
tag what they observe, so hasSite=false means the tag was genuinely
absent, not that the API withheld it.
"""
