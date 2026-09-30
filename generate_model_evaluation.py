#!/usr/bin/env python3
"""
Generate learning curves and confusion matrices for Decision Tree and Random Forest models.

This script creates evaluation plots to help detect overfitting and visualize model performance.

Usage:
    python generate_model_evaluation.py [--samples 1000] [--output-dir ./evaluation_plots]

Project: PINV01-24 - FIUNA
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description='Generate learning curves and confusion matrices for DT and RF models'
    )
    parser.add_argument(
        '--samples', '-n',
        type=int,
        default=1000,
        help='Number of synthetic training samples (default: 1000)'
    )
    parser.add_argument(
        '--output-dir', '-o',
        type=str,
        default='./evaluation_plots',
        help='Directory to save plots (default: ./evaluation_plots)'
    )
    parser.add_argument(
        '--models', '-m',
        nargs='+',
        default=['decision_tree', 'random_forest'],
        choices=['decision_tree', 'random_forest'],
        help='Models to evaluate (default: both)'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("IoT-ITS Model Evaluation - Learning Curves & Confusion Matrices")
    print("=" * 60)
    print(f"\nConfiguration:")
    print(f"  Samples: {args.samples}")
    print(f"  Output directory: {args.output_dir}")
    print(f"  Models: {', '.join(args.models)}")

    try:
        from pnp_ai.device_classifier import generate_model_evaluation_plots
    except ImportError as e:
        print(f"\nError: Could not import required modules: {e}")
        print("Make sure you're running from the project root directory.")
        sys.exit(1)

    # Generate plots
    print("\n" + "-" * 60)
    results = generate_model_evaluation_plots(
        num_samples=args.samples,
        output_dir=args.output_dir,
        models=args.models
    )

    # Summary
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    for model_type, metrics in results.items():
        print(f"\n{model_type.upper().replace('_', ' ')}")
        print("-" * 40)
        print(f"  Train Accuracy:      {metrics['train_accuracy']:.4f}")
        print(f"  Test Accuracy:       {metrics['test_accuracy']:.4f}")
        print(f"  Overfitting Gap:     {metrics['overfitting_indicator']:.4f}")

        if metrics['overfitting_indicator'] > 0.1:
            print(f"  [!] WARNING: Potential overfitting detected!")
        elif metrics['overfitting_indicator'] > 0.05:
            print(f"  [*] Note: Slight gap between train/test accuracy")
        else:
            print(f"  [OK] Good generalization (minimal overfitting)")

        print(f"\n  Saved plots:")
        print(f"    - {metrics['learning_curve_plot']}")
        print(f"    - {metrics['confusion_matrix_plot']}")

        # Per-class metrics from classification report
        if metrics.get('classification_report'):
            print(f"\n  Per-class F1 scores (test set):")
            for cls, scores in metrics['classification_report'].items():
                if isinstance(scores, dict) and 'f1-score' in scores:
                    print(f"    {cls}: {scores['f1-score']:.3f}")

    print("\n" + "=" * 60)
    print(f"All plots saved to: {Path(args.output_dir).absolute()}")
    print("=" * 60)


if __name__ == '__main__':
    main()
