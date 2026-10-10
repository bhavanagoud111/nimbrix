"""URL prefix for API version 1.

Each module router declares this prefix itself (rather than inheriting it from
the aggregating router) so the matched route carries its full template, which
metrics use as a bounded-cardinality label.
"""

API_V1_PREFIX = "/api/v1"
