from __future__ import annotations

import argparse
import json

from .recorder import WorkRecorder
from .skills import SkillRegistry


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentvallet", description="Portable AI work and skill brain")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("skills", help="List portable skills")

    start = sub.add_parser("record-demo", help="Create a small local demo run")
    start.add_argument("goal")

    args = parser.parse_args()

    if args.command == "skills":
        registry = SkillRegistry()
        for skill in registry.list():
            print(f"{skill.name}\t{skill.version}\t{'trusted' if skill.trusted else skill.manifest.get('status', 'unknown')}")
        return

    if args.command == "record-demo":
        recorder = WorkRecorder()
        run = recorder.start(args.goal, source="cli-demo")
        recorder.event("note", "Demo observable work event")
        recorder.validate("demo_validation", True, "demo passed")
        finished = recorder.finish({"message": "demo complete"}, approved=True)
        print(json.dumps(finished.to_dict(), indent=2, ensure_ascii=False))
        recorder.close()


if __name__ == "__main__":
    main()
