"""zakupki.gov.ru worker.

Searches the Russian state procurement portal's public search page for
purchases related to website/software development, and forwards the
customer (the government/municipal body or state-owned company placing
the order) to the qorsa_leadgen Java core as a HOT lead: the budget
(НМЦК) is real and already approved, and placing a public procurement
notice for a website/software is about as direct an intent signal as
exists.

There is no public JSON API for this search, so this worker parses the
HTML of the public search results page (see parser.py) - see README.md
for why, and for what to do when the page markup changes and this
breaks.
"""
