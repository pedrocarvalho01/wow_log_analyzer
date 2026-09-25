"""One-off GraphQL schema introspection against the live Warcraft Logs API v2.

Run with: python -m wcl.introspect
Confirms the field/argument names that wcl/fetch.py relies on before they are
hard-coded into queries. Findings should be reconciled with the README's
"API notes / schema deltas" section.
"""
from __future__ import annotations

import json
import sys

from dotenv import load_dotenv

from wcl.client import graphql

TYPE_QUERY = """
query ($name: String!) {
  __type(name: $name) {
    name
    fields {
      name
      type { name kind ofType { name kind ofType { name kind } } }
      args { name type { name kind ofType { name kind } } }
    }
  }
}
"""


def describe_type(name: str) -> dict:
    return graphql(TYPE_QUERY, {"name": name})["__type"]


def main() -> None:
    load_dotenv()
    types_to_check = [
        "Report",
        "ReportData",
        "ReportFight",
        "ReportMasterData",
        "ReportActor",
        "ReportRankedCharacter",
        "TableDataType",
    ]
    result = {}
    for type_name in types_to_check:
        try:
            result[type_name] = describe_type(type_name)
        except Exception as exc:  # noqa: BLE001
            result[type_name] = {"error": str(exc)}

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())
