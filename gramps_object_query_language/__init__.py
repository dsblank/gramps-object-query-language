"""Object query language and SQL compiler for Gramps data.

Standalone, privacy-agnostic: compiles a structured query (select/where/
order_by/limit/after) or an "almost Python" expression string into
parameterized SQL against Gramps' flattened secondary columns. Carries no
knowledge of proxies, permissions, or Gramps Web API request handling --
callers are responsible for only invoking it against an unproxied database
handle.
"""
