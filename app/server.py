"""Local web app: `uv run rl app`. Reads books from disk; chats through app/chat.py.

Chat runs only on the Claude Code subscription (app/subscription.py). API credentials are removed
from this process's environment at import, so nothing the app starts can bill the Anthropic API.
"""

from __future__ import annotations

import json
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lib import config as C
from lib.store import read_json, write_text

from . import bookdata, chat, saved
from . import subscription as sub

sub.scrub_env()

STATIC = C.ROOT / "app" / "static"
SAFE = re.compile(r"^[A-Za-z0-9_-]+$")

app = FastAPI(title="Reddit Library", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/data/images", StaticFiles(directory=C.IMAGES), name="images")


def _safe(*parts: str) -> None:
    for p in parts:
        if not SAFE.match(p or ""):
            raise HTTPException(400, f"Invalid id {p!r}")


def _strip_front_matter(text: str) -> str:
    return text.split("---", 2)[2].lstrip() if text.startswith("---") else text


def _game_title(slug: str) -> str:
    path = C.GAMES / slug / "game.md"
    if path.exists():
        first = path.read_text(encoding="utf-8").splitlines()[:1]
        if first and first[0].startswith("# "):
            return first[0][2:].strip()
    return slug.replace("-", " ").title()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/tree")
def tree() -> dict:
    built = set(chat.built_books())
    games = sorted(p.parent.name for p in C.GAMES.glob("*/game.md"))
    return {
        "games": [{"id": g, "title": _game_title(g)} for g in games],
        "library": [{"id": "master", "title": "Master Book", "status": "soon"}] + [
            {"id": s, "title": f"r/{s}", "status": "ready" if s in built else "not built"}
            for s in C.config()["subreddits"]],
        "ideas": {"status": "soon"},
    }


@app.get("/api/models")
def models() -> dict:
    return {"models": [{"id": m["id"], "label": m["label"]} for m in chat.chat_models()],
            "default": chat.default_model()}


@app.get("/api/book/{book}")
def book(book: str) -> dict:
    _safe(book)
    if not (C.BOOKS / book / "index.md").exists():
        raise HTTPException(404, "Book not built yet")
    return bookdata.book_overview(book)


@app.get("/api/book/{book}/{chapter}")
def chapter(book: str, chapter: str) -> dict:
    _safe(book, chapter)
    data = bookdata.chapter_detail(book, chapter)
    if not data:
        raise HTTPException(404, "No such chapter")
    return data


@app.get("/api/thread/{thread_id}")
def thread(thread_id: str) -> dict:
    _safe(thread_id)
    data = read_json(C.THREADS / f"{thread_id}.json")
    if not data:
        raise HTTPException(404, "No such thread")
    return data


class GameText(BaseModel):
    markdown: str


class NewGame(BaseModel):
    name: str


@app.get("/api/game/{slug}")
def get_game(slug: str) -> dict:
    _safe(slug)
    path = C.GAMES / slug / "game.md"
    if not path.exists():
        raise HTTPException(404, "No such game")
    return {"markdown": path.read_text(encoding="utf-8"), "title": _game_title(slug)}


@app.put("/api/game/{slug}")
def put_game(slug: str, body: GameText) -> dict:
    _safe(slug)
    if not (C.GAMES / slug).is_dir():
        raise HTTPException(404, "No such game")
    write_text(C.GAMES / slug / "game.md", body.markdown)
    return {"ok": True}


@app.post("/api/games")
def add_game(body: NewGame) -> dict:
    name = body.name.strip()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not name or not slug:
        raise HTTPException(400, "Give the game a name")
    if (C.GAMES / slug).exists():
        raise HTTPException(409, "A game with that name already exists")
    write_text(C.GAMES / slug / "game.md",
               f"# {name}\n\n_Describe the game here: pitch, player count, play time, core loop, rules, and card "
               f"lists. The chat for this tab reads this file._\n")
    return {"id": slug, "title": name}


# --- chat (Claude Code subscription only) ----------------------------------------

class Msg(BaseModel):
    role: str
    text: str
    ts: str | None = None
    model: str | None = None
    model_label: str | None = None
    via: str | None = None
    usage: dict | None = None
    tokens: int | None = None
    tools_used: list | None = None
    sources: list | None = None


class ChatIn(BaseModel):
    message: str
    model: str | None = None
    history: list[Msg] = []
    saved_id: str | None = None


class SaveIn(BaseModel):
    messages: list[Msg]
    saved_id: str | None = None


class RenameIn(BaseModel):
    title: str


def _tab(tab: str) -> str:
    try:
        chat.tab_kind(tab)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return tab


@app.get("/api/chat/status")
def chat_status(refresh: bool = False) -> dict:
    s = sub.login_status(force=refresh)
    cfg = chat.chat_cfg()
    return {**s, "unsaved_confirm_after": cfg.get("unsaved_confirm_after", 4),
            "long_chat_hint_tokens": cfg.get("long_chat_hint_tokens", 60000)}


@app.post("/api/chat/{tab}")
async def post_chat(tab: str, body: ChatIn) -> StreamingResponse:
    _tab(tab)
    if not body.message.strip():
        raise HTTPException(400, "Empty message")
    try:
        chat.model_entry(body.model or chat.default_model())
        saved_chat = saved.get(tab, body.saved_id) if body.saved_id else None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    history = [m.model_dump(exclude_none=True) for m in body.history]

    async def events():
        try:
            async for ev in chat.send(tab, body.message.strip(), body.model, history, saved_chat):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'type': 'error', 'kind': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat/{tab}/save")
async def save_chat(tab: str, body: SaveIn) -> dict:
    _tab(tab)
    try:
        return await saved.save(tab, [m.model_dump(exclude_none=True) for m in body.messages], body.saved_id)
    except sub.SubscriptionError as e:
        raise HTTPException(503 if e.kind != "usage_limit" else 429, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/chat/{tab}/saved")
def list_saved(tab: str) -> dict:
    return {"saved": saved.list_saved(_tab(tab))}


@app.get("/api/chat/{tab}/saved/{saved_id}")
def get_saved(tab: str, saved_id: str) -> dict:
    try:
        d = saved.get(_tab(tab), saved_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not d:
        raise HTTPException(404, "No such saved chat")
    return d


@app.patch("/api/chat/{tab}/saved/{saved_id}")
def rename_saved(tab: str, saved_id: str, body: RenameIn) -> dict:
    try:
        return saved.rename(_tab(tab), saved_id, body.title)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/chat/{tab}/saved/{saved_id}")
def delete_saved(tab: str, saved_id: str) -> dict:
    try:
        saved.delete(_tab(tab), saved_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True}
