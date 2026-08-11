import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="conet", description="CoNET operator CLI")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Show control plane status")
    subparsers.add_parser("agents", help="List registered agents")

    args = parser.parse_args()

    if args.command == "status":
        print("CoNET control plane: not yet implemented")
    elif args.command == "agents":
        print("No agents registered")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
