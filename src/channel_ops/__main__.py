"""Package entry point: ``python -m channel_ops``."""
import sys


def _main() -> None:
    from .cli import main
    try:
        main()
    except (
        FileExistsError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        KeyError,
        PermissionError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    _main()
