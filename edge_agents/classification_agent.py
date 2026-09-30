"""
Classification Agent - Handles automatic device classification
Uses PnP-AI models to classify and configure devices automatically.

Project: PINV01-24 - FIUNA
"""

import asyncio
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime
import sys
from pathlib import Path

from .base_agent import EdgeAgent, AgentConfig, AgentMessage

# Import PnP-AI components
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from pnp_ai.device_classifier import DeviceClassifier, ClassificationResult
    CLASSIFIER_AVAILABLE = True
except ImportError:
    CLASSIFIER_AVAILABLE = False


@dataclass
class ClassificationConfig(AgentConfig):
    """Configuration for the Classification Agent."""
    agent_type: str = "classification"
    model_type: str = "rule_based"  # 'rule_based', 'decision_tree', 'random_forest'
    model_path: Optional[str] = None
    auto_train: bool = True
    training_samples: int = 500
    confidence_threshold: float = 0.6
    cache_classifications: bool = True


class ClassificationAgent(EdgeAgent):
    """
    Agent responsible for automatic device classification and onboarding.

    Features:
    - Automatic device type classification
    - Role and capability assignment
    - Integration with semantic layer
    - Configurable ML or rule-based classification
    """

    def __init__(self, config: Optional[ClassificationConfig] = None):
        super().__init__(config or ClassificationConfig())
        self.config: ClassificationConfig = self.config

        self._classifier: Optional[DeviceClassifier] = None
        self._classification_cache: Dict[str, ClassificationResult] = {}
        self._pending_classifications: asyncio.Queue = asyncio.Queue()
        self._classification_count = 0

    async def setup(self) -> bool:
        """Setup the classification agent."""
        self.logger.info("Setting up Classification Agent")

        if not CLASSIFIER_AVAILABLE:
            self.logger.warning("DeviceClassifier not available, using rule-based only")
            self.config.model_type = 'rule_based'

        try:
            # Initialize classifier
            self._classifier = DeviceClassifier(model_type=self.config.model_type)

            # Load pre-trained model if specified
            if self.config.model_path:
                self._classifier.load(self.config.model_path)
                self.logger.info(f"Loaded model from {self.config.model_path}")

            # Auto-train with synthetic data if needed
            elif self.config.auto_train and self.config.model_type != 'rule_based':
                await self._train_classifier()

            return True

        except Exception as e:
            self.logger.error(f"Setup failed: {e}")
            return False

    async def _train_classifier(self) -> None:
        """Train the classifier with synthetic data."""
        self.logger.info(f"Training classifier with {self.config.training_samples} samples")

        try:
            from pnp_ai.device_classifier import train_classifier_from_synthetic_data

            # Run training in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            self._classifier, metrics = await loop.run_in_executor(
                None,
                train_classifier_from_synthetic_data,
                self.config.training_samples,
                self.config.model_type
            )

            self.logger.info(f"Training complete. Accuracy: {metrics.get('accuracy', 'N/A')}")

        except Exception as e:
            self.logger.error(f"Training failed: {e}, falling back to rule-based")
            self._classifier = DeviceClassifier(model_type='rule_based')

    async def process(self) -> None:
        """Main processing loop."""
        # Process pending classifications
        processed = 0
        max_per_cycle = 5

        while not self._pending_classifications.empty() and processed < max_per_cycle:
            try:
                device_data = self._pending_classifications.get_nowait()
                await self._classify_device(device_data)
                processed += 1
            except asyncio.QueueEmpty:
                break

    async def _classify_device(self, device_data: Dict[str, Any]) -> ClassificationResult:
        """Classify a single device."""
        device_id = device_data.get('id') or device_data.get('device_id', 'unknown')

        # Check cache
        if self.config.cache_classifications and device_id in self._classification_cache:
            self.logger.debug(f"Using cached classification for {device_id}")
            return self._classification_cache[device_id]

        # Perform classification
        result = self._classifier.classify(device_data)
        self._classification_count += 1

        # Log result
        self.logger.info(
            f"Classified {device_id}: {result.device_type} "
            f"(confidence: {result.confidence:.1%})"
        )

        # Check confidence threshold
        if result.confidence < self.config.confidence_threshold:
            self.logger.warning(
                f"Low confidence classification for {device_id}: {result.confidence:.1%}"
            )

        # Cache result
        if self.config.cache_classifications:
            self._classification_cache[device_id] = result

        # Emit event
        await self._emit('device_classified', {
            'device_id': device_id,
            'result': result.to_dict()
        })

        return result

    async def classify(self, device_data: Dict[str, Any]) -> ClassificationResult:
        """
        Classify a device immediately (synchronous API).

        Args:
            device_data: Device information dictionary

        Returns:
            ClassificationResult with type, confidence, and suggestions
        """
        return await self._classify_device(device_data)

    def queue_classification(self, device_data: Dict[str, Any]) -> None:
        """
        Queue a device for classification.

        Args:
            device_data: Device information dictionary
        """
        try:
            self._pending_classifications.put_nowait(device_data)
        except asyncio.QueueFull:
            self.logger.warning("Classification queue full")

    async def handle_message(self, message: AgentMessage) -> None:
        """Handle messages from other agents."""
        if message.message_type == 'classify_device':
            device_data = message.payload.get('device')
            if device_data:
                self.queue_classification(device_data)

        elif message.message_type == 'device_discovered':
            # Auto-classify newly discovered devices
            device_data = message.payload
            if device_data:
                self.queue_classification(device_data)

    async def cleanup(self) -> None:
        """Cleanup resources."""
        # Save model if path specified
        if self.config.model_path and self._classifier:
            try:
                self._classifier.save(self.config.model_path)
                self.logger.info(f"Saved model to {self.config.model_path}")
            except Exception as e:
                self.logger.error(f"Failed to save model: {e}")

    def get_classification(self, device_id: str) -> Optional[ClassificationResult]:
        """Get cached classification for a device."""
        return self._classification_cache.get(device_id)

    def clear_cache(self) -> None:
        """Clear the classification cache."""
        self._classification_cache.clear()
        self.logger.info("Classification cache cleared")

    def get_status(self) -> Dict[str, Any]:
        """Get agent status with classification-specific info."""
        status = super().get_status()
        status.update({
            'model_type': self.config.model_type,
            'cached_classifications': len(self._classification_cache),
            'total_classifications': self._classification_count,
            'pending_classifications': self._pending_classifications.qsize(),
            'confidence_threshold': self.config.confidence_threshold
        })
        return status
