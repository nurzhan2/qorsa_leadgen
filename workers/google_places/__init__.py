"""Google Places API (New) worker.

Pulls companies out of Google Places API (New) - Text Search - for a
configured list of (city, category) pairs and forwards them to the
qorsa_leadgen Java core via POST /api/v1/companies/ingest.

Requires GOOGLE_PLACES_API_KEY with an active Google Cloud billing
account. If the key is missing, this worker logs a friendly message and
exits cleanly (no stack trace) - see main.py and README.md. If the key is
present but billing isn't enabled, Google's own error response is logged
clearly per request rather than crashing the process.
"""
