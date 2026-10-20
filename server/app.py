"""
Flask Application Factory
Creates and configures the IoT-ITS server.

Project: PINV01-24 - FIUNA
"""

import logging
import sys
import time
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, request, g

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from .config import Config
from semantic_layer import SemanticDeviceManager
from pnp_ai.device_classifier import DeviceClassifier
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

    # Full SHACL validation on the registration path (optional). The shapes
    # and the ontology hierarchy are loaded once here, not per request.
    app.shacl_validator = None
    if getattr(config, 'shacl_on_register', False):
        from semantic_layer.validator import SHACLValidator
        app.shacl_validator = SHACLValidator()
        app.logger.info("SHACL validation on registration: on")

    # Device Classifier
    app.classifier = DeviceClassifier(
        model_type=config.classifier_type,
        feature_set=config.classifier_feature_set
    )
    if config.auto_train and config.classifier_type != 'rule_based':
        app.logger.info(
            f"Training classifier with {config.training_samples} samples "
            f"(source: {config.training_source})..."
        )
        try:
            from pnp_ai.device_classifier import train_classifier_from_synthetic_data
            generator = None
            if config.training_source == 'catalog':
                from pnp_ai.dataset_generator.catalog import (
                    CatalogDatasetGenerator, DEFAULT_LABELING)
                generator = CatalogDatasetGenerator(seed=42, labeling=DEFAULT_LABELING, calibrated=True)
                app.logger.info(f"Catalog version {generator.catalog.version}, "
                                f"{len(generator.catalog.boards)} boards, "
                                f"labeling {DEFAULT_LABELING}")
            elif config.training_source != 'profiles':
                raise ValueError(f"unknown training source {config.training_source!r} "
                                 "(expected 'profiles' or 'catalog')")
            app.classifier, metrics = train_classifier_from_synthetic_data(
                config.training_samples,
                config.classifier_type,
                feature_set=config.classifier_feature_set,
                generator=generator
            )
            app.logger.info(
                f"Classifier trained (accuracy: {metrics.get('accuracy', 'N/A')}, "
                f"source: {metrics.get('training_source')}, "
                f"classes: {_known_classes(app.classifier)}, "
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
                'trained': app.classifier.is_trained,
                'training_source': config.training_source,
                'classes': [str(c) for c in _known_classes(app.classifier)]
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
        # Wall-clock arrival (epoch ms) and a monotonic start for the stage
        # timings returned to the caller. They let the bridge separate the
        # HTTP transport from the server's own work in the onboarding latency.
        recv_epoch_ms = time.time() * 1000.0
        t_start = time.perf_counter()

        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        app.logger.info(f"Device registration: {data.get('id', data.get('device_id', 'unknown'))}")

        # Validate before classifying: a malformed announcement is rejected
        # with 400 without paying for the classification, and the classifier
        # never sees values it cannot convert (which used to end in a 500).
        t0 = time.perf_counter()
        validation = app.semantic_manager.validate_announcement(data)
        t_validate_ms = (time.perf_counter() - t0) * 1000.0
        if not validation.is_valid:
            return jsonify({
                'success': False,
                'error': 'Validation failed',
                'validation': validation.to_dict(),
                'timing_ms': {'semantic': round(t_validate_ms, 3)},
            }), 400

        # Classify the device
        t0 = time.perf_counter()
        try:
            classification = app.classifier.classify(data)
        except (TypeError, ValueError) as exc:
            return jsonify({'success': False, 'error': f'Cannot classify announcement: {exc}'}), 400
        t_classify_ms = (time.perf_counter() - t0) * 1000.0

        # The classification is what types the device in the ontology, but only
        # when the classifier is confident enough. Below the threshold we do not
        # assert a specific class: the device is registered under the generic
        # one and flagged, so an operator can review it instead of a wrong type
        # being persisted silently.
        threshold = app.config['ITS_CONFIG'].classifier_confidence_threshold
        low_confidence = classification.confidence < threshold
        # Below the threshold the device is typed with the generic class, never
        # with a class guessed from its hardware name.
        typed_class = 'IoTDevice' if low_confidence else classification.device_type
        if low_confidence:
            app.logger.warning(
                f"Low-confidence classification for "
                f"{data.get('id', data.get('device_id', 'unknown'))}: "
                f"{classification.device_type} at {classification.confidence:.3f} "
                f"(threshold {threshold}). Registering as a generic device; "
                f"this usually means the announcement falls outside the "
                f"training distribution."
            )

        # Full SHACL validation of this device's own graph, typed as it will be
        # stored. Validating a per-device graph keeps the cost independent of
        # how many devices are already registered.
        t_shacl_ms = None
        if app.shacl_validator is not None:
            from semantic_layer.rdf_store import RDFStore
            t0 = time.perf_counter()
            try:
                tmp = RDFStore(load_ontology=False)
                tmp.register_device(
                    app.semantic_manager._normalize_device_data(data),
                    device_type=typed_class)
                shacl = app.shacl_validator.validate(tmp.graph)
            except ValueError as exc:
                shacl = None
                shacl_error = str(exc)
            t_shacl_ms = (time.perf_counter() - t0) * 1000.0
            if shacl is None or not shacl.is_valid:
                return jsonify({
                    'success': False,
                    'error': 'SHACL validation failed',
                    'validation': shacl.to_dict() if shacl is not None else {'error': shacl_error},
                    'timing_ms': {'semantic': round(t_validate_ms, 3),
                                  'classify': round(t_classify_ms, 3),
                                  'shacl': round(t_shacl_ms, 3)},
                }), 400

        # Register with semantic layer (lightweight validation + RDF graph)
        t0 = time.perf_counter()
        result = app.semantic_manager.register_device(
            data,
            device_type=typed_class,
            validated=True
        )
        t_semantic_ms = t_validate_ms + (time.perf_counter() - t0) * 1000.0

        if not result['success']:
            return jsonify(result), 400

        # Log to BlockDAG
        t0 = time.perf_counter()
        if app.dag:
            device_id = data.get('id') or data.get('device_id')
            app.dag.add_event('device_register', {
                'device_id': device_id,
                'hardware': data.get('hardware') or data.get('hardware_type', ''),
                'ip': data.get('ip') or data.get('ip_address', ''),
                'classification': classification.to_dict()
            })
        t_dag_ms = (time.perf_counter() - t0) * 1000.0

        timing = {
            'classify': round(t_classify_ms, 3),
            'semantic': round(t_semantic_ms, 3),
            'dag': round(t_dag_ms, 3),
        }
        if t_shacl_ms is not None:
            timing['shacl'] = round(t_shacl_ms, 3)
        timing['total'] = round((time.perf_counter() - t_start) * 1000.0, 3)

        return jsonify({
            'status': 'registered',
            'device_id': result.get('device_id'),
            'classification': classification.to_dict(),
            # True when the classifier was below the configured threshold, so
            # the device was typed generically instead of by its prediction.
            'low_confidence': low_confidence,
            'confidence_threshold': threshold,
            # Stage timings of this request, in milliseconds. 'total' runs from
            # the start of the handler to just before the response is built.
            'server_recv_epoch_ms': round(recv_epoch_ms, 3),
            'timing_ms': timing,
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

        try:
            classification = app.classifier.classify(data)
        except (TypeError, ValueError) as exc:
            return jsonify({'error': f'Cannot classify announcement: {exc}'}), 400
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
