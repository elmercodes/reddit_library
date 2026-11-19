"""Cost estimates, the y/N gate, and data/costs.csv logging."""

from __future__ import annotations

import csv
import sys
import uuid
from dataclasses import dataclass, field

from . import config as C
from .ledger import now

CSV_FIELDS = [
    "timestamp", "run_id", "phase", "service", "model_or_actor",
    "description", "units", "estimated_usd", "actual_usd",
]


@dataclass
class LineItem:
    description: str
    units: str          # e.g. "500 results", "1.2M in / 0.3M out tokens"
    usd: float


@dataclass
class Estimate:
    title: str
    items: list[LineItem] = field(default_factory=list)

    def add(self, description: str, units: str, usd: float) -> None:
        self.items.append(LineItem(description, units, usd))

    @property
    def total(self) -> float:
        return sum(i.usd for i in self.items)

    def render(self) -> str:
        width = max([len(i.description) for i in self.items] + [20])
        lines = [f"\nCost estimate: {self.title}", "-" * (width + 40)]
        for i in self.items:
            lines.append(f"  {i.description:<{width}}  {i.units:<24} ${i.usd:,.4f}")
        lines.append("-" * (width + 40))
        lines.append(f"  {'TOTAL':<{width}}  {'':<24} ${self.total:,.4f}")
        return "\n".join(lines)


def confirm(estimate: Estimate, assume_yes: bool = False, max_spend: float | None = None) -> bool:
    """Print the estimate and wait for y/N.

    `assume_yes` / `max_spend` exist only for a future scheduler; interactive runs never use them.
    """
    print(estimate.render())
    if assume_yes:
        if max_spend is None or estimate.total > max_spend:
            print(f"Refusing unattended run: estimate ${estimate.total:.4f} exceeds --max-spend {max_spend}.")
            return False
        print("Proceeding unattended (--yes, within --max-spend).")
        return True
    if not sys.stdin.isatty():
        print("No terminal to confirm on; not proceeding.")
        return False
    answer = input("Proceed? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def new_run_id(phase: str) -> str:
    return f"{phase}-{now().replace(':', '').replace('-', '')[:15]}-{uuid.uuid4().hex[:6]}"


def log(run_id: str, phase: str, service: str, model_or_actor: str,
        description: str, units: str, estimated_usd: float, actual_usd: float | None) -> None:
    path = C.COSTS
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow({
            "timestamp": now(), "run_id": run_id, "phase": phase, "service": service,
            "model_or_actor": model_or_actor, "description": description, "units": units,
            "estimated_usd": f"{estimated_usd:.6f}",
            "actual_usd": "" if actual_usd is None else f"{actual_usd:.6f}",
        })


# --- Claude token math -------------------------------------------------------

def claude_cost(model: str, input_tokens: int = 0, output_tokens: int = 0,
                cache_write_tokens: int = 0, cache_read_tokens: int = 0,
                batch: bool = False) -> float:
    p = C.price(model)
    usd = (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_write_tokens * p["cache_write"]
        + cache_read_tokens * p["cache_read"]
    ) / 1_000_000
    if batch:
        usd *= C.config().get("batch_discount", 0.5)
    return usd


def cost_from_usage(model: str, usage, batch: bool = False) -> float:
    """Cost of one Anthropic response from its `usage` object (or dict)."""
    get = (lambda k: usage.get(k) or 0) if isinstance(usage, dict) else (lambda k: getattr(usage, k, 0) or 0)
    return claude_cost(
        model,
        input_tokens=get("input_tokens"),
        output_tokens=get("output_tokens"),
        cache_write_tokens=get("cache_creation_input_tokens"),
        cache_read_tokens=get("cache_read_input_tokens"),
        batch=batch,
    )


def apify_cost(results: int, collector_cfg: dict) -> float:
    return results * collector_cfg["price_per_1000_results"] / 1000 + collector_cfg.get("start_fee_usd", 0.0)
