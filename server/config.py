"""
Server Configuration
Manages configuration for the IoT-ITS server.

Project: PINV01-24 - FIUNA
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import yaml


@dataclass
class Config:
    """Server configuration."""

    # Server settings
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False
    node_id: str = "edge-node-01"

    # Paths
    data_dir: str = "./data"
    ontology_dir: str = "./ontology"
    model_dir: str = "./models"

    # Device management
    device_timeout: float = 120.0  # seconds
    health_check_interval: float = 30.0  # seconds

    # Classification
    classifier_type: str = "rule_based"  # 'rule_based', 'decision_tree', 'random_forest'
    classifier_feature_set: str = "resource+name"  # 'resource' | 'resource+name'
    classifier_confidence_threshold: float = 0.6
    auto_train: bool = True
    training_samples: int = 500

    # BlockDAG
    enable_dag: bool = True
    dag_sync_interval: float = 60.0  # seconds
    max_dag_events: int = 10000
    dag_key_file: Optional[str] = None            # Ed25519 private key (hex); created if missing
    dag_trusted_keys_file: Optional[str] = None   # JSON {creator_id: public_key_hex}
    dag_require_signatures: bool = False          # reject unsigned / unknown-creator nodes

    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = None

    # Security (for future implementation)
    enable_auth: bool = False
    api_key: Optional[str] = None

    @classmethod
    def from_yaml(cls, filepath: str) -> 'Config':
        """Load configuration from YAML file."""
        with open(filepath, 'r') as f:
            data = yaml.safe_load(f)
        return cls(**data)

    @classmethod
    def from_env(cls) -> 'Config':
        """Load configuration from environment variables."""
        return cls(
            host=os.getenv('ITS_HOST', '0.0.0.0'),
            port=int(os.getenv('ITS_PORT', '5000')),
            debug=os.getenv('ITS_DEBUG', 'false').lower() == 'true',
            node_id=os.getenv('ITS_NODE_ID', 'edge-node-01'),
            data_dir=os.getenv('ITS_DATA_DIR', './data'),
            log_level=os.getenv('ITS_LOG_LEVEL', 'INFO'),
            classifier_type=os.getenv('ITS_CLASSIFIER', 'rule_based'),
            classifier_feature_set=os.getenv('ITS_FEATURE_SET', 'resource+name'),
            enable_dag=os.getenv('ITS_ENABLE_DAG', 'true').lower() == 'true',
            dag_key_file=os.getenv('ITS_DAG_KEY_FILE') or None,
            dag_trusted_keys_file=os.getenv('ITS_DAG_TRUSTED_KEYS_FILE') or None,
            dag_require_signatures=os.getenv('ITS_DAG_REQUIRE_SIGNATURES', 'false').lower() == 'true'
        )

    def to_yaml(self, filepath: str):
        """Save configuration to YAML file."""
        data = {
            'host': self.host,
            'port': self.port,
            'debug': self.debug,
            'node_id': self.node_id,
            'data_dir': self.data_dir,
            'ontology_dir': self.ontology_dir,
            'model_dir': self.model_dir,
            'device_timeout': self.device_timeout,
            'health_check_interval': self.health_check_interval,
            'classifier_type': self.classifier_type,
            'classifier_feature_set': self.classifier_feature_set,
            'classifier_confidence_threshold': self.classifier_confidence_threshold,
            'auto_train': self.auto_train,
            'training_samples': self.training_samples,
            'enable_dag': self.enable_dag,
            'dag_sync_interval': self.dag_sync_interval,
            'max_dag_events': self.max_dag_events,
            'dag_key_file': self.dag_key_file,
            'dag_trusted_keys_file': self.dag_trusted_keys_file,
            'dag_require_signatures': self.dag_require_signatures,
            'log_level': self.log_level,
            'log_file': self.log_file,
            'enable_auth': self.enable_auth
        }

        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)

    def ensure_dirs(self):
        """Ensure required directories exist."""
        for dir_path in [self.data_dir, self.model_dir]:
            Path(dir_path).mkdir(parents=True, exist_ok=True)


# Default configuration
DEFAULT_CONFIG = Config()
