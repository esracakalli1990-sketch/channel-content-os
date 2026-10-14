"""Ask Instagram whether the stored token works, without changing anything.

The weekly refresh job cannot answer this right after a token is replaced:
Instagram refuses to refresh a token under 24 hours old, so a fresh token
looks "broken" to it for a day. This asks the plainer question instead --
does the token authenticate, and as which account -- which any valid token
answers at once.

It also stamps the time, because the thing worth knowing about a token
generated in the Meta dashboard is whether it is the 60-day kind or the
one-hour kind, and that is only visible by asking again later: a one-hour
token is dead by the second look.

Prints the account name and whether the id matches IG_USER_ID. Never the token.

It also proves the personal access token (GH_SECRET_TOKEN) can really write
Actions secrets, because the weekly refresh cannot show that for days. Reading
the repository's public key would not prove it -- read and write are separate
permissions -- so the proof is a round trip: write a throwaway secret, delete
it. Nothing the pipeline uses is touched.

Usage: ig_check.py
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops import github_secrets, instagram_uploader as ig  # noqa: E402


PROBE_NAME = "IG_PAT_PROBE"


def check_pat() -> bool:
    """Round-trip a throwaway secret to show the PAT can write them."""
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not os.getenv("GH_SECRET_TOKEN", "").strip():
        print("  ✗ GH_SECRET_TOKEN boş — yenileme yeni token'ı yazamaz")
        return False
    if not repository:
        print("  ✗ GITHUB_REPOSITORY yok; yalnızca Actions içinde çalışır")
        return False
    try:
        github_secrets.update_secret(repository, PROBE_NAME, "probe")
    except github_secrets.SecretWriteError as exc:
        print("  ✗ PAT secret YAZAMIYOR")
        print(f"    {exc}")
        return False
    try:
        github_secrets.delete_secret(repository, PROBE_NAME)
    except github_secrets.SecretWriteError as exc:
        # Written but not removed: harmless, but worth saying so it is not a
        # mystery secret in the list later.
        print(f"  ✓ PAT secret yazabiliyor, ama {PROBE_NAME} silinemedi: {exc}")
        print(f"    {PROBE_NAME} secret'ını elle silebilirsin; hiçbir şey ona bağlı değil.")
        return True
    print("  ✓ PAT secret yazıp silebiliyor (deneme secret'ı kaldırıldı)")
    return True


def main() -> int:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        user_id, token = ig._credentials()
        reply = ig._call("me", {"fields": "user_id,username",
                                "access_token": token})
    except ig.InstagramError as exc:
        print(f"[{now}] ✗ Token ÇALIŞMIYOR")
        print(f"  {exc}")
        return 1

    returned = str(reply.get("user_id") or reply.get("id") or "")
    username = reply.get("username", "?")
    matches = returned == user_id.strip()

    print(f"[{now}] ✓ Token çalışıyor")
    print(f"  hesap      : @{username}")
    print(f"  kimlik     : {'IG_USER_ID ile eşleşiyor' if matches else 'IG_USER_ID İLE EŞLEŞMİYOR'}")
    if not matches:
        # A valid token for the wrong account would publish to the wrong place.
        print("  ⚠ Token başka bir hesaba ait görünüyor; paylaşım yanlış yere gider.")
        return 1

    print("\nYenileme anahtarı (GH_SECRET_TOKEN):")
    return 0 if check_pat() else 1


if __name__ == "__main__":
    raise SystemExit(main())
