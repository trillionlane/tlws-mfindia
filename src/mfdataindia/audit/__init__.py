"""Governed, application-level data-quality audits.

These run against the whole database on an operator-approved cadence and write
only to their own assessment tables. They are never part of an API request
path and never mutate mf.nav_history.
"""
