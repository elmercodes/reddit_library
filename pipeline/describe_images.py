"""Phase 3: pick posts where the picture is the point, download up to N images, describe once.

Free part:  selection + download to data/images/<id>/<n>.jpg (resized to what Claude would see).
Paid part:  one Claude call per post (all its images together, with the post's title and text),
            saved to data/images/<id>/<n>.md and data/images/<id>/notes.json. Later steps read
            these notes and never resend the images.
Ledger:     images_done = "described" | "skipped: <reason>"
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image

from lib import claude
from lib import config as C
from lib import costs
from lib.ledger import Ledger, now
from lib.store import read_json, write_json, write_text

MAX_EDGE = 1568          # Claude downsizes beyond this anyway; sending more wastes upload
MAX_PIXELS = 1_150_000
PROMPT_OVERHEAD_TOKENS = 700      # system prompt + post text + schema, per post (estimate)
OUTPUT_TOKENS_PER_IMAGE = 350
OUTPUT_TOKENS_PER_POST = 150

REFERS_TO_IMAGE = re.compile(
    r"\b(this|these|the|my|our|first|second|third|which|both)\s+"
    r"(card|cards|image|images|pic|pics|picture|pictures|layout|layouts|design|designs|art|artwork|"
    r"frame|frames|template|templates|prototype|prototypes|mockup|version|versions|option|options|back|icon|icons|logo)\b"
    r"|\b(attached|above|below|swipe|slide|left|right|pictured|shown)\b", re.I)


def image_dir(post_id: str) -> Path:
    return C.IMAGES / post_id


def notes_path(post_id: str) -> Path:
    return image_dir(post_id) / "notes.json"


def load_raw(sub: str, post_id: str) -> dict | None:
    return read_json(C.RAW / sub / f"{post_id}.json")


# --- selection ----------------------------------------------------------------

def select(post: dict) -> tuple[bool, str]:
    """(describe?, reason). Cheap, transparent rules from config.yaml `images`."""
    icfg = C.config()["images"]
    if post.get("is_video"):
        return False, "video post"
    if not post.get("image_urls"):
        return False, "no images"
    title = (post.get("title") or "").lower()
    flair = (post.get("flair") or "").lower()
    body = post.get("selftext") or ""
    if any(k in title or k in flair for k in icfg["skip_keywords"]):
        return False, "meme/collection keyword"
    if flair in {f.lower() for f in icfg.get("design_flairs", [])}:
        return True, f"flair '{post.get('flair')}'"
    hit = next((k for k in icfg["design_keywords"] if k in title or k in flair), None)
    if hit:
        return True, f"keyword '{hit}'"
    if REFERS_TO_IMAGE.search(post.get("title") or "") or REFERS_TO_IMAGE.search(body):
        return True, "text refers to the image"
    return False, "no sign the image is the point"


# --- download (free) ----------------------------------------------------------

@dataclass
class LocalImage:
    path: Path
    width: int
    height: int

    @property
    def est_tokens(self) -> int:
        return max(1, round(self.width * self.height / 750))


def _fit(img: Image.Image) -> Image.Image:
    w, h = img.size
    scale = min(1.0, MAX_EDGE / max(w, h), (MAX_PIXELS / (w * h)) ** 0.5)
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return img


def download(post: dict) -> list[LocalImage]:
    """Download up to images.max_per_post images (skips files already on disk)."""
    limit = C.config()["images"]["max_per_post"]
    out_dir = image_dir(post["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    result = []
    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (research-library image fetch)"}) as http:
        for n, url in enumerate(post["image_urls"][:limit], start=1):
            path = out_dir / f"{n}.jpg"
            if not path.exists():
                try:
                    r = http.get(url)
                    r.raise_for_status()
                    img = Image.open(io.BytesIO(r.content))
                    img.seek(0)  # first frame of GIFs
                    img = _fit(img.convert("RGB"))
                    img.save(path, "JPEG", quality=88)
                except Exception as e:  # noqa: BLE001
                    print(f"  could not download {url}: {e}")
                    continue
            with Image.open(path) as im:
                result.append(LocalImage(path, *im.size))
    return result


# --- describe (paid) ----------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {
        "design_relevant": {"type": "boolean"},
        "skip_reason": {"type": "string"},
        "what_is_asked": {"type": "string"},
        "images": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "description": {"type": "string"}},
                "required": ["index", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["design_relevant", "skip_reason", "what_is_asked", "images"],
    "additionalProperties": False,
}


def estimate_usd(images_by_post: dict[str, list[LocalImage]], model: str) -> tuple[float, int, int]:
    """Priced per post (each post is its own request, which matters for Haiku's long-context tier)."""
    usd = tin = tout = 0
    for imgs in images_by_post.values():
        i = PROMPT_OVERHEAD_TOKENS + sum(x.est_tokens for x in imgs)
        o = OUTPUT_TOKENS_PER_POST + OUTPUT_TOKENS_PER_IMAGE * len(imgs)
        usd += costs.claude_cost(model, i, o)
        tin += i
        tout += o
    return usd, tin, tout


def describe(post: dict, images: list[LocalImage], model: str) -> tuple[dict, float, dict]:
    system = (C.PROMPTS / "describe_images.md").read_text(encoding="utf-8")
    content: list = []
    for n, img in enumerate(images, start=1):
        content.append({"type": "text", "text": f"Image {n}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(img.path.read_bytes()).decode("ascii")}})
    body = (post.get("selftext") or "")[:2000]
    content.append({"type": "text", "text":
                    f"Subreddit: r/{post['subreddit']}\nFlair: {post.get('flair')}\nTitle: {post['title']}\n"
                    f"Post text: {body or '(none)'}\n\nDescribe images 1-{len(images)}."})
    result, cost, usage = claude.call("images", [{"role": "user", "content": content}],
                                      system=system, schema=SCHEMA, max_tokens=8000, model=model)
    return result, cost, usage


def save_notes(post: dict, images: list[LocalImage], result: dict, cost: float, model: str) -> None:
    by_index = {d["index"]: d["description"] for d in result["images"]}
    previous = read_json(notes_path(post["id"]))
    notes = {"post_id": post["id"], "described_at": now(), "model": model,
             "cost_usd": round(cost, 6), "design_relevant": result["design_relevant"],
             "skip_reason": result["skip_reason"], "what_is_asked": result["what_is_asked"],
             "images": []}
    for n, img in enumerate(images, start=1):
        desc = by_index.get(n, "")
        write_text(img.path.with_suffix(".md"), f"# Image {n}: {post['title']}\n\n{desc}\n")
        notes["images"].append({"file": img.path.name, "index": n, "description": desc})
    if previous:  # keep a record of what a rerun replaced
        notes["replaced"] = [{k: previous.get(k) for k in ("model", "described_at", "cost_usd")}] + previous.get("replaced", [])
    write_json(notes_path(post["id"]), notes)


# --- the phase ----------------------------------------------------------------

def run(sub: str, model: str | None = None, ids: list[str] | None = None) -> None:
    """Describe images for a subreddit.

    With `ids`, only those posts are (re)described, even if already done, and only they are paid
    for. Combine with `model` (e.g. "opus") to redo posts a cheaper model misread.
    """
    model = claude.resolve_model(model) or claude.model_for("images")
    ledger = Ledger()
    chosen: dict[str, tuple[dict, list[LocalImage]]] = {}
    skipped: dict[str, int] = {}

    if ids:
        for pid in ids:
            raw = load_raw(sub, pid)
            if not raw:
                print(f"  {pid}: not a fetched r/{sub} thread; ignored.")
                continue
            post = raw["post"]
            imgs = download(post)  # already on disk: no download cost
            if not imgs:
                print(f"  {pid}: no images; ignored.")
                continue
            chosen[pid] = (post, imgs)
        print(f"Re-describing {len(chosen)} listed post(s) on {model}.")
    else:
        all_ids = sorted(i for i, e in ledger.threads.items() if e.get("subreddit") == sub and e.get("fetched_at"))
        todo = [i for i in all_ids if not ledger.has(i, "images_done")]
        print(f"r/{sub}: {len(all_ids)} fetched threads, {len(all_ids) - len(todo)} already done for images.")
        for pid in todo:
            post = load_raw(sub, pid)["post"]
            ok, reason = select(post)
            if not ok:
                ledger.mark(pid, images_done=f"skipped: {reason}")
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            imgs = download(post)
            if not imgs:
                ledger.mark(pid, images_done="skipped: download failed")
                skipped["download failed"] = skipped.get("download failed", 0) + 1
                continue
            chosen[pid] = (post, imgs)
        ledger.save()
        n_img = sum(len(v[1]) for v in chosen.values())
        print(f"Selected {len(chosen)} posts ({n_img} images, downloaded to data/images/). Skipped: {skipped or 'none'}")
    if not chosen:
        return

    n_img = sum(len(v[1]) for v in chosen.values())
    usd, tin, tout = estimate_usd({k: v[1] for k, v in chosen.items()}, model)
    est = costs.Estimate(f"Phase 3 image descriptions for r/{sub}")
    est.add(f"{len(chosen)} posts / {n_img} images on {model}", f"~{tin:,} in / {tout:,} out", usd)
    if not costs.confirm(est):
        print("Cancelled. Images stay downloaded; nothing was sent to Claude.")
        return

    spend = claude.Spend()
    run_id = costs.new_run_id("phase3-images")
    described = failed = 0
    try:
        for pid, (post, imgs) in chosen.items():
            try:
                result, cost, usage = describe(post, imgs, model)
            except claude.ClaudeError as e:
                print(f"  {pid}: {e}")
                failed += 1
                continue
            spend.add(cost, usage)
            save_notes(post, imgs, result, cost, model)
            # A rerun invalidates this thread's extraction so Phase 4 picks up the new notes.
            ledger.mark(pid, images_done="described", images_described=len(imgs), images_model=model,
                        **({"extracted_at": None} if ids else {}))
            ledger.save()
            described += 1
            flag = "" if result["design_relevant"] else f"  (not design: {result['skip_reason']})"
            print(f"  [{described}/{len(chosen)}] {pid} {post['title'][:55]}  ${cost:.4f}{flag}")
    finally:
        if spend.calls:
            what = f"rerun {len(chosen)} listed posts" if ids else f"{described} posts"
            costs.log(run_id, "phase3", "anthropic", model, f"r/{sub} image descriptions ({what})",
                      spend.units(), usd, spend.usd)
    print(f"\nDescribed {described} posts for ${spend.usd:.4f} (estimate ${usd:.4f}). Failed: {failed}.")
