"""
Tests for loading the OWL/XML extension ontology into the RDF store.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from semantic_layer import RDFStore, SHACLValidator


class TestOntologyLoading:
    """The OWL/XML extension ontology must reach the graph, because the SHACL
    class targets (sh:targetClass iot:IoTDevice) only cover devices typed with
    a subclass when the rdfs:subClassOf hierarchy is in the data graph."""

    def _invalid_esp32(self, store):
        # 70 characters violates maxLength 64 of IoTDeviceShape.
        store.register_device(
            {'device_id': 'a' * 70, 'ip_address': '10.0.0.1',
             'hardware_type': 'ESP32'},
            device_type='ESP32Device',
        )

    def test_hierarchy_is_loaded(self):
        from rdflib import RDFS
        from semantic_layer.rdf_store import IOT
        store = RDFStore(load_ontology=True)
        ancestors = set(store.graph.transitive_objects(IOT.ESP32Device, RDFS.subClassOf))
        assert IOT.Microcontroller in ancestors
        assert IOT.IoTDevice in ancestors

    def test_loader_reports_no_unsupported_axioms(self):
        from rdflib import Graph
        from semantic_layer.owlxml import load_owlxml
        path = Path(__file__).parent.parent / "ontology" / "extensions" / "iot_pnp.owl"
        stats = load_owlxml(path, Graph())
        assert stats['triples'] > 0
        assert stats['skipped'] == 0

    def test_loader_rejects_non_owlxml(self, tmp_path):
        from rdflib import Graph
        from semantic_layer.owlxml import load_owlxml
        bad = tmp_path / "x.owl"
        bad.write_text('<?xml version="1.0"?><root/>')
        with pytest.raises(ValueError):
            load_owlxml(bad, Graph())

    def test_subclass_device_is_validated_with_ontology(self):
        store = RDFStore(load_ontology=True)
        self._invalid_esp32(store)
        assert not SHACLValidator().validate(store.graph).conforms

    def test_subclass_device_escapes_validation_without_ontology(self):
        """Documents why the hierarchy matters: without it the same invalid
        device is not selected by the general shape and wrongly conforms."""
        store = RDFStore(load_ontology=False)
        self._invalid_esp32(store)
        assert SHACLValidator().validate(store.graph).conforms
