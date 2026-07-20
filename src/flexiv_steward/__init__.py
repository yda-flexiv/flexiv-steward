"""The Flexiv development-workspace command line utility."""


def main() -> None:
    """Run the command-line interface without importing it during package load."""
    from flexiv_steward.cli import main as cli_main

    cli_main()

if __name__ == "__main__":
    main()
