"""Utility SQL queries for clawEASA."""

LIST_TABLES = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"

HEALTHCHECK = "SELECT 1"
