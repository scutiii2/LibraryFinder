"""Controlled catalog enrichment failures."""

class EnrichmentUnavailable(RuntimeError): pass

class EnrichmentResponseError(RuntimeError): pass

class EnrichmentTimeout(EnrichmentResponseError): pass

class EnrichmentCancelled(EnrichmentUnavailable): pass

