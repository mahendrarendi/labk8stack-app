"""K8s Lab platform: learn by fixing a broken cluster."""

from __future__ import annotations

from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import db
from .challenges import CHALLENGES, NODE_CPU, NODE_MEMORY_MIB, get_challenge
from .config import BASE_DIR, settings
from .engine import get_engine
from .manifests import safety_error
from .scoring import compute_score, seconds_left
from .security import COOKIE_NAME, read_user_id, sign_user


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


class SubmitBody(BaseModel):
    yaml: str = ""
    source: str = "editor"  # editor | terminal


class CommandBody(BaseModel):
    command: str = ""


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


def current_user(request: Request):
    user_id = read_user_id(request.cookies.get(COOKIE_NAME))
    return db.get_user(user_id) if user_id else None


def render(request: Request, template: str, **context):
    context.update(
        {
            "request": request,
            "app_name": settings.app_name,
            "tagline": settings.tagline,
            "mode": settings.mode,
            "user": current_user(request),
            "node_cpu": NODE_CPU,
            "node_memory": NODE_MEMORY_MIB,
            "leaderboard_days": settings.leaderboard_days,
        }
    )
    return templates.TemplateResponse(request, template, context)


def parse_document(text: str) -> tuple[dict | None, str | None]:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"Invalid YAML: {exc}"
    if not isinstance(document, dict):
        return None, "YAML must be a single Kubernetes resource object."
    if "kind" not in document:
        return None, "The resource must have a 'kind' field."
    problem = safety_error(document)
    if problem:
        return None, problem
    return document, None


# ------------------------------------------------------------------ pages

@app.get("/")
def index(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "index.html")


@app.post("/join")
def join(request: Request, name: str = Form(...)):
    clean = " ".join(name.split())
    if not (2 <= len(clean) <= 40):
        return render(request, "index.html", error="Name must be 2-40 characters.")
    user = db.get_or_create_user(clean)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        sign_user(user["id"]),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/dashboard")
def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)

    attempts = db.attempts_for_user(user["id"])
    cards = []
    for challenge in CHALLENGES.values():
        attempt = attempts.get(challenge.slug)
        if attempt is None:
            state, label = "todo", "Not started"
        elif attempt["status"] == "passed":
            state, label = "passed", f"Solved · {attempt['score']} pts"
        elif attempt["status"] == "active":
            left = seconds_left(attempt["started_at"], challenge.duration_seconds)
            state, label = "active", f"In progress · {left // 60}:{left % 60:02d}"
        else:
            state, label = "expired", "Time is up"
        cards.append({"challenge": challenge, "state": state, "label": label})

    return render(request, "dashboard.html", cards=cards)


@app.get("/challenge/{slug}")
def challenge_page(request: Request, slug: str):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)

    challenge = get_challenge(slug)
    if challenge is None:
        return RedirectResponse("/dashboard", status_code=303)

    attempt = db.active_attempt(user["id"], slug)
    passed_attempt = None
    if attempt is None:
        latest = db.latest_attempt(user["id"], slug)
        if latest and latest["status"] == "passed":
            passed_attempt = latest
        elif latest is None or latest["status"] == "expired":
            # A new attempt always starts from a clean sandbox. In kubernetes
            # mode this namespace has to be created before the page loads.
            get_engine().reset(user["id"], challenge)
            attempt = db.start_attempt(user["id"], slug)
        else:
            attempt = latest

    remaining = (
        seconds_left(attempt["started_at"], challenge.duration_seconds)
        if attempt and attempt["status"] == "active"
        else 0
    )
    return render(
        request,
        "challenge.html",
        challenge=challenge,
        attempt=attempt,
        remaining=remaining,
        passed_attempt=passed_attempt,
        editor_value=challenge.broken_yaml,
    )


@app.post("/challenge/{slug}/retry")
def retry(request: Request, slug: str):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    challenge = get_challenge(slug)
    if challenge:
        get_engine().reset(user["id"], challenge)
        db.start_attempt(user["id"], slug)
    return RedirectResponse(f"/challenge/{slug}", status_code=303)


@app.get("/leaderboard")
def leaderboard(request: Request):
    rows = db.leaderboard(settings.leaderboard_days)
    return render(request, "leaderboard.html", rows=rows)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "mode": settings.mode}


# -------------------------------------------------------------------- api

def _guard(request: Request):
    user = current_user(request)
    if not user:
        return None, JSONResponse(
            {"ok": False, "output": "Session expired. Reload the page."}, status_code=401
        )
    return user, None


@app.post("/api/challenge/{slug}/apply")
def api_apply(request: Request, slug: str, body: SubmitBody):
    user, error = _guard(request)
    if error:
        return error
    challenge = get_challenge(slug)
    if challenge is None:
        return JSONResponse({"ok": False, "output": "Challenge not found."}, status_code=404)

    document, parse_error = parse_document(body.yaml)
    if parse_error:
        return JSONResponse({"ok": False, "output": f"error: {parse_error}"}, status_code=400)

    result = get_engine().apply(user["id"], challenge, document)
    return JSONResponse({"ok": result.ok, "output": result.output})


@app.post("/api/challenge/{slug}/terminal")
def api_terminal(request: Request, slug: str, body: CommandBody):
    user, error = _guard(request)
    if error:
        return error
    challenge = get_challenge(slug)
    if challenge is None:
        return JSONResponse({"ok": False, "output": "Challenge not found."}, status_code=404)

    command = body.command.strip()
    if len(command) > 400:
        return JSONResponse({"output": "error: command too long."}, status_code=200)

    output = get_engine().terminal(user["id"], challenge, command)
    return JSONResponse({"output": output})


@app.post("/api/challenge/{slug}/verify")
def api_verify(request: Request, slug: str, body: SubmitBody):
    user, error = _guard(request)
    if error:
        return error
    challenge = get_challenge(slug)
    if challenge is None:
        return JSONResponse({"ok": False, "output": "Challenge not found."}, status_code=404)

    engine = get_engine()

    if body.source == "terminal":
        document = engine.current(user["id"], challenge)
        if document is None:
            return JSONResponse(
                {
                    "passed": False,
                    "message": "The sandbox has no resource to check. Use the terminal or editor first.",
                },
                status_code=409,
            )
    else:
        document, parse_error = parse_document(body.yaml)
        if parse_error:
            return JSONResponse(
                {"passed": False, "message": f"error: {parse_error}"}, status_code=400
            )
        # The editor behaves like `kubectl apply`: state changes before checking.
        applied = engine.apply(user["id"], challenge, document)
        if not applied.ok:
            return JSONResponse(
                {"passed": False, "message": applied.output}, status_code=200
            )
        document = engine.current(user["id"], challenge) or document

    attempt = db.active_attempt(user["id"], slug)
    if attempt is None:
        return JSONResponse(
            {"passed": False, "message": "No active attempt. Reload the page."},
            status_code=409,
        )

    remaining = seconds_left(attempt["started_at"], challenge.duration_seconds)
    if remaining <= 0:
        db.finish_attempt(attempt["id"], "expired", 0)
        return JSONResponse(
            {
                "passed": False,
                "expired": True,
                "message": "Time is up. This attempt scores 0.",
            },
            status_code=200,
        )

    passed, message = challenge.validate(document)
    tries = db.record_try(attempt["id"])

    if passed:
        elapsed = challenge.duration_seconds - remaining
        score = compute_score(
            challenge.points, challenge.duration_seconds, elapsed, tries
        )
        db.finish_attempt(attempt["id"], "passed", score)
        return JSONResponse(
            {
                "passed": True,
                "message": message,
                "score": score,
                "tries": tries,
                "remaining": remaining,
            }
        )

    return JSONResponse(
        {
            "passed": False,
            "message": message,
            "tries": tries,
            "remaining": remaining,
        }
    )


@app.post("/api/challenge/{slug}/reset")
def api_reset(request: Request, slug: str):
    user, error = _guard(request)
    if error:
        return error
    challenge = get_challenge(slug)
    if challenge is None:
        return JSONResponse({"ok": False, "output": "Challenge not found."}, status_code=404)
    result = get_engine().reset(user["id"], challenge)
    return JSONResponse({"ok": result.ok, "output": result.output})
