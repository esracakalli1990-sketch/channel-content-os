"""Keep the Instagram token alive without anyone opening the settings page.

Instagram tokens last sixty days. On 27 September one reached day sixty and
died in silence, and the first sign of it was a publish failing. Refreshing
early and often removes the deadline altogether: each refresh issues a
replacement good for another sixty days, so a weekly run means the token is
never more than a week old.

Two limits are worth knowing before reading a failure here as a bug:

* An EXPIRED token cannot be refreshed. Once it is dead a human has to issue a
  new one from the app dashboard -- this script cannot rescue that, it can only
  stop it happening again.
* A token under twenty-four hours old cannot be refreshed either, so the run
  right after a manual replacement is expected to fail. The next one works.

Exit code is 0 whenever the token is healthy, 1 when someone has to act.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops import instagram_uploader, notifications  # noqa: E402
from channel_ops.github_secrets import SecretWriteError, update_secret  # noqa: E402

SECRET_NAME = "IG_ACCESS_TOKEN"


def _tell(message: str) -> None:
    """Print, and try Telegram -- but never let Telegram decide the outcome."""
    print(message)
    try:
        notifications.send_message(message)
    except Exception as exc:  # noqa: BLE001 - reporting must not change the result
        print(f"[telegram gönderilemedi: {exc}]")


def main() -> int:
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not repository:
        print("GITHUB_REPOSITORY is not set; nowhere to write the secret.")
        return 1

    try:
        new_token, days = instagram_uploader.refresh_token()
    except instagram_uploader.InstagramError as exc:
        _tell(
            "⚠️ <b>Instagram token yenilenemedi</b>\n"
            f"{exc}\n\n"
            "Token süresi dolduysa otomatik yenileme onu geri getiremez: "
            "Meta panelinden yeni bir token üretip "
            f"<code>{SECRET_NAME}</code> secret'ını elle güncellemen gerekiyor. "
            "Sonrasında bu iş devralır."
        )
        return 1

    try:
        update_secret(repository, SECRET_NAME, new_token)
    except SecretWriteError as exc:
        # The refresh already happened, so the OLD token is now the stale one
        # and the new value is about to be lost. Saying that plainly matters
        # more than the error itself.
        _tell(
            "⚠️ <b>Instagram token yenilendi ama kaydedilemedi</b>\n"
            f"{exc}\n\n"
            f"Yeni token {days} gün geçerli ama secret'a yazılamadığı için "
            "kullanılamayacak. Yazma izni düzeltilene kadar eski token "
            "geçerliliğini koruyor."
        )
        return 1

    print(f"Instagram token yenilendi, {days} gün geçerli.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
