#!/usr/bin/env python3
"""
IoT-ITS Server Entry Point
Run the production REST API server.

Usage:
    python run_server.py                     # Run with defaults
    python run_server.py --port 8080         # Custom port
    python run_server.py --config config.yml # Load config file
    python run_server.py --debug             # Debug mode

Project: PINV01-24 - FIUNA
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from server import create_app, Config


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='IoT-ITS Interoperability Server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_server.py                        Run with defaults (port 5000)
  python run_server.py --port 8080            Run on custom port
  python run_server.py --node-id gateway-01   Set node ID
  python run_server.py --debug                Enable debug mode
  python run_server.py --config config.yml    Load from config file
        """
    )

    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='Host to bind to (default: 0.0.0.0)'
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=5000,
        help='Port to listen on (default: 5000)'
    )
    parser.add_argument(
        '--node-id',
        default='edge-node-01',
        help='Node identifier (default: edge-node-01)'
    )
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        help='Enable debug mode'
    )
    parser.add_argument(
        '--config', '-c',
        help='Path to YAML config file'
    )
    parser.add_argument(
        '--classifier',
        choices=['rule_based', 'decision_tree', 'random_forest'],
        default='rule_based',
        help='Classifier type (default: rule_based)'
    )
    parser.add_argument(
        '--feature-set',
        choices=['resource', 'resource+name'],
        default='resource+name',
        help='Features used by the ML classifier. "resource" uses only '
             'quantitative resource characteristics; "resource+name" also '
             'uses flags derived from the hardware model string '
             '(default: resource+name)'
    )
    parser.add_argument(
        '--training-source',
        choices=['profiles', 'catalog'],
        default='profiles',
        help='Training data for the ML classifier: "profiles" = hand-written '
             'synthetic profiles (5 classes); "catalog" = real-board catalog '
             'with calibrated memory, as in the paper (default: profiles)'
    )
    parser.add_argument(
        '--training-samples',
        type=int,
        default=500,
        help='Training announcements for the ML classifier (default: 500)'
    )
    parser.add_argument(
        '--no-dag',
        action='store_true',
        help='Disable BlockDAG event logging'
    )
    parser.add_argument(
        '--dag-key-file',
        default=None,
        help='Ed25519 private key file (hex) used to sign the DAG nodes this '
             'server creates; created if it does not exist. Without it the '
             'nodes are created unsigned. Also: ITS_DAG_KEY_FILE'
    )
    parser.add_argument(
        '--dag-trusted-keys',
        default=None,
        help='JSON file {creator_id: public_key_hex} with the public keys of '
             'the servers whose DAG nodes are accepted. Also: ITS_DAG_TRUSTED_KEYS_FILE'
    )
    parser.add_argument(
        '--dag-require-signatures',
        action='store_true',
        help='Reject DAG nodes that are unsigned or come from an unknown '
             'creator. Also: ITS_DAG_REQUIRE_SIGNATURES=true'
    )
    parser.add_argument(
        '--shacl-on-register',
        action='store_true',
        help='Validate every registration with the full SHACL shapes (and the '
             'ontology hierarchy) after the lightweight check. '
             'Also: ITS_SHACL_ON_REGISTER=true'
    )
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level (default: INFO)'
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Load or create configuration
    if args.config:
        print(f"Loading configuration from: {args.config}")
        config = Config.from_yaml(args.config)
    else:
        config = Config(
            host=args.host,
            port=args.port,
            node_id=args.node_id,
            debug=args.debug,
            classifier_type=args.classifier,
            classifier_feature_set=args.feature_set,
            training_source=args.training_source,
            training_samples=args.training_samples,
            enable_dag=not args.no_dag,
            dag_key_file=args.dag_key_file or os.getenv('ITS_DAG_KEY_FILE') or None,
            dag_trusted_keys_file=(
                args.dag_trusted_keys or os.getenv('ITS_DAG_TRUSTED_KEYS_FILE') or None
            ),
            dag_require_signatures=(
                args.dag_require_signatures
                or os.getenv('ITS_DAG_REQUIRE_SIGNATURES', 'false').lower() == 'true'
            ),
            shacl_on_register=(
                args.shacl_on_register
                or os.getenv('ITS_SHACL_ON_REGISTER', 'false').lower() == 'true'
            ),
            log_level=args.log_level
        )

    # Print startup banner
    print("=" * 60)
    print(" IoT-ITS Interoperability Server")
    print(" Project: PINV01-24 - FIUNA")
    print("=" * 60)
    print(f" Node ID:    {config.node_id}")
    print(f" Host:       {config.host}")
    print(f" Port:       {config.port}")
    print(f" Classifier: {config.classifier_type}")
    if config.classifier_type != 'rule_based':
        print(f" Features:   {config.classifier_feature_set}")
    print(f" BlockDAG:   {'enabled' if config.enable_dag else 'disabled'}")
    if config.enable_dag:
        print(f" DAG signing: {'on' if config.dag_key_file else 'off'}"
              f" (require signatures: {config.dag_require_signatures})")
    if config.classifier_type != 'rule_based':
        print(f" Training:   {config.training_source}, {config.training_samples} samples")
    print(f" SHACL on register: {'on' if config.shacl_on_register else 'off'}")
    print(f" Debug:      {config.debug}")
    print("=" * 60)

    # Create and run application
    app = create_app(config)

    print(f"\nServer starting at http://{config.host}:{config.port}")
    print("Press Ctrl+C to stop\n")

    try:
        app.run(
            host=config.host,
            port=config.port,
            debug=config.debug,
            threaded=True
        )
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == '__main__':
    main()
