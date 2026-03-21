"""Tests for ZIP archive extraction in the ingestion pipeline."""

import zipfile
from pathlib import Path

import pytest

from claw_easa.ingest.service import _materialize_parse_path


class TestMaterializeParsePath:
    def test_non_zip_returns_same_path(self, tmp_path):
        xml_file = tmp_path / "document.xml"
        xml_file.write_text("<root/>")
        assert _materialize_parse_path(xml_file) == xml_file

    def test_zip_with_single_xml(self, tmp_path):
        xml_content = b"<root><body>Test content</body></root>"
        zip_path = tmp_path / "source.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("document.xml", xml_content)

        result = _materialize_parse_path(zip_path)

        assert result.suffix == ".xml"
        assert result.exists()
        assert result.read_bytes() == xml_content

    def test_zip_with_multiple_xml_picks_largest(self, tmp_path):
        small = b"<meta/>"
        large = b"<root>" + b"<entry>x</entry>" * 100 + b"</root>"
        zip_path = tmp_path / "multi.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("metadata.xml", small)
            zf.writestr("regulation.xml", large)

        result = _materialize_parse_path(zip_path)

        assert result.exists()
        assert result.read_bytes() == large

    def test_zip_filters_opc_internal_files(self, tmp_path):
        content = b"<root>real document</root>"
        zip_path = tmp_path / "opc.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("[Content_Types].xml", b"<Types/>")
            zf.writestr("_rels/.rels", b"<Relationships/>")
            zf.writestr("document.xml", content)

        result = _materialize_parse_path(zip_path)

        assert result.exists()
        assert result.read_bytes() == content

    def test_zip_with_no_xml_raises(self, tmp_path):
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("readme.txt", "not xml")

        with pytest.raises(ValueError, match="No XML document found"):
            _materialize_parse_path(zip_path)

    def test_zip_with_nested_xml(self, tmp_path):
        content = b"<root>nested</root>"
        zip_path = tmp_path / "nested.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("subdir/regulation.xml", content)

        result = _materialize_parse_path(zip_path)

        assert result.suffix == ".xml"
        assert result.exists()
        assert result.read_bytes() == content
