#!/usr/bin/env python3
"""
Generate BlockDAG performance evaluation plots for academic reporting.

This script creates a synthetic workload and produces:
- Merge success rate chart
- Sync payload size and serialization time chart
- Tip count growth over time chart
- Topological sort time vs node count chart

Usage:
    python generate_dag_evaluation.py [--nodes 500] [--syncs 10] [--output-dir ./dag_evaluation]

Project: PINV01-24 - FIUNA
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description='Generate BlockDAG performance evaluation plots'
    )
    parser.add_argument(
        '--nodes', '-n',
        type=int,
        default=500,
        help='Number of DAG nodes to generate (default: 500)'
    )
    parser.add_argument(
        '--syncs', '-s',
        type=int,
        default=10,
        help='Number of sync operations to simulate (default: 10)'
    )
    parser.add_argument(
        '--output-dir', '-o',
        type=str,
        default='./dag_evaluation',
        help='Directory to save plots (default: ./dag_evaluation)'
    )
    parser.add_argument(
        '--metrics', '-m',
        nargs='+',
        default=['merge', 'sync', 'tips', 'sort'],
        choices=['merge', 'sync', 'tips', 'sort'],
        help='Metrics to evaluate (default: all)'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("BlockDAG Performance Evaluation")
    print("=" * 60)
    print(f"\nConfiguration:")
    print(f"  Nodes: {args.nodes}")
    print(f"  Sync operations: {args.syncs}")
    print(f"  Output directory: {args.output_dir}")
    print(f"  Metrics: {', '.join(args.metrics)}")

    try:
        from blockdag.metrics import generate_dag_performance_report
    except ImportError as e:
        print(f"\nError: Could not import required modules: {e}")
        print("Make sure you're running from the project root directory.")
        sys.exit(1)

    # Generate report
    print("\n" + "-" * 60)
    results = generate_dag_performance_report(
        num_nodes=args.nodes,
        num_syncs=args.syncs,
        output_dir=args.output_dir,
        metrics=args.metrics
    )

    # Summary
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    if 'merge' in results:
        m = results['merge']
        print(f"\nMERGE METRICS")
        print("-" * 40)
        print(f"  Total merges:          {m['total_merges']}")
        print(f"  Total nodes offered:   {m['total_offered']}")
        print(f"  Added:                 {m['total_added']}")
        print(f"  Failed:                {m['total_failed']}")
        print(f"  Already existing:      {m['total_existing']}")
        print(f"  Success rate:          {m['success_rate']:.2%}")
        print(f"  Avg nodes/sync:        {m['avg_nodes_per_sync']:.1f}")
        if m.get('failure_breakdown'):
            print(f"  Failure breakdown:")
            for reason, count in m['failure_breakdown'].items():
                print(f"    {reason}: {count}")

    if 'sync' in results:
        s = results['sync']
        print(f"\nSYNC METRICS")
        print("-" * 40)
        print(f"  Total exports:         {s['total_exports']}")
        print(f"  Total bytes exported:  {s['total_bytes_exported']:,}")
        print(f"  Avg bytes/node:        {s['avg_bytes_per_node']:.0f}")
        print(f"  Avg serialization:     {s['avg_serialization_sec']*1000:.2f} ms")
        print(f"  Max serialization:     {s['max_serialization_sec']*1000:.2f} ms")

    if 'tips' in results:
        t = results['tips']
        print(f"\nTIP METRICS")
        print("-" * 40)
        print(f"  Total snapshots:       {t['snapshots']}")
        print(f"  Current tips:          {t['current_tips']}")
        print(f"  Max tips:              {t['max_tips']}")
        print(f"  Min tips:              {t['min_tips']}")
        print(f"  Avg tips:              {t['avg_tips']:.1f}")

    if 'sort' in results:
        sr = results['sort']
        print(f"\nSORT METRICS")
        print("-" * 40)
        print(f"  Total sorts:           {sr['total_sorts']}")
        print(f"  Avg sort time:         {sr['avg_elapsed_sec']*1000:.3f} ms")
        print(f"  Max sort time:         {sr['max_elapsed_sec']*1000:.3f} ms")
        if sr.get('node_count_vs_time'):
            first = sr['node_count_vs_time'][0]
            last = sr['node_count_vs_time'][-1]
            print(f"  At {first['node_count']} nodes:       {first['elapsed_sec']*1000:.3f} ms")
            print(f"  At {last['node_count']} nodes:      {last['elapsed_sec']*1000:.3f} ms")

    # Plot paths
    if 'plots' in results:
        print(f"\nGENERATED PLOTS")
        print("-" * 40)
        for name, path in results['plots'].items():
            if path:
                print(f"  {name}: {path}")

    print("\n" + "=" * 60)
    print(f"All plots saved to: {Path(args.output_dir).absolute()}")
    print("=" * 60)


if __name__ == '__main__':
    main()
