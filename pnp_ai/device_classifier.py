"""
Lightweight Device Classifier for PnP-AI
Designed to run on edge devices with limited resources (Raspberry Pi, Jetson Nano).

Uses simple, efficient models that don't require GPU acceleration.

Project: PINV01-24 - FIUNA
"""

import json
import pickle
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
import numpy as np

from .features import (
    DEFAULT_FEATURE_SET,
    FEATURE_SET_RESOURCE,
    FEATURE_SET_RESOURCE_NAME,
    FEATURE_SETS,
    extract_features,
    feature_names as feature_names_for,
    features_to_vector,
    declared_capabilities,
    GNSS_CAPABILITY_NAMES,
    hardware_name,
    normalize_announcement,
)

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.svm import LinearSVC
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import LabelEncoder, StandardScaler
    from sklearn.model_selection import train_test_split, learning_curve
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend for server/CI environments
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


@dataclass
class ClassificationResult:
    """Result of device classification."""
    device_type: str
    confidence: float
    all_probabilities: Dict[str, float]
    suggested_role: str
    suggested_capabilities: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'device_type': self.device_type,
            'confidence': self.confidence,
            'all_probabilities': self.all_probabilities,
            'suggested_role': self.suggested_role,
            'suggested_capabilities': self.suggested_capabilities
        }


class DeviceClassifier:
    """
    Lightweight classifier for automatic device type identification.

    Features:
    - Uses decision tree or random forest (low memory footprint)
    - Can be exported to ONNX for embedded deployment
    - Rule-based fallback when ML is unavailable
    """

    # Rule-based classification thresholds
    MEMORY_THRESHOLDS = {
        'microcontroller': 1_000_000,      # < 1MB
        'sbc_low': 1_000_000_000,          # < 1GB
        'sbc_high': 8_000_000_000,         # < 8GB
    }

    # Device type to capability mapping
    DEVICE_CAPABILITIES = {
        'ESP32Device': ['sensing', 'actuation', 'wifi', 'bluetooth', 'low_power'],
        'SingleBoardComputer': ['processing', 'edge_ai', 'gateway', 'storage', 'visualization'],
        'IoTGateway': ['protocol_conversion', 'data_aggregation', 'routing', 'security'],
        'Sensor': ['sensing', 'low_power', 'battery'],
        'Actuator': ['actuation', 'control'],
        'Microcontroller': ['sensing', 'actuation', 'low_power'],
        'GNSSReceiver': ['gnss', 'position', 'time']
    }

    # Device type to typical role mapping
    DEVICE_ROLES = {
        'ESP32Device': ['sensor', 'actuator', 'monitor'],
        'SingleBoardComputer': ['gateway', 'edge_server', 'controller'],
        'IoTGateway': ['gateway', 'protocol_converter'],
        'Sensor': ['sensor'],
        'Actuator': ['actuator'],
        'Microcontroller': ['sensor', 'actuator'],
        'GNSSReceiver': ['sensor']
    }

    def __init__(
        self,
        model_type: str = 'decision_tree',
        feature_set: str = DEFAULT_FEATURE_SET
    ):
        """
        Initialize the classifier.

        Args:
            model_type: 'decision_tree', 'random_forest', 'svm', or 'mlp'
            feature_set: which features to use, see pnp_ai.features.
                'resource'      -> quantitative resource characteristics only
                'resource+name' -> also flags derived from the model name
        """
        if feature_set not in FEATURE_SETS:
            raise ValueError(
                f"Unknown feature_set {feature_set!r}. Expected one of {FEATURE_SETS}."
            )

        self.model_type = model_type
        self.feature_set = feature_set
        self.model = None
        self.label_encoder = None
        self.scaler = None
        # Fixed at construction time: the column order can no longer depend on
        # whichever announcement happened to arrive first.
        self.feature_names = feature_names_for(feature_set)
        self.is_trained = False

        if model_type != 'rule_based' and not SKLEARN_AVAILABLE:
            print("Warning: scikit-learn not available, falling back to rule-based")
            self.model_type = 'rule_based'

    def _extract_features(self, device_data: Dict[str, Any]) -> Dict[str, float]:
        """
        Extract numerical features from a device announcement.

        Delegates to pnp_ai.features so that training and inference can never
        build different feature vectors.
        """
        return extract_features(device_data, self.feature_set)

    def _features_to_array(self, features: Dict[str, float]) -> np.ndarray:
        """Convert feature dict to numpy array, in the model's column order."""
        return np.array(features_to_vector(features, self.feature_names))

    def train(
        self,
        features: List[Dict[str, float]],
        labels: List[str],
        test_size: float = 0.2
    ) -> Dict[str, Any]:
        """
        Train the classifier on labeled data.

        Args:
            features: List of feature dictionaries. Every dictionary must
                contain at least this classifier's feature set; extra keys
                (for example oracle features such as the device role) are
                ignored, so a model can never be trained on a signal that is
                unavailable at registration time.
            labels: List of device type labels
            test_size: Fraction of data to use for testing

        Returns:
            Dictionary with training metrics
        """
        if self.model_type == 'rule_based':
            self.is_trained = True
            return {'model': 'rule_based', 'accuracy': 'N/A'}

        if not SKLEARN_AVAILABLE:
            raise RuntimeError("scikit-learn required for ML training")

        # Prepare data. The column order comes from the feature set, not from
        # the training data, and features_to_vector fails loudly if a required
        # feature is absent.
        X = np.array([features_to_vector(f, self.feature_names) for f in features])

        # Encode labels
        self.label_encoder = LabelEncoder()
        y = self.label_encoder.fit_transform(labels)

        # Scale features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X_scaled, y, test_size=test_size, random_state=42, stratify=y
        )

        # Create and train model
        if self.model_type == 'decision_tree':
            self.model = DecisionTreeClassifier(
                max_depth=10,
                min_samples_split=5,
                random_state=42
            )
        elif self.model_type == 'random_forest':
            self.model = RandomForestClassifier(
                n_estimators=50,  # Keep small for edge deployment
                max_depth=10,
                min_samples_split=5,
                random_state=42,
                n_jobs=1  # Single thread for edge devices
            )
        elif self.model_type == 'svm':
            # LinearSVC wrapped in CalibratedClassifierCV to enable predict_proba
            self.model = CalibratedClassifierCV(
                LinearSVC(C=0.5, max_iter=5000, random_state=42),
                method='sigmoid',
                cv=3
            )
        elif self.model_type == 'mlp':
            self.model = MLPClassifier(
                hidden_layer_sizes=(64, 32),
                activation='relu',
                solver='adam',
                max_iter=500,
                random_state=42,
                early_stopping=True,
                validation_fraction=0.1
            )
        else:
            raise ValueError(f"Unknown model_type: {self.model_type}. "
                             f"Choose from: decision_tree, random_forest, svm, mlp")

        self.model.fit(X_train, y_train)
        self.is_trained = True

        # Evaluate
        y_pred = self.model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)

        return {
            'model': self.model_type,
            'accuracy': accuracy,
            'num_samples': len(features),
            'num_classes': len(self.label_encoder.classes_),
            'classes': list(self.label_encoder.classes_),
            'feature_importance': dict(zip(
                self.feature_names,
                self.model.feature_importances_.tolist()
            )) if hasattr(self.model, 'feature_importances_') else {}
        }

    def classify(self, device_data: Dict[str, Any]) -> ClassificationResult:
        """
        Classify a device based on its characteristics.

        Args:
            device_data: Dictionary with device information

        Returns:
            ClassificationResult with type, confidence, and suggestions
        """
        if self.model_type == 'rule_based' or not self.is_trained:
            return self._classify_rule_based(device_data)

        # Extract and prepare features
        features = self._extract_features(device_data)
        X = self._features_to_array(features).reshape(1, -1)
        X_scaled = self.scaler.transform(X)

        # Predict
        proba = self.model.predict_proba(X_scaled)[0]
        pred_idx = np.argmax(proba)
        device_type = self.label_encoder.inverse_transform([pred_idx])[0]
        confidence = float(proba[pred_idx])

        # All probabilities
        all_probs = {
            self.label_encoder.inverse_transform([i])[0]: float(p)
            for i, p in enumerate(proba)
        }

        return ClassificationResult(
            device_type=device_type,
            confidence=confidence,
            all_probabilities=all_probs,
            suggested_role=self._suggest_role(device_type),
            suggested_capabilities=self.DEVICE_CAPABILITIES.get(device_type, [])
        )

    def _classify_rule_based(self, device_data: Dict[str, Any]) -> ClassificationResult:
        """Rule-based classification fallback."""
        hardware = hardware_name(device_data)
        memory = device_data.get('memory_total', 0)

        # Hardware name-based rules
        if declared_capabilities(device_data) & set(GNSS_CAPABILITY_NAMES):
            device_type = 'GNSSReceiver'
            confidence = 0.95
        elif 'esp32' in hardware:
            device_type = 'ESP32Device'
            confidence = 0.95
        elif 'raspberry' in hardware or 'rpi' in hardware:
            device_type = 'SingleBoardComputer'
            confidence = 0.95
        elif 'jetson' in hardware:
            device_type = 'SingleBoardComputer'
            confidence = 0.95
        elif 'gateway' in hardware:
            device_type = 'IoTGateway'
            confidence = 0.90
        elif 'sensor' in hardware:
            device_type = 'Sensor'
            confidence = 0.90
        elif 'actuator' in hardware:
            device_type = 'Actuator'
            confidence = 0.90
        else:
            # Memory-based fallback
            if memory < self.MEMORY_THRESHOLDS['microcontroller']:
                device_type = 'Microcontroller'
                confidence = 0.70
            elif memory < self.MEMORY_THRESHOLDS['sbc_low']:
                device_type = 'ESP32Device'
                confidence = 0.60
            elif memory < self.MEMORY_THRESHOLDS['sbc_high']:
                device_type = 'SingleBoardComputer'
                confidence = 0.70
            else:
                device_type = 'SingleBoardComputer'
                confidence = 0.50

        return ClassificationResult(
            device_type=device_type,
            confidence=confidence,
            all_probabilities={device_type: confidence},
            suggested_role=self._suggest_role(device_type),
            suggested_capabilities=self.DEVICE_CAPABILITIES.get(device_type, [])
        )

    def _suggest_role(self, device_type: str) -> str:
        """Suggest a default role for a device type."""
        roles = self.DEVICE_ROLES.get(device_type, ['unknown'])
        return roles[0]

    def save(self, filepath: str):
        """Save the trained model to a file."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        model_data = {
            'model_type': self.model_type,
            'feature_set': self.feature_set,
            'feature_names': self.feature_names,
            'is_trained': self.is_trained
        }

        if self.model_type != 'rule_based' and self.is_trained:
            model_data['model'] = self.model
            model_data['label_encoder'] = self.label_encoder
            model_data['scaler'] = self.scaler

        with open(path, 'wb') as f:
            pickle.dump(model_data, f)

    def load(self, filepath: str):
        """Load a trained model from a file."""
        with open(filepath, 'rb') as f:
            model_data = pickle.load(f)

        self.model_type = model_data['model_type']
        # Models saved before feature sets existed used the name-augmented set.
        self.feature_set = model_data.get('feature_set', FEATURE_SET_RESOURCE_NAME)
        self.feature_names = model_data['feature_names']
        self.is_trained = model_data['is_trained']

        expected = feature_names_for(self.feature_set)
        if self.feature_names != expected:
            raise ValueError(
                f"Model file {filepath} was trained on features "
                f"{self.feature_names}, but feature set {self.feature_set!r} "
                f"now defines {expected}. Retrain the model."
            )

        if 'model' in model_data:
            self.model = model_data['model']
            self.label_encoder = model_data['label_encoder']
            self.scaler = model_data['scaler']

    def get_model_size(self) -> int:
        """Get approximate model size in bytes."""
        if self.model is None:
            return 0

        import io
        buffer = io.BytesIO()
        pickle.dump(self.model, buffer)
        return buffer.tell()

    def generate_evaluation_report(
        self,
        features: List[Dict[str, float]],
        labels: List[str],
        test_size: float = 0.2
    ) -> Dict[str, Any]:
        """
        Generate comprehensive evaluation report including confusion matrix and metrics.

        Args:
            features: List of feature dictionaries
            labels: List of device type labels
            test_size: Fraction of data to use for testing

        Returns:
            Dictionary with confusion matrix, classification report, and metrics
        """
        if self.model_type == 'rule_based':
            return {'model': 'rule_based', 'error': 'Evaluation not available for rule-based'}

        if not SKLEARN_AVAILABLE:
            raise RuntimeError("scikit-learn required for evaluation")

        # Prepare data (same column order as this classifier's feature set)
        feature_names = self.feature_names
        X = np.array([features_to_vector(f, feature_names) for f in features])

        # Encode labels
        label_encoder = LabelEncoder()
        y = label_encoder.fit_transform(labels)

        # Scale features
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X_scaled, y, test_size=test_size, random_state=42, stratify=y
        )

        # Create model based on type
        if self.model_type == 'decision_tree':
            model = DecisionTreeClassifier(max_depth=10, min_samples_split=5, random_state=42)
        elif self.model_type == 'random_forest':
            model = RandomForestClassifier(n_estimators=50, max_depth=10, min_samples_split=5, random_state=42, n_jobs=1)
        elif self.model_type == 'svm':
            model = CalibratedClassifierCV(LinearSVC(C=0.5, max_iter=5000, random_state=42), method="sigmoid", cv=3)
        elif self.model_type == 'mlp':
            model = MLPClassifier(hidden_layer_sizes=(64, 32), activation="relu", solver="adam", max_iter=500, random_state=42, early_stopping=True, validation_fraction=0.1)
        else:
            model = RandomForestClassifier(n_estimators=50, max_depth=10, random_state=42, n_jobs=1)

        model.fit(X_train, y_train)

        # Predictions
        y_train_pred = model.predict(X_train)
        y_test_pred = model.predict(X_test)

        # Confusion matrices
        cm_train = confusion_matrix(y_train, y_train_pred)
        cm_test = confusion_matrix(y_test, y_test_pred)

        # Accuracy scores
        train_accuracy = accuracy_score(y_train, y_train_pred)
        test_accuracy = accuracy_score(y_test, y_test_pred)

        # Classification report
        class_report = classification_report(
            y_test, y_test_pred,
            target_names=label_encoder.classes_,
            output_dict=True
        )

        return {
            'model': self.model_type,
            'classes': list(label_encoder.classes_),
            'confusion_matrix_train': cm_train.tolist(),
            'confusion_matrix_test': cm_test.tolist(),
            'train_accuracy': train_accuracy,
            'test_accuracy': test_accuracy,
            'classification_report': class_report,
            'num_train_samples': len(y_train),
            'num_test_samples': len(y_test),
            'overfitting_indicator': train_accuracy - test_accuracy
        }

    def generate_learning_curve_data(
        self,
        features: List[Dict[str, float]],
        labels: List[str],
        cv: int = 5,
        train_sizes: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """
        Generate learning curve data to detect overfitting.

        Args:
            features: List of feature dictionaries
            labels: List of device type labels
            cv: Number of cross-validation folds
            train_sizes: List of training set size fractions (default: [0.1, 0.25, 0.5, 0.75, 1.0])

        Returns:
            Dictionary with learning curve data
        """
        if self.model_type == 'rule_based':
            return {'model': 'rule_based', 'error': 'Learning curve not available for rule-based'}

        if not SKLEARN_AVAILABLE:
            raise RuntimeError("scikit-learn required for learning curve")

        if train_sizes is None:
            train_sizes = [0.1, 0.25, 0.5, 0.75, 1.0]

        # Prepare data (same column order as this classifier's feature set)
        feature_names = self.feature_names
        X = np.array([features_to_vector(f, feature_names) for f in features])

        # Encode labels
        label_encoder = LabelEncoder()
        y = label_encoder.fit_transform(labels)

        # Scale features
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Create model
        if self.model_type == 'decision_tree':
            model = DecisionTreeClassifier(
                max_depth=10,
                min_samples_split=5,
                random_state=42
            )
        else:  # random_forest
            model = RandomForestClassifier(
                n_estimators=50,
                max_depth=10,
                min_samples_split=5,
                random_state=42,
                n_jobs=1
            )

        # Generate learning curve
        train_sizes_abs, train_scores, test_scores = learning_curve(
            model, X_scaled, y,
            train_sizes=train_sizes,
            cv=cv,
            scoring='accuracy',
            random_state=42,
            n_jobs=1
        )

        return {
            'model': self.model_type,
            'train_sizes': train_sizes_abs.tolist(),
            'train_scores_mean': np.mean(train_scores, axis=1).tolist(),
            'train_scores_std': np.std(train_scores, axis=1).tolist(),
            'test_scores_mean': np.mean(test_scores, axis=1).tolist(),
            'test_scores_std': np.std(test_scores, axis=1).tolist(),
            'cv_folds': cv
        }

    def plot_learning_curve(
        self,
        features: List[Dict[str, float]],
        labels: List[str],
        output_path: Optional[str] = None,
        cv: int = 5,
        train_sizes: Optional[List[float]] = None,
        figsize: Tuple[int, int] = (10, 6)
    ) -> Optional[str]:
        """
        Generate and save learning curve plot.

        Args:
            features: List of feature dictionaries
            labels: List of device type labels
            output_path: Path to save the plot (default: learning_curve_{model_type}.png)
            cv: Number of cross-validation folds
            train_sizes: List of training set size fractions
            figsize: Figure size as (width, height)

        Returns:
            Path to saved plot or None if matplotlib unavailable
        """
        if not MATPLOTLIB_AVAILABLE:
            print("Warning: matplotlib not available, cannot generate plot")
            return None

        data = self.generate_learning_curve_data(features, labels, cv, train_sizes)

        if 'error' in data:
            print(f"Cannot generate learning curve: {data['error']}")
            return None

        fig, ax = plt.subplots(figsize=figsize)

        train_sizes = data['train_sizes']
        train_mean = np.array(data['train_scores_mean'])
        train_std = np.array(data['train_scores_std'])
        test_mean = np.array(data['test_scores_mean'])
        test_std = np.array(data['test_scores_std'])

        # Plot training scores
        ax.fill_between(train_sizes, train_mean - train_std, train_mean + train_std,
                        alpha=0.1, color='blue')
        ax.plot(train_sizes, train_mean, 'o-', color='blue', label='Training score')

        # Plot validation scores
        ax.fill_between(train_sizes, test_mean - test_std, test_mean + test_std,
                        alpha=0.1, color='orange')
        ax.plot(train_sizes, test_mean, 'o-', color='orange', label='Cross-validation score')

        ax.set_xlabel('Training Examples')
        ax.set_ylabel('Accuracy Score')
        ax.set_title(f'Learning Curve - {self.model_type.replace("_", " ").title()}')
        ax.legend(loc='lower right')
        ax.grid(True, alpha=0.3)
        ax.set_ylim(0, 1.05)

        # Add overfitting indicator
        final_gap = train_mean[-1] - test_mean[-1]
        if final_gap > 0.1:
            ax.text(0.02, 0.98, f'⚠ Potential overfitting (gap: {final_gap:.2f})',
                    transform=ax.transAxes, fontsize=10, verticalalignment='top',
                    color='red')

        plt.tight_layout()

        if output_path is None:
            output_path = f'learning_curve_{self.model_type}.png'

        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        return output_path

    def plot_confusion_matrix(
        self,
        features: List[Dict[str, float]],
        labels: List[str],
        output_path: Optional[str] = None,
        test_size: float = 0.2,
        figsize: Tuple[int, int] = (12, 5),
        normalize: bool = True
    ) -> Optional[str]:
        """
        Generate and save confusion matrix plots for train and test sets.

        Args:
            features: List of feature dictionaries
            labels: List of device type labels
            output_path: Path to save the plot (default: confusion_matrix_{model_type}.png)
            test_size: Fraction of data to use for testing
            figsize: Figure size as (width, height)
            normalize: Whether to normalize the confusion matrix

        Returns:
            Path to saved plot or None if matplotlib unavailable
        """
        if not MATPLOTLIB_AVAILABLE:
            print("Warning: matplotlib not available, cannot generate plot")
            return None

        report = self.generate_evaluation_report(features, labels, test_size)

        if 'error' in report:
            print(f"Cannot generate confusion matrix: {report['error']}")
            return None

        classes = report['classes']
        cm_train = np.array(report['confusion_matrix_train'])
        cm_test = np.array(report['confusion_matrix_test'])

        if normalize:
            cm_train = cm_train.astype('float') / cm_train.sum(axis=1)[:, np.newaxis]
            cm_test = cm_test.astype('float') / cm_test.sum(axis=1)[:, np.newaxis]
            cm_train = np.nan_to_num(cm_train)
            cm_test = np.nan_to_num(cm_test)

        fig, axes = plt.subplots(1, 2, figsize=figsize)

        for ax, cm, title, acc in [
            (axes[0], cm_train, 'Training Set', report['train_accuracy']),
            (axes[1], cm_test, 'Test Set', report['test_accuracy'])
        ]:
            im = ax.imshow(cm, interpolation='nearest', cmap=plt.cm.Blues)
            ax.figure.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

            ax.set(
                xticks=np.arange(len(classes)),
                yticks=np.arange(len(classes)),
                xticklabels=classes,
                yticklabels=classes,
                title=f'{title}\nAccuracy: {acc:.3f}',
                ylabel='True label',
                xlabel='Predicted label'
            )

            plt.setp(ax.get_xticklabels(), rotation=45, ha='right', rotation_mode='anchor')

            # Add text annotations
            fmt = '.2f' if normalize else 'd'
            thresh = cm.max() / 2.
            for i in range(cm.shape[0]):
                for j in range(cm.shape[1]):
                    ax.text(j, i, format(cm[i, j], fmt),
                            ha='center', va='center',
                            color='white' if cm[i, j] > thresh else 'black',
                            fontsize=8)

        # Add overall title with overfitting indicator
        gap = report['overfitting_indicator']
        title = f'Confusion Matrix - {self.model_type.replace("_", " ").title()}'
        if gap > 0.1:
            title += f'\n⚠ Overfitting detected (train-test gap: {gap:.2f})'
        fig.suptitle(title, fontsize=12, y=1.02)

        plt.tight_layout()

        if output_path is None:
            output_path = f'confusion_matrix_{self.model_type}.png'

        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        return output_path


def generate_model_evaluation_plots(
    num_samples: int = 1000,
    output_dir: str = '.',
    models: Optional[List[str]] = None
) -> Dict[str, Dict[str, str]]:
    """
    Convenience function to generate learning curves and confusion matrices for multiple models.

    Args:
        num_samples: Number of training samples
        output_dir: Directory to save plots
        models: List of model types (default: ['decision_tree', 'random_forest'])

    Returns:
        Dictionary mapping model names to their plot paths
    """
    from .dataset_generator import SyntheticDatasetGenerator

    if models is None:
        models = ['decision_tree', 'random_forest', 'svm', 'mlp']

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Generate data
    generator = SyntheticDatasetGenerator(seed=42)
    features, labels = generator.generate_classification_dataset(num_samples)

    results = {}

    for model_type in models:
        print(f"\nGenerating evaluation plots for {model_type}...")
        classifier = DeviceClassifier(model_type=model_type)

        lc_path = classifier.plot_learning_curve(
            features, labels,
            output_path=str(output_path / f'learning_curve_{model_type}.png')
        )

        cm_path = classifier.plot_confusion_matrix(
            features, labels,
            output_path=str(output_path / f'confusion_matrix_{model_type}.png')
        )

        # Get evaluation report
        report = classifier.generate_evaluation_report(features, labels)

        results[model_type] = {
            'learning_curve_plot': lc_path,
            'confusion_matrix_plot': cm_path,
            'train_accuracy': report.get('train_accuracy'),
            'test_accuracy': report.get('test_accuracy'),
            'overfitting_indicator': report.get('overfitting_indicator'),
            'classification_report': report.get('classification_report')
        }

        print(f"  Train accuracy: {report.get('train_accuracy', 'N/A'):.4f}")
        print(f"  Test accuracy: {report.get('test_accuracy', 'N/A'):.4f}")
        print(f"  Overfitting gap: {report.get('overfitting_indicator', 'N/A'):.4f}")

    return results


def train_classifier_from_synthetic_data(
    num_samples: int = 1000,
    model_type: str = 'decision_tree',
    feature_set: str = DEFAULT_FEATURE_SET,
    seed: int = 42,
    generator=None
) -> Tuple[DeviceClassifier, Dict[str, Any]]:
    """
    Convenience function to train a classifier using synthetic data.

    The training vectors are built with the very same extractor that the
    classifier uses at inference time, so a model produced here can always
    classify a real device announcement.

    Args:
        num_samples: Number of training samples
        model_type: Type of model to train
        feature_set: Which features to train on, see pnp_ai.features
        seed: Seed for the synthetic generator, for reproducibility
        generator: Generator to sample the training announcements from, for
            example a CatalogDatasetGenerator. Default: the profile generator
            (SyntheticDatasetGenerator) seeded with ``seed``.

    Returns:
        Tuple of (trained classifier, training metrics)
    """
    from .dataset_generator import SyntheticDatasetGenerator

    classifier = DeviceClassifier(model_type=model_type, feature_set=feature_set)

    # Generate raw device announcements, then extract features through the
    # classifier itself. Note this deliberately does NOT use
    # generate_classification_dataset(), which also returns oracle role
    # features that no device announces when it registers.
    if generator is None:
        generator = SyntheticDatasetGenerator(seed=seed)
    devices = generator.generate_dataset(num_samples)
    devices = generator.generate_anomalies(devices, 0.1)

    features = [classifier._extract_features(device) for device in devices]
    labels = [device['device_type'] for device in devices]

    metrics = classifier.train(features, labels)
    metrics['feature_set'] = feature_set
    metrics['feature_names'] = list(classifier.feature_names)
    describe = getattr(generator, 'describe', None)
    metrics['training_source'] = describe().get('source', 'catalog') if describe else 'profiles'

    return classifier, metrics
