from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from firebase_config import ensure_user_account  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or update a Firebase user and grant the expert role."
    )
    parser.add_argument(
        "--email",
        default=os.getenv("EXPERT_ACCOUNT_EMAIL", "expert2@gmail.com"),
        help="Firebase user email to promote (default: expert2@gmail.com or EXPERT_ACCOUNT_EMAIL).",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Password to set if the user is created, or to update an existing user.",
    )
    parser.add_argument(
        "--no-password",
        action="store_true",
        help="Do not change the password for an existing user.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    email = args.email.strip().lower()
    password = None if args.no_password else args.password

    if not email:
        raise SystemExit("An email address is required.")

    user, created_new = ensure_user_account(
        email=email,
        password=password,
        custom_claims={"role": "expert"},
    )

    action = "created" if created_new else "updated"
    print(f"Expert account {action}: {user.email} ({user.uid})")
    print("Role set to expert.")
    print("If this user just signed in, ask them to sign out and sign back in so the new claim is picked up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
