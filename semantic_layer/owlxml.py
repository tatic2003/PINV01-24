"""
Minimal OWL/XML loader.

rdflib reads RDF/XML, Turtle and similar, but not the OWL/XML syntax in which
ontology/extensions/iot_pnp.owl is written. Parsing it as RDF/XML fails
("Repeat node-elements inside property elements"), so the class hierarchy
never reached the RDF store and SHACL class targets (sh:targetClass
iot:IoTDevice) did not cover devices typed with a subclass.

This module covers the subset of OWL/XML that the project ontology uses:
Declaration, SubClassOf, Data/ObjectProperty Domain and Range,
AnnotationAssertion, Import and ontology annotations. Anything else is
skipped and counted in the returned stats, never silently dropped.

Project: PINV01-24 - FIUNA
"""

from pathlib import Path
from typing import Dict, Optional
import xml.etree.ElementTree as ET

from rdflib import Graph, Literal, URIRef, RDF, RDFS, OWL, XSD

_OWL_NS = "http://www.w3.org/2002/07/owl#"

_DECLARED_AS = {
    "Class": OWL.Class,
    "DataProperty": OWL.DatatypeProperty,
    "ObjectProperty": OWL.ObjectProperty,
    "AnnotationProperty": OWL.AnnotationProperty,
    "Datatype": RDFS.Datatype,
}

_DOMAIN_RANGE = {
    "DataPropertyDomain": RDFS.domain,
    "DataPropertyRange": RDFS.range,
    "ObjectPropertyDomain": RDFS.domain,
    "ObjectPropertyRange": RDFS.range,
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class _Resolver:
    """Resolves IRI, abbreviatedIRI and relative '#Name' references."""

    def __init__(self, root: ET.Element):
        self.base = root.get("{http://www.w3.org/XML/1998/namespace}base") \
            or root.get("ontologyIRI") or ""
        self.prefixes: Dict[str, str] = {}
        for el in root:
            if _local(el.tag) == "Prefix":
                self.prefixes[el.get("name", "")] = el.get("IRI", "")

    def iri(self, el: ET.Element) -> Optional[URIRef]:
        abbreviated = el.get("abbreviatedIRI")
        if abbreviated is not None:
            prefix, _, name = abbreviated.partition(":")
            if prefix not in self.prefixes:
                return None
            return URIRef(self.prefixes[prefix] + name)
        value = el.get("IRI")
        if value is None and _local(el.tag) == "IRI":
            value = (el.text or "").strip()
        if not value:
            return None
        if value.startswith("#"):
            return URIRef(self.base + value)
        return URIRef(value)


def _literal(el: ET.Element, resolver: _Resolver) -> Literal:
    text = el.text or ""
    lang = el.get("{http://www.w3.org/XML/1998/namespace}lang")
    datatype = el.get("datatypeIRI")
    if lang:
        return Literal(text, lang=lang)
    if datatype:
        return Literal(text, datatype=URIRef(datatype))
    return Literal(text)


def load_owlxml(path, graph: Graph) -> Dict[str, int]:
    """
    Parse an OWL/XML file and add its triples to ``graph``.

    Returns counters: {'triples': n_added, 'skipped': n_unsupported_axioms}.
    Raises ValueError if the file is not an OWL/XML ``Ontology`` document.
    """
    root = ET.parse(str(Path(path))).getroot()
    if _local(root.tag) != "Ontology":
        raise ValueError(f"{path} is not an OWL/XML document (root is {_local(root.tag)})")

    resolver = _Resolver(root)
    before = len(graph)
    skipped = 0

    ontology_iri = root.get("ontologyIRI")
    ontology_ref = URIRef(ontology_iri) if ontology_iri else None
    if ontology_ref is not None:
        graph.add((ontology_ref, RDF.type, OWL.Ontology))

    for el in root:
        kind = _local(el.tag)
        children = list(el)

        if kind == "Prefix":
            continue

        if kind == "Import":
            if ontology_ref is not None and (el.text or "").strip():
                graph.add((ontology_ref, OWL.imports, URIRef(el.text.strip())))

        elif kind == "Annotation":
            # Ontology-level annotation: AnnotationProperty + Literal
            prop = next((resolver.iri(c) for c in children
                         if _local(c.tag) == "AnnotationProperty"), None)
            lit = next((_literal(c, resolver) for c in children
                        if _local(c.tag) == "Literal"), None)
            if ontology_ref is not None and prop is not None and lit is not None:
                graph.add((ontology_ref, prop, lit))

        elif kind == "Declaration" and children:
            entity = children[0]
            entity_type = _DECLARED_AS.get(_local(entity.tag))
            iri = resolver.iri(entity)
            if entity_type is not None and iri is not None:
                graph.add((iri, RDF.type, entity_type))
            else:
                skipped += 1

        elif kind == "SubClassOf":
            terms = [resolver.iri(c) for c in children if _local(c.tag) == "Class"]
            if len(terms) == 2 and all(terms) and len(children) == 2:
                graph.add((terms[0], RDFS.subClassOf, terms[1]))
            else:
                skipped += 1  # class expressions (restrictions) are not supported

        elif kind in _DOMAIN_RANGE:
            terms = [resolver.iri(c) for c in children]
            if len(terms) == 2 and all(terms):
                graph.add((terms[0], _DOMAIN_RANGE[kind], terms[1]))
            else:
                skipped += 1

        elif kind == "AnnotationAssertion":
            prop = subject = value = None
            for c in children:
                tag = _local(c.tag)
                if tag == "AnnotationProperty":
                    prop = resolver.iri(c)
                elif tag == "IRI" and subject is None:
                    subject = resolver.iri(c)
                elif tag == "IRI":
                    value = resolver.iri(c)
                elif tag == "Literal":
                    value = _literal(c, resolver)
            if prop is not None and subject is not None and value is not None:
                graph.add((subject, prop, value))
            else:
                skipped += 1

        else:
            skipped += 1

    return {"triples": len(graph) - before, "skipped": skipped}
