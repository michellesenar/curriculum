"""
Owner commands for hosted testers. Run these on the server.

    curriculum-admin create "Yuka"          add a tester and print their link
    curriculum-admin list                   show active testers (never links)
    curriculum-admin rotate <id>            turn off their links, print a new one
    curriculum-admin remove <id>            delete their database and links
    curriculum-admin import <id> <file>     merge a saved graph into a tester's account

A link is shown once, when it is made. Only a hash is stored, so a lost link
can't be looked up, only replaced with `rotate`.
"""

import argparse
import os
import sys
from pathlib import Path

from curriculum import importer, tenants

PLACEHOLDER_HOST = "https://YOUR-HOST"


def _links(token: str, base_url: str | None) -> str:
    base = (base_url or os.environ.get("CURRICULUM_PUBLIC_URL") or PLACEHOLDER_HOST).rstrip("/")
    note = ""
    if base == PLACEHOLDER_HOST:
        note = (
            "\n(No public address set. Replace YOUR-HOST, or pass --base-url, or set "
            "CURRICULUM_PUBLIC_URL.)"
        )
    return (
        "Connector URL (give this to the tester; it is shown only once):\n"
        f"  {base}/c/{token}/mcp\n"
        "Export link (their data as one CSV; same address, ending in export.csv):\n"
        f"  {base}/c/{token}/export.csv"
        f"{note}"
    )


def _find(tenant_id: str) -> dict | None:
    return next((t for t in tenants.list_tenants() if t["id"] == tenant_id), None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curriculum-admin", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="add a tester and print their link")
    create.add_argument("label", help="a name for your own reference, e.g. Yuka")
    create.add_argument("--base-url", help="public address, e.g. https://curriculum.example.com")

    sub.add_parser("list", help="show active testers")

    rotate = sub.add_parser("rotate", help="turn off a tester's links and print a new one")
    rotate.add_argument("tenant_id")
    rotate.add_argument("--base-url")

    remove = sub.add_parser("remove", help="delete a tester's database and turn off their links")
    remove.add_argument("tenant_id")
    remove.add_argument("--yes", action="store_true", help="skip the confirmation question")

    imp = sub.add_parser("import", help="merge a saved graph (a hosted-schema .db file) into a tester's account")
    imp.add_argument("tenant_id")
    imp.add_argument("file", help="path to the .db file to merge in")
    imp.add_argument("--dry-run", action="store_true", help="show counts only; write nothing")

    args = parser.parse_args(argv)

    if args.command == "create":
        tenant_id, token = tenants.create_tenant(args.label)
        print(f"Created tester '{args.label}' (id {tenant_id}).")
        print(_links(token, args.base_url))
        return 0

    if args.command == "list":
        rows = tenants.list_tenants()
        if not rows:
            print("No testers yet.")
        for t in rows:
            print(f"{t['id']}  {t['label']}  (added {t['created_at'][:10]})")
        return 0

    if args.command == "rotate":
        if _find(args.tenant_id) is None:
            print(f"No active tester with id {args.tenant_id}.", file=sys.stderr)
            return 1
        token = tenants.rotate_token(args.tenant_id)
        print("Old links are off. Their data is untouched.")
        print(_links(token, args.base_url))
        return 0

    if args.command == "remove":
        tenant = _find(args.tenant_id)
        if tenant is None:
            print(f"No active tester with id {args.tenant_id}.", file=sys.stderr)
            return 1
        if not args.yes:
            answer = input(
                f"Delete '{tenant['label']}' and all their data? Type the name to confirm: "
            )
            if answer.strip() != tenant["label"]:
                print("Not confirmed. Nothing changed.")
                return 1
        tenants.remove_tenant(args.tenant_id)
        print(f"Removed '{tenant['label']}': database deleted, links off.")
        print("Backups still hold a copy until they expire (14 days).")
        return 0

    if args.command == "import":
        target = tenants.tenant_db_path(args.tenant_id)
        if target is None:
            print(f"No active tester with id {args.tenant_id}.", file=sys.stderr)
            return 1
        try:
            c = importer.import_graph(Path(args.file), target, dry_run=args.dry_run)
        except importer.ImportFailed as e:
            print(f"Not imported: {e}", file=sys.stderr)
            return 1
        verb = "Would add" if args.dry_run else "Added"
        print(
            f"{verb}: {c['topics_new']} topics, {c['connections_new']} connections, "
            f"{c['recommendations_new']} recommendations."
        )
        print(
            f"Already there or skipped: {c['topics_reused']} topics, "
            f"{c['connections_skipped']} connections, {c['recommendations_skipped']} recommendations."
        )
        if args.dry_run:
            print("Dry run: nothing was written.")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
