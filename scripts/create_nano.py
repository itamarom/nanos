"""Register a nano and generate an API key."""
import os
import sys
import secrets
import uuid

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.database import SyncSessionLocal
from shared.models import Nano, NanoApiKey, NanoPermission


def generate_nano_key() -> str:
    return "nk_" + secrets.token_hex(16)


def create_nano(config_path: str):
    with open(config_path) as f:
        config = yaml.safe_load(f)

    name = config["name"]
    description = config.get("description", "")
    schedule = config.get("schedule")
    permissions = config.get("permissions", [])
    # Derive script_path from config directory
    script_dir = os.path.basename(os.path.dirname(os.path.abspath(config_path)))
    script_path = f"{script_dir}/nano.py"

    session = next(iter([SyncSessionLocal()]))
    try:
        nano = Nano(
            id=uuid.uuid4(),
            name=name,
            description=description,
            script_path=script_path,
            schedule=schedule,
            is_active=True,
        )
        session.add(nano)

        api_key = generate_nano_key()
        session.add(NanoApiKey(nano_id=nano.id, key=api_key))

        for perm in permissions:
            session.add(NanoPermission(nano_id=nano.id, endpoint=perm))

        session.commit()
        print(f"Nano '{name}' registered successfully.")
        print(f"  ID: {nano.id}")
        print(f"  API Key: {api_key}")
        print(f"  Permissions: {', '.join(permissions)}")
        if schedule:
            print(f"  Schedule: {schedule}")
    except Exception as e:
        session.rollback()
        print(f"Error: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to nano config.yaml")
    args = parser.parse_args()
    create_nano(args.config)
