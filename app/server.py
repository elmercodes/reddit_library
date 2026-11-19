"""Local web app: `uv run rl app`. Reads books from disk; chats through app/chat.py."""

from __future__ import annotations

import json
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lib import config as C
from lib.store import read_json, write_text

from . import bookdata, chat

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


# --- chat --------------------------------------------------------------------------

class ChatIn(BaseModel):
    message: str
    model: str | None = None


class ModelIn(BaseModel):
    model: str


def _tab(tab: str) -> str:
    try:
        chat.tab_kind(tab)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return tab


@app.get("/api/chat/{tab}")
def get_chat(tab: str) -> dict:
    return chat.load(_tab(tab))


@app.put("/api/chat/{tab}/model")
def put_model(tab: str, body: ModelIn) -> dict:
    try:
        return chat.set_model(_tab(tab), body.model)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/chat/{tab}/clear")
def clear_chat(tab: str) -> dict:
    return chat.clear(_tab(tab))


@app.post("/api/chat/{tab}")
def post_chat(tab: str, body: ChatIn) -> StreamingResponse:
    _tab(tab)
    if not body.message.strip():
        raise HTTPException(400, "Empty message")

    def events():
        try:
            for ev in chat.send(tab, body.message.strip(), body.model):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
