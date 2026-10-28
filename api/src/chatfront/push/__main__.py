"""Make the VAPID key that signs alerts (feature 115):

    python -m chatfront.push

prints a new private key. Put it in VAPID_PRIVATE_KEY (Render's Environment, and api/.env to
try alerts locally); never in the repository. Changing it later means every device has to
turn alerts on again.
"""

from chatfront.push.webpush import Vapid, new_private_key


def main() -> None:
    private = new_private_key()
    print(f"VAPID_PRIVATE_KEY={private}")
    print(f"(public key, for reference: {Vapid.from_text(private, '').public_key})")


if __name__ == "__main__":
    main()
