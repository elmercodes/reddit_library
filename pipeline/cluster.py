"""Phase 5, Step A: propose chapters for a subreddit book and assign every thread.

Output: books/<sub>/plan.json  {"status": "proposed"|"approved", "chapters": [...], "assignments": {...}}
Heat and counts are computed in code from the thread files.
"""

from __future__ import annotations

import json

from lib import claude
from lib import config as C
from lib import costs
from lib.ledger import now
from lib.bookstats import book_dir, heat_table, load_threads, plan_path  # noqa: F401 (re-exported)
from lib.store import read_json, write_json

CHARS_PER_TOKEN = 3.8
SCHEMA_OVERHEAD_TOKENS = 1500
EST_OUTPUT_TOKENS = 16000        # chapter list + 100 assignments + high-effort thinking
MAX_OUTPUT_TOKENS = 18000        # hard ceiling: keeps a worst-case run near the approved budget

SCHEMA = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["slug", "title", "description", "keywords"],
                "additionalProperties": False,
            },
        },
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "primary": {"type": "string"},
                    "secondary": {"type": "string"},
                },
                "required": ["id", "primary", "secondary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["chapters", "assignments"],
    "additionalProperties": False,
}


def digest(d: dict) -> dict:
    return {"id": d["id"], "title": d["title"], "flair": d.get("flair"),
            "engagement": d["engagement_score"], "upvotes": d["upvotes"], "comments": d["comments"],
            "topics": d["topics"], "summary": d["summary"],
            "consensus": [c["point"] for c in d["consensus"]],
            "splits": [x["issue"] for x in d["disagreements"]],
            "hooks": [h["hook"] for h in d["content_ideas"]]}


def build_input(threads: dict[str, dict]) -> str:
    rows = sorted(threads.values(), key=lambda d: -(d["engagement_score"] or 0))
    return "\n".join(json.dumps(digest(d), ensure_ascii=False) for d in rows)


def estimate(sub: str, threads: dict[str, dict]) -> tuple[float, int, int]:
    model = claude.model_for("books")
    prompt = (C.PROMPTS / "cluster_chapters.md").read_text(encoding="utf-8")
    tin = int((len(prompt) + len(build_input(threads))) / CHARS_PER_TOKEN) + SCHEMA_OVERHEAD_TOKENS
    return costs.claude_cost(model, tin, EST_OUTPUT_TOKENS), tin, EST_OUTPUT_TOKENS


def validate(result: dict, threads: dict[str, dict]) -> tuple[dict, list[str]]:
    """Normalize the model's output and list any problems (fixed where possible)."""
    problems = []
    slugs = [c["slug"] for c in result["chapters"]]
    if len(set(slugs)) != len(slugs):
        problems.append("duplicate chapter slugs")
    assignments = {}
    for a in result["assignments"]:
        if a["id"] not in threads:
            problems.append(f"unknown thread {a['id']} ignored")
            continue
        if a["id"] in assignments:
            problems.append(f"thread {a['id']} assigned twice; kept first")
            continue
        if a["primary"] not in slugs:
            problems.append(f"thread {a['id']} has unknown primary chapter {a['primary']!r}")
        sec = a["secondary"] if a["secondary"] in slugs and a["secondary"] != a["primary"] else ""
        assignments[a["id"]] = {"primary": a["primary"], "secondary": sec}
    missing = [i for i in threads if i not in assignments]
    if missing:
        problems.append(f"{len(missing)} threads not assigned: {missing}")
    if not 8 <= len(slugs) <= 20:
        problems.append(f"{len(slugs)} chapters (target 8-20)")
    return {"chapters": result["chapters"], "assignments": assignments}, problems


def propose(sub: str) -> None:
    threads = load_threads(sub)
    if not threads:
        print(f"No extracted threads for r/{sub}. Run Phase 4 first.")
        return
    existing = read_json(plan_path(sub))
    if existing:
        print(f"A chapter plan already exists ({existing['status']}, {existing['created_at'][:10]}). "
              f"Delete {plan_path(sub).relative_to(C.ROOT)} to propose a new one.")
        return

    model = claude.model_for("books")
    usd, tin, tout = estimate(sub, threads)
    est = costs.Estimate(f"Phase 5 Step A: chapters for r/{sub}")
    est.add(f"1 call on {model}, effort {claude.effort_for('books')}, {len(threads)} thread digests",
            f"~{tin:,} in / ~{tout:,} out", usd)
    if not costs.confirm(est):
        print("Cancelled. Nothing was sent to Claude.")
        return

    system = (C.PROMPTS / "cluster_chapters.md").read_text(encoding="utf-8")
    run_id = costs.new_run_id("phase5-cluster")
    try:
        result, cost, usage = claude.call(
            "books", [{"role": "user", "content": f"Subreddit: r/{sub}\n\nThread digests (one JSON per line):\n"
                                                  + build_input(threads)}],
            system=system, schema=SCHEMA, max_tokens=MAX_OUTPUT_TOKENS)
    except claude.ClaudeError as e:
        if e.cost:
            costs.log(run_id, "phase5", "anthropic", model, f"r/{sub} chapter proposal (FAILED: {e})",
                      f"{e.usage}", usd, e.cost)
        print(f"Chapter proposal failed: {e} (charged ${e.cost:.4f}, logged).")
        return
    spend = claude.Spend()
    spend.add(cost, usage)
    costs.log(run_id, "phase5", "anthropic", model, f"r/{sub} chapter proposal", spend.units(), usd, cost)

    plan, problems = validate(result, threads)
    plan = {"subreddit": sub, "status": "proposed", "created_at": now(), "model": model,
            "cost_usd": round(cost, 6), **plan}
    write_json(plan_path(sub), plan)
    print(f"\nChapter proposal saved to {plan_path(sub).relative_to(C.ROOT)} (cost ${cost:.4f}, estimate ${usd:.4f}).")
    if problems:
        print("Problems to review:", *problems, sep="\n  - ")
    show(sub)


def show(sub: str) -> None:
    plan = read_json(plan_path(sub))
    if not plan:
        print(f"No chapter plan for r/{sub}.")
        return
    threads = load_threads(sub)
    rows = heat_table(plan, threads)
    desc = {c["slug"]: c["description"] for c in plan["chapters"]}
    print(f"\nr/{sub}: {len(plan['chapters'])} chapters, status {plan['status']}\n")
    print(f"{'heat':>7}  {'thr':>3} {'+2nd':>4}  {'upvotes':>7} {'cmts':>5}  chapter")
    for r in rows:
        print(f"{r['heat']:>7.1f}  {r['threads']:>3} {r['secondary']:>4}  {r['upvotes']:>7} {r['comments']:>5}  "
              f"{r['title']} [{r['slug']}]")
        print(f"{'':>33}{desc[r['slug']]}")
