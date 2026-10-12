"""
Flask Application Factory
Creates and configures the IoT-ITS server.

Project: PINV01-24 - FIUNA
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, request, g

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from .config import Config
from semantic_layer import SemanticDeviceManager
from pnp_ai.device_classifier import DeviceClassifier
from pnp_ai.capability_rules import apply_capability_rules
from blockdag import BlockDAG, DAGStorage, build_dag_options


def _known_classes(classifier):
    """Classes a trained classifier can output; empty for the rule-based one."""
    encoder = getattr(classifier, 'label_encoder', None)
    return list(encoder.classes_) if encoder is not None else []


def create_app(config: Config = None) -> Flask:
    """
    Application factory for the IoT-ITS server.

    Args:
        config: Server configuration (uses defaults if None)

    Returns:
        Configured Flask application
    """
    app = Flask(__name__)
    config = config or Config()

    # Store config
    app.config['ITS_CONFIG'] = config

    # Setup logging
    _setup_logging(app, config)

    # Initialize components
    _init_components(app, config)

    # Register blueprints
    _register_routes(app)

    # Register error handlers
    _register_error_handlers(app)

    app.logger.info(f"IoT-ITS Server initialized (node: {config.node_id})")

    return app


def _setup_logging(app: Flask, config: Config):
    """Configure logging."""
    log_level = getattr(logging, config.log_level.upper())
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    if config.log_file:
        handler = logging.FileHandler(config.log_file)
        handler.setLevel(log_level)
        app.logger.addHandler(handler)


def _init_components(app: Flask, config: Config):
    """Initialize server components."""
    config.ensure_dirs()

    # Semantic Device Manager
    app.semantic_manager = SemanticDeviceManager(validate_on_register=True)
    app.logger.info("Semantic Device Manager initialized")

    # Device Classifier
    app.classifier = DeviceClassifier(
        model_type=config.classifier_type,
        feature_set=config.classifier_feature_set
    )
    if config.auto_train and config.classifier_type != 'rule_based':
        app.logger.info(f"Training classifier with {config.training_samples} samples...")
        try:
            from pnp_ai.device_classifier import train_classifier_from_synthetic_data
            app.classifier, metrics = train_classifier_from_synthetic_data(
                config.training_samples,
                config.classifier_type,
                feature_set=config.classifier_feature_set
            )
            app.logger.info(
                f"Classifier trained (accuracy: {metrics.get('accuracy', 'N/A')}, "
                f"feature_set: {metrics.get('feature_set')}, "
                f"features: {metrics.get('feature_names')})"
            )
        except Exception as e:
            # Falling back silently would leave the server answering with a
            # different model than the operator asked for, which would then
            # show up as unexplained results in the experiment logs.
            app.logger.error(f"Classifier training failed: {e}", exc_info=True)
            raise RuntimeError(
                f"Could not train the {config.classifier_type} classifier: {e}. "
                "Fix the cause, or start the server with --classifier rule_based."
            ) from e
    app.logger.info(
        f"Device Classifier initialized "
        f"({config.classifier_type}, features: {app.classifier.feature_set})"
    )

    # BlockDAG
    if config.enable_dag:
        dag_options = build_dag_options(
            key_file=config.dag_key_file,
            trusted_keys_file=config.dag_trusted_keys_file,
            require_signatures=config.dag_require_signatures
        )
        app.dag_storage = DAGStorage(dag_options=dag_options)
        app.dag = app.dag_storage.create(config.node_id)
        app.logger.info(
            "BlockDAG initialized "
            f"(signing: {'on' if 'signing_key' in dag_options else 'off'}, "
            f"require_signatures: {dag_options.get('require_signatures', False)})"
        )
    else:
        app.dag = None
        app.dag_storage = None


def _register_routes(app: Flask):
    """Register all API routes."""

    # ===================
    # Health & Status
    # ===================

    @app.route('/health', methods=['GET'])
    def health():
        """Health check endpoint."""
        return jsonify({
            'status': 'healthy',
            'version': '1.0.0',
            'node_id': app.config['ITS_CONFIG'].node_id,
            'timestamp': datetime.utcnow().isoformat()
        })

    @app.route('/api/status', methods=['GET'])
    def status():
        """Get server status."""
        config = app.config['ITS_CONFIG']
        stats = app.semantic_manager.get_statistics()

        response = {
            'node_id': config.node_id,
            'status': 'running',
            'devices': stats,
            'devices_count': stats.get('total', 0) if isinstance(stats, dict) else 0,
            'classifier': {
                'type': config.classifier_type,
                'trained': app.classifier.is_trained
            },
            'timestamp': datetime.utcnow().isoformat()
        }

        if app.dag:
            response['dag'] = {
                'enabled': True,
                'total_events': len(app.dag),
                'tips': len(app.dag.get_tips())
            }

        return jsonify(response)

    # ===================
    # Device Management
    # ===================

    @app.route('/api/devices/register', methods=['POST'])
    def register_device():
        """
        Register a new device.
        Compatible with ESP32 JSON announcements.
        """
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        app.logger.info(f"Device registration: {data.get('id', data.get('device_id', 'unknown'))}")

        # Classify the device
        classification = app.classifier.classify(data)
        # Safety net for models that predate the GNSSReceiver class.
        classification = apply_capability_rules(data, classification, _known_classes(app.classifier))

        # The classification is what types the device in the ontology, but only
        # when the classifier is confident enough. Below the threshold we do not
        # assert a specific class: the device is registered under the generic
        # one and flagged, so an operator can review it instead of a wrong type
        # being persisted silently.
        threshold = app.config['ITS_CONFIG'].classifier_confidence_threshold
        low_confidence = classification.confidence < threshold
        if low_confidence:
            app.logger.warning(
                f"Low-confidence classification for "
                f"{data.get('id', data.get('device_id', 'unknown'))}: "
                f"{classification.device_type} at {classification.confidence:.3f} "
                f"(threshold {threshold}). Registering as a generic device; "
                f"this usually means the announcement falls outside the "
                f"training distribution."
            )

        # Register with semantic layer
        result = app.semantic_manager.register_device(
            data,
            device_type=None if low_confidence else classification.device_type
        )

        if not result['success']:
            return jsonify(result), 400

        # Log to BlockDAG
        if app.dag:
            device_id = data.get('id') or data.get('device_id')
            app.dag.add_event('device_register', {
                'device_id': device_id,
                'hardware': data.get('hardware') or data.get('hardware_type', ''),
                'ip': data.get('ip') or data.get('ip_address', ''),
                'classification': classification.to_dict()
            })

        return jsonify({
            'status': 'registered',
            'device_id': result.get('device_id'),
            'classification': classification.to_dict(),
            # True when the classifier was below the configured threshold, so
            # the device was typed generically instead of by its prediction.
            'low_confidence': low_confidence,
            'confidence_threshold': threshold
        })

    @app.route('/api/devices', methods=['GET'])
    def get_devices():
        """Get all registered devices."""
        devices = app.semantic_manager.get_all_devices()
        return jsonify({
            'count': len(devices),
            'devices': devices
        })

    @app.route('/api/devices/<device_id>', methods=['GET'])
    def get_device(device_id):
        """Get a specific device."""
        device = app.semantic_manager.get_device(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404
        return jsonify(device)

    @app.route('/api/devices/<device_id>/state', methods=['PUT'])
    def update_device_state(device_id):
        """Update device state."""
        data = request.get_json()
        new_state = data.get('state')

        if not new_state:
            return jsonify({'error': 'State not provided'}), 400

        result = app.semantic_manager.update_state(device_id, new_state)

        if not result['success']:
            return jsonify(result), 400

        # Log to BlockDAG
        if app.dag:
            app.dag.add_event('state_change', {
                'device_id': device_id,
                'old_state': result.get('old_state'),
                'new_state': new_state
            })

        return jsonify(result)

    @app.route('/api/devices/<device_id>/heartbeat', methods=['POST'])
    def device_heartbeat(device_id):
        """Process device heartbeat."""
        result = app.semantic_manager.heartbeat(device_id)

        if not result['success']:
            return jsonify(result), 404

        return jsonify(result)

    @app.route('/api/devices/<device_id>', methods=['DELETE'])
    def remove_device(device_id):
        """Remove a device."""
        success = app.semantic_manager.remove_device(device_id)

        if not success:
            return jsonify({'error': 'Device not found'}), 404

        # Log to BlockDAG
        if app.dag:
            app.dag.add_event('device_remove', {'device_id': device_id})

        return jsonify({'status': 'removed', 'device_id': device_id})

    # ===================
    # Classification
    # ===================

    @app.route('/api/classify', methods=['POST'])
    def classify_device():
        """Classify a device without registering."""
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        classification = apply_capability_rules(data, app.classifier.classify(data), _known_classes(app.classifier))
        return jsonify(classification.to_dict())

    # ===================
    # Semantic / RDF
    # ===================

    @app.route('/api/semantic/export', methods=['GET'])
    def export_semantic():
        """Export devices in semantic format."""
        format_type = request.args.get('format', 'json')

        if format_type == 'json':
            return jsonify(app.semantic_manager.get_all_devices())
        elif format_type == 'turtle':
            rdf = app.semantic_manager.export_rdf(format='turtle')
            return rdf, 200, {'Content-Type': 'text/turtle'}
        elif format_type == 'xml':
            rdf = app.semantic_manager.export_rdf(format='xml')
            return rdf, 200, {'Content-Type': 'application/rdf+xml'}
        else:
            return jsonify({'error': f'Unknown format: {format_type}'}), 400

    @app.route('/api/semantic/validate', methods=['GET'])
    def validate_semantic():
        """Run SHACL validation on device graph."""
        validation = app.semantic_manager.validate_graph()
        return jsonify(validation.to_dict())

    # ===================
    # BlockDAG
    # ===================

    @app.route('/api/dag/status', methods=['GET'])
    def dag_status():
        """Get BlockDAG status."""
        if not app.dag:
            return jsonify({'error': 'BlockDAG not enabled'}), 501

        return jsonify({
            'status': 'active',
            'node_count': len(app.dag),
            'tips_count': len(app.dag.get_tips()),
            'node_id': app.config['ITS_CONFIG'].node_id
        })

    @app.route('/api/dag/events', methods=['GET'])
    def get_dag_events():
        """Get recent DAG events."""
        if not app.dag:
            return jsonify({'error': 'BlockDAG not enabled'}), 400

        limit = request.args.get('limit', 50, type=int)
        event_type = request.args.get('type')

        if event_type:
            events = app.dag.get_events_by_type(event_type)
        else:
            events = app.dag.get_tips()
            # Get recent events based on tips
            events = [app.dag.get_node(tip) for tip in events]

        return jsonify({
            'count': len(events),
            'events': [e.to_dict() if hasattr(e, 'to_dict') else str(e) for e in events[:limit]]
        })

    @app.route('/api/dag/sync', methods=['POST'])
    def sync_dag():
        """Synchronize DAG with another node."""
        if not app.dag:
            return jsonify({'error': 'BlockDAG not enabled'}), 400

        data = request.get_json()

        if data.get('action') == 'get':
            # Return our DAG data for sync
            since = data.get('since')
            sync_data = app.dag.get_sync_data(since)
            return jsonify(sync_data)

        elif data.get('action') == 'merge':
            # Merge incoming DAG data
            sync_data = data.get('sync_data')
            if not sync_data:
                return jsonify({'error': 'No sync_data provided'}), 400

            result = app.dag.merge_sync_data(sync_data)
            return jsonify(result)

        else:
            return jsonify({'error': 'Unknown action'}), 400

    @app.route('/api/dag/device/<device_id>/history', methods=['GET'])
    def get_device_history(device_id):
        """Get event history for a device."""
        if not app.dag:
            return jsonify({'error': 'BlockDAG not enabled'}), 400

        events = app.dag.get_events_by_type(f'device_{device_id}')
        # Also search in payload
        all_events = []
        for node_id in app.dag._nodes:
            node = app.dag.get_node(node_id)
            if node and node.payload.get('device_id') == device_id:
                all_events.append(node.to_dict())

        return jsonify({
            'device_id': device_id,
            'event_count': len(all_events),
            'events': all_events
        })


def _register_error_handlers(app: Flask):
    """Register error handlers."""

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({'error': 'Bad request', 'message': str(e)}), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(500)
    def internal_error(e):
        app.logger.error(f"Internal error: {e}")
        return jsonify({'error': 'Internal server error'}), 500
