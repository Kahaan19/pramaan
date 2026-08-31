#!/usr/bin/env python3
"""Generate demo Ed25519 keypairs for the Pramaan mandate layer.

Writes, for each role (user, merchant):
  secrets/{role}_ed25519.key   private key, base64-encoded seed, chmod 0600
  secrets/{role}_ed25519.pub   public key, base64-encoded
  secrets/{role}_ed25519.id    the owning id (e.g. "user_kahaan"), plain text

The control plane's Keyring (control-plane/mandates/keys.py) loads ONLY the
.pub + .id files. The .key files are for whoever signs mandates (the buyer
agent, a merchant catalog script, or a REPL) -- never for the verifier.

`secrets/` is gitignored wholesale. NEVER commit anything from it.
"""

import argparse
import os
import sys
from pathlib import Path

from nacl.encoding import Base64Encoder
from nacl.signing import SigningKey

DEFAULT_USER_ID = "user_kahaan"
DEFAULT_MERCHANT_ID = "merchant_demo_01"


def load_signing_key(path: Path) -> SigningKey:
    """Loads a private key written by write_keypair() below."""
    seed_b64 = path.read_text().strip()
    return SigningKey(seed_b64.encode("ascii"), encoder=Base64Encoder)


def write_keypair(secrets_dir: Path, role: str, owner_id: str, force: bool) -> None:
    key_path = secrets_dir / f"{role}_ed25519.key"
    pub_path = secrets_dir / f"{role}_ed25519.pub"
    id_path = secrets_dir / f"{role}_ed25519.id"

    existing = [p for p in (key_path, pub_path, id_path) if p.exists()]
    if existing and not force:
        names = ", ".join(str(p) for p in existing)
        print(f"refusing to overwrite existing files ({names}); pass --force to regenerate", file=sys.stderr)
        sys.exit(1)

    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key

    key_path.write_text(Base64Encoder.encode(bytes(signing_key)).decode("ascii") + "\n")
    os.chmod(key_path, 0o600)

    pub_path.write_text(Base64Encoder.encode(bytes(verify_key)).decode("ascii") + "\n")
    id_path.write_text(owner_id + "\n")

    print(f"wrote {role} keypair for {owner_id!r}: {key_path}, {pub_path}, {id_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--secrets-dir", default=Path("secrets"), type=Path)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--merchant-id", default=DEFAULT_MERCHANT_ID)
    parser.add_argument("--force", action="store_true", help="overwrite existing keys")
    args = parser.parse_args()

    args.secrets_dir.mkdir(parents=True, exist_ok=True)
    write_keypair(args.secrets_dir, "user", args.user_id, args.force)
    write_keypair(args.secrets_dir, "merchant", args.merchant_id, args.force)


if __name__ == "__main__":
    main()
