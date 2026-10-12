"""
Ed25519 signing helpers for the BlockDAG.

Keys are handled as plain hex strings (raw 32-byte keys), which keeps them easy
to store in a file, in an environment variable or in a JSON trust store.

Trust model (deliberately simple):
- Every server that creates DAG nodes has its own Ed25519 key pair.
- Every server holds a trust store ``{creator_id: public_key_hex}`` with the
  public keys of the servers it accepts nodes from. It is provisioned out of
  band (copied by the operator); there is no key rotation or revocation yet.

Command line (generate a key pair for this node):

    python -m blockdag.signing keygen --out keys/edge-node-01.key

Project: PINV01-24 - FIUNA
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _require_cryptography():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "Signing needs the 'cryptography' package: pip install cryptography"
        ) from exc
    return ed25519, serialization, InvalidSignature


def generate_private_key() -> str:
    """Create a new Ed25519 private key and return it as 64 hex characters."""
    ed25519, serialization, _ = _require_cryptography()
    key = ed25519.Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return raw.hex()


def public_key_of(private_key_hex: str) -> str:
    """Public key (64 hex characters) that belongs to a private key."""
    ed25519, serialization, _ = _require_cryptography()
    key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return raw.hex()


def sign(private_key_hex: str, message: bytes) -> str:
    """Sign ``message`` and return the signature as 128 hex characters."""
    ed25519, _, _ = _require_cryptography()
    key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return key.sign(message).hex()


def verify(public_key_hex: str, message: bytes, signature_hex: str) -> bool:
    """True only if ``signature_hex`` is a valid signature of ``message``."""
    ed25519, _, InvalidSignature = _require_cryptography()
    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(bytes.fromhex(signature_hex), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def load_or_create_private_key(path: str) -> str:
    """
    Read the private key stored (as hex) in ``path``; create it if missing.

    On POSIX the new file is created readable by its owner only.
    """
    file = Path(path)
    if file.exists():
        key = file.read_text(encoding="utf-8").strip()
        public_key_of(key)  # raises if the file does not hold a valid key
        return key

    file.parent.mkdir(parents=True, exist_ok=True)
    key = generate_private_key()
    fd = os.open(str(file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key + "\n")
    return key


def load_trusted_keys(path: str) -> Dict[str, str]:
    """Read a JSON trust store ``{creator_id: public_key_hex}``."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Trust store must be a JSON object {creator_id: public_key_hex}")
    for creator_id, public_key in data.items():
        bytes.fromhex(public_key)  # raises ValueError if it is not hex
        if len(public_key) != 64:
            raise ValueError(f"Public key of {creator_id!r} must be 64 hex characters")
    return data


def build_dag_options(
    key_file: Optional[str] = None,
    trusted_keys_file: Optional[str] = None,
    require_signatures: bool = False,
) -> Dict[str, Any]:
    """
    Keyword arguments for ``BlockDAG`` / ``DAGStorage`` from configuration.

    With nothing configured it returns ``{}`` and the DAG behaves exactly as an
    unsigned DAG.
    """
    options: Dict[str, Any] = {}
    if key_file:
        options["signing_key"] = load_or_create_private_key(key_file)
    if trusted_keys_file:
        options["trusted_keys"] = load_trusted_keys(trusted_keys_file)
    if require_signatures:
        options["require_signatures"] = True
    return options


def _main() -> None:
    parser = argparse.ArgumentParser(description="BlockDAG signing keys")
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="create a key file and print its public key")
    keygen.add_argument("--out", required=True, help="path of the private key file")

    show = sub.add_parser("pubkey", help="print the public key of an existing key file")
    show.add_argument("--key", required=True, help="path of the private key file")

    args = parser.parse_args()
    if args.command == "keygen":
        if Path(args.out).exists():
            raise SystemExit(f"{args.out} already exists; refusing to overwrite it")
        key = load_or_create_private_key(args.out)
        print(f"Private key written to {args.out} (keep it secret, do not commit it)")
        print(f"Public key: {public_key_of(key)}")
    else:
        if not Path(args.key).exists():
            raise SystemExit(f"{args.key} does not exist")
        print(public_key_of(load_or_create_private_key(args.key)))


if __name__ == "__main__":
    _main()
