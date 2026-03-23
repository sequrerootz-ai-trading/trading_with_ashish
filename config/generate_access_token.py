from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from kiteconnect import KiteConnect


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_CANDIDATES = [
    BASE_DIR / ".env",
    Path.cwd() / ".env",
    Path(__file__).resolve().parent / ".env",
]

for env_path in ENV_CANDIDATES:
    if env_path.exists():
        load_dotenv(env_path, override=True)


def main() -> None:
    api_key = os.getenv("KITE_API_KEY", "").strip()
    api_secret = os.getenv("KITE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        searched_paths = "\n".join(f"- {path}" for path in ENV_CANDIDATES)
        raise ValueError(
            "Missing KITE_API_KEY or KITE_API_SECRET.\n"
            "Checked these .env locations:\n"
            f"{searched_paths}\n\n"
            "Make sure your file is named exactly .env, not .env.txt."
        )

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    print("Open this URL in your browser and log in to Zerodha:")
    print(login_url)
    print()
    print("After login, copy the request_token from the redirected URL.")

    request_token = input("Enter request_token: ").strip()
    if not request_token:
        raise ValueError("request_token is required.")

    session_data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = session_data["access_token"]

    env_file = _resolve_env_file()
    _upsert_env_value(env_file, "KITE_ACCESS_TOKEN", access_token)

    print()
    print("Access token generated successfully.")
    print(f"KITE_ACCESS_TOKEN={access_token}")
    print(f"Saved to: {env_file}")


def _resolve_env_file() -> Path:
    for env_path in ENV_CANDIDATES:
        if env_path.exists():
            return env_path
    return BASE_DIR / ".env"


def _upsert_env_value(env_file: Path, key: str, value: str) -> None:
    lines: list[str] = []
    if env_file.exists():
        lines = env_file.read_text().splitlines()

    updated = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            updated = True
            break

    if not updated:
        lines.append(f"{key}={value}")

    env_file.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
