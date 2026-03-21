"""Ingestion pipeline for clawEASA."""

from claw_easa.ingest.service import fetch_source, parse_source

__all__ = ["fetch_source", "parse_source"]
