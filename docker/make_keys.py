"""
Creates the Ed25519 keys of the edge nodes and the trust store they share.

Run by the 'keys' service of docker-compose.yml before the servers start.
Keys that already exist are kept, so the nodes keep their identity across
restarts. The keys live in a Docker volume, never in the repository.

Usage: python docker/make_keys.py <dir> <node_id> [<node_id> ...]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blockdag.signing import load_or_create_private_key, public_key_of  # noqa: E402


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    trusted = {}
    for node_id in sys.argv[2:]:
        key = load_or_create_private_key(str(out / f"{node_id}.key"))
        trusted[node_id] = public_key_of(key)
    (out / "trusted_keys.json").write_text(json.dumps(trusted, indent=2))
    for node_id, public in trusted.items():
        print(f"{node_id}: {public}")


if __name__ == "__main__":
    main()
