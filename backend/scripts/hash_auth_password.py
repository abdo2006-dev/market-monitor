"""Generate an APP_AUTH_PASSWORD_HASH without echoing the password."""
from getpass import getpass

from app.auth import hash_password


def main() -> None:
    password = getpass("New Market Monitor password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    if len(password) < 14:
        raise SystemExit("Use at least 14 characters")
    print(hash_password(password))


if __name__ == "__main__":
    main()
