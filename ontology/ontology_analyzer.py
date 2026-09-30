"""
ITS Ontology Analyzer
Analyzes the structure of the ITS.owl ontology for the PINV01-24 project.

Author: Jason Hiroshi
Project: PINV01-24 - Interoperability of Plug-and-Play Devices for ITS
"""

from rdflib import Graph, Namespace, RDF, RDFS, OWL
from collections import defaultdict
from pathlib import Path
import json


# Define namespaces
ITS = Namespace("http://edsplab.us.es/ITSresearch/ITS#")
DC = Namespace("http://purl.org/dc/elements/1.1/")


def load_ontology(owl_file: str) -> Graph:
    """Load an OWL ontology file into an RDF graph."""
    g = Graph()
    g.parse(owl_file, format="xml")
    return g


def get_all_classes(g: Graph) -> list:
    """Extract all OWL classes from the graph."""
    classes = []
    for s in g.subjects(RDF.type, OWL.Class):
        if isinstance(s, str) or hasattr(s, 'n3'):
            classes.append(str(s))
    return sorted(classes)


def get_class_hierarchy(g: Graph) -> dict:
    """Build the class hierarchy (subClassOf relationships)."""
    hierarchy = defaultdict(list)

    for subclass, _, superclass in g.triples((None, RDFS.subClassOf, None)):
        sub_name = str(subclass).split('#')[-1] if '#' in str(subclass) else str(subclass).split('/')[-1]
        super_name = str(superclass).split('#')[-1] if '#' in str(superclass) else str(superclass).split('/')[-1]
        hierarchy[super_name].append(sub_name)

    return dict(hierarchy)


def get_top_level_classes(hierarchy: dict, all_classes: list) -> list:
    """Find classes that are not subclasses of any other class in the ontology."""
    all_subclasses = set()
    for subclasses in hierarchy.values():
        all_subclasses.update(subclasses)

    class_names = set()
    for c in all_classes:
        name = c.split('#')[-1] if '#' in c else c.split('/')[-1]
        class_names.add(name)

    top_level = class_names - all_subclasses
    return sorted(list(top_level))


def get_ontology_metadata(g: Graph) -> dict:
    """Extract ontology metadata (title, creator, date, etc.)."""
    metadata = {}

    # Find the ontology IRI
    for ont in g.subjects(RDF.type, OWL.Ontology):
        metadata['iri'] = str(ont)

        # Get annotations
        for pred, obj in g.predicate_objects(ont):
            pred_name = str(pred).split('/')[-1].split('#')[-1]
            if pred_name in ['title', 'creator', 'date', 'description', 'rights', 'label']:
                metadata[pred_name] = str(obj)

    return metadata


def analyze_ontology(owl_file: str) -> dict:
    """Complete analysis of an OWL ontology."""
    print(f"Loading ontology: {owl_file}")
    g = load_ontology(owl_file)

    print("Extracting classes...")
    all_classes = get_all_classes(g)

    print("Building hierarchy...")
    hierarchy = get_class_hierarchy(g)

    print("Finding top-level classes...")
    top_level = get_top_level_classes(hierarchy, all_classes)

    print("Extracting metadata...")
    metadata = get_ontology_metadata(g)

    analysis = {
        'metadata': metadata,
        'statistics': {
            'total_classes': len(all_classes),
            'top_level_classes': len(top_level),
            'total_relationships': sum(len(v) for v in hierarchy.values())
        },
        'top_level_classes': top_level,
        'hierarchy': hierarchy,
        'all_classes': [c.split('#')[-1] if '#' in c else c for c in all_classes]
    }

    return analysis


def print_hierarchy_tree(hierarchy: dict, root: str, indent: int = 0, max_depth: int = 3):
    """Print a tree view of the class hierarchy."""
    if indent > max_depth:
        return

    prefix = "  " * indent + ("├── " if indent > 0 else "")
    print(f"{prefix}{root}")

    if root in hierarchy:
        children = sorted(hierarchy[root])
        for child in children:
            print_hierarchy_tree(hierarchy, child, indent + 1, max_depth)


def main():
    base_path = Path(__file__).parent / "base"
    its_owl = base_path / "ITS.owl"

    if not its_owl.exists():
        print(f"Error: {its_owl} not found")
        return

    # Analyze the ontology
    analysis = analyze_ontology(str(its_owl))

    # Print results
    print("\n" + "="*60)
    print("ITS ONTOLOGY ANALYSIS REPORT")
    print("="*60)

    print("\n## Metadata")
    for key, value in analysis['metadata'].items():
        print(f"  {key}: {value[:80]}..." if len(str(value)) > 80 else f"  {key}: {value}")

    print(f"\n## Statistics")
    for key, value in analysis['statistics'].items():
        print(f"  {key}: {value}")

    print(f"\n## Top-Level Classes ({len(analysis['top_level_classes'])})")
    for cls in analysis['top_level_classes'][:20]:
        print(f"  - {cls}")
    if len(analysis['top_level_classes']) > 20:
        print(f"  ... and {len(analysis['top_level_classes']) - 20} more")

    print("\n## Sample Class Hierarchy (max depth 3)")
    # Print hierarchy for some key ITS classes
    key_classes = ['ITS', 'Intelligent_Infrastructures', 'Intelligent_Vehicles',
                   'Driver_Assistance_Systems', 'Surveillance']
    for root in key_classes:
        if root in analysis['hierarchy'] or root in [c for c in analysis['all_classes']]:
            print_hierarchy_tree(analysis['hierarchy'], root, max_depth=2)
            print()

    # Save full analysis to JSON
    output_file = Path(__file__).parent / "its_analysis.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
    print(f"\nFull analysis saved to: {output_file}")


if __name__ == "__main__":
    main()
