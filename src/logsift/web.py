"""A FastAPI log-analysis console over the logsift engine.

Upload or paste logs; they are parsed once and stored. Each dataset gets a dashboard — level
breakdown, a timeline of volume and errors, the most common message templates and top errors — and
an interactive viewer that filters by level, text, structured field and time window. The views reuse
the same :mod:`logsift.parsers`, :mod:`logsift.filters` and :mod:`logsift.stats` as the CLI.
"""

from __future__ import annotations

import re
from datetime import timedelta
from importlib import resources
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from logsift.filters import FilterSpec, parse_time_bound, parse_where
from logsift.models import Level, Record
from logsift.parsers import parse_lines
from logsift.stats import Summary, summarise
from logsift.store import LogStore

_PAGE_SIZE = 100
_BUCKET_CHOICES = [
    timedelta(seconds=1), timedelta(seconds=5), timedelta(seconds=15), timedelta(seconds=30),
    timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=30),
    timedelta(hours=1), timedelta(hours=3), timedelta(hours=6), timedelta(hours=12), timedelta(days=1),
]

_SAMPLE = "\n".join(
    [
        '{"ts":"2025-03-10T09:00:01Z","level":"info","msg":"Application started","version":"2.4.1"}',
        '{"ts":"2025-03-10T09:00:05Z","level":"info","msg":"GET /api/orders 200","status":200,"ms":42}',
        '{"ts":"2025-03-10T09:01:06Z","level":"info","msg":"GET /api/orders/118 200","status":200,"ms":18}',
        '{"ts":"2025-03-10T09:02:11Z","level":"warn","msg":"GET /api/orders/999 404","status":404,"ms":7}',
        '{"ts":"2025-03-10T09:03:20Z","level":"info","msg":"POST /api/orders 201","status":201,"ms":88}',
        '{"ts":"2025-03-10T09:04:45Z","level":"error","msg":"GET /api/orders/118 500","status":500,"ms":1203}',
        '{"ts":"2025-03-10T09:05:02Z","level":"error","msg":"GET /api/orders/204 500","status":500,"ms":1180}',
        '{"ts":"2025-03-10T09:06:18Z","level":"warn","msg":"Slow query took 842 ms","ms":842}',
        '{"ts":"2025-03-10T09:07:33Z","level":"info","msg":"GET /api/health 200","status":200,"ms":3}',
        '{"ts":"2025-03-10T09:08:50Z","level":"error","msg":"POST /api/orders 500","status":500,"ms":1340}',
        '{"ts":"2025-03-10T09:10:05Z","level":"info","msg":"Job reconcile completed","job":"reconcile"}',
        '{"ts":"2025-03-10T09:12:40Z","level":"fatal","msg":"Out of memory, restarting worker","worker":3}',
    ]
)


def _templates() -> Jinja2Templates:
    directory = resources.files("logsift") / "templates"
    return Jinja2Templates(directory=str(directory))


def _choose_bucket(span: timedelta, target: int = 40) -> timedelta:
    """Pick a round bucket size so a span is covered by roughly ``target`` buckets."""
    ideal = span.total_seconds() / target if span.total_seconds() > 0 else 1
    for choice in _BUCKET_CHOICES:
        if choice.total_seconds() >= ideal:
            return choice
    return _BUCKET_CHOICES[-1]


def _build_spec(params: dict[str, str]) -> FilterSpec:
    spec = FilterSpec(keep_untimed=params.get("drop_untimed") != "1")
    flags = re.IGNORECASE
    if params.get("level"):
        level = Level.parse(params["level"])
        if level is not Level.UNKNOWN:
            spec.min_level = level
    if params.get("only"):
        levels = {Level.parse(x) for x in params["only"].split(",") if x.strip()}
        levels.discard(Level.UNKNOWN)
        if levels:
            spec.exact_levels = levels
    if params.get("q"):
        spec.grep = [re.compile(params["q"], flags)]
    if params.get("exclude"):
        spec.exclude = [re.compile(params["exclude"], flags)]
    if params.get("where"):
        spec.where = dict([parse_where(params["where"])])
    if params.get("since"):
        spec.since = parse_time_bound(params["since"])
    if params.get("until"):
        spec.until = parse_time_bound(params["until"])
    return spec


def _level_rows(summary: Summary) -> list[dict[str, Any]]:
    rows = []
    for level, count in sorted(summary.by_level.items(), key=lambda kv: kv[0], reverse=True):
        name = level.name if level is not Level.UNKNOWN else "UNKNOWN"
        pct = 100.0 * count / summary.total if summary.total else 0.0
        rows.append({"name": name, "count": count, "pct": round(pct, 1)})
    return rows


def _timeline(summary: Summary) -> list[dict[str, Any]]:
    if not summary.buckets:
        return []
    peak = max(summary.buckets.values())
    out = []
    for start, total in sorted(summary.buckets.items()):
        errors = summary.error_buckets.get(start, 0)
        out.append(
            {
                "label": start.strftime("%H:%M"),
                "date": start.strftime("%Y-%m-%d %H:%M"),
                "total": total,
                "errors": errors,
                "height": round(100.0 * total / peak, 1),
                "error_height": round(100.0 * errors / peak, 1),
            }
        )
    return out


def _human_delta(delta: timedelta | None) -> str:
    if delta is None:
        return "-"
    seconds = int(delta.total_seconds())
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def create_app(db_path: str = ":memory:", *, seed: bool = True) -> FastAPI:
    app = FastAPI(title="Logsift Console")
    store = LogStore(db_path)
    templates = _templates()

    if seed and store.dataset_count() == 0:
        store.add_dataset("Sample API logs", list(parse_lines(_SAMPLE.splitlines(), source="sample")), "sample")

    app.state.store = store

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"datasets": store.list_datasets()},
        )

    @app.post("/datasets")
    async def upload(
        request: Request,
        name: str = Form(""),
        content: str = Form(""),
        file: UploadFile | None = None,
    ) -> Any:
        text = content
        source = "paste"
        if file is not None and file.filename:
            raw = await file.read()
            text = raw.decode("utf-8", errors="replace")
            source = file.filename
            name = name or file.filename
        if not text.strip():
            return templates.TemplateResponse(
                request=request,
                name="index.html",
                context={"datasets": store.list_datasets(), "error": "Paste some log lines or choose a file."},
                status_code=400,
            )
        records = list(parse_lines(text.splitlines(), source=source))
        dataset = store.add_dataset(name or "Untitled logs", records, source)
        return RedirectResponse(f"/datasets/{dataset.id}", status_code=303)

    def _load(dataset_id: int):
        dataset = store.get_dataset(dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="dataset not found")
        return dataset

    def _filtered(dataset_id: int, params: dict[str, str]) -> tuple[list[Record], Summary]:
        spec = _build_spec(params)
        predicate = spec.build()
        records = [r for r in store.records(dataset_id) if predicate(r)]
        base = summarise(records)
        summary = summarise(records, bucket=_choose_bucket(base.span)) if base.span else base
        return records, summary

    # Register the .json route before the bare one so "/datasets/1.json" is not captured
    # by "/datasets/{dataset_id}" (which would try to parse "1.json" as an int and 422).
    @app.get("/datasets/{dataset_id}.json")
    def dataset_json(request: Request, dataset_id: int) -> JSONResponse:
        _load(dataset_id)
        params = dict(request.query_params)
        try:
            _records, summary = _filtered(dataset_id, params)
        except (ValueError, re.error) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse(summary.to_dict(top=int(params.get("top", "10") or "10")))

    @app.get("/datasets/{dataset_id}", response_class=HTMLResponse)
    def dataset_page(request: Request, dataset_id: int) -> HTMLResponse:
        dataset = _load(dataset_id)
        params = dict(request.query_params)
        try:
            records, summary = _filtered(dataset_id, params)
        except (ValueError, re.error) as exc:
            return templates.TemplateResponse(
                request=request,
                name="dataset.html",
                context={
                    "dataset": dataset, "error": str(exc), "params": params,
                    "records": [], "summary": summarise([]), "levels": [], "timeline": [],
                    "span": "-", "page": 1, "pages": 1, "shown": 0,
                    "error_count": 0, "warn_count": 0, "top_messages": [], "top_errors": [],
                },
                status_code=400,
            )
        page = max(1, int(params.get("page", "1") or "1"))
        pages = max(1, (len(records) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = min(page, pages)
        window = records[(page - 1) * _PAGE_SIZE: page * _PAGE_SIZE]
        errors = summary.by_level.get(Level.ERROR, 0) + summary.by_level.get(Level.FATAL, 0)
        warnings = summary.by_level.get(Level.WARN, 0)
        return templates.TemplateResponse(
            request=request,
            name="dataset.html",
            context={
                "dataset": dataset,
                "params": params,
                "summary": summary,
                "error_count": errors,
                "warn_count": warnings,
                "levels": _level_rows(summary),
                "timeline": _timeline(summary),
                "span": _human_delta(summary.span),
                "top_messages": summary.templates.most_common(8),
                "top_errors": summary.error_templates.most_common(6),
                "records": window,
                "shown": len(records),
                "page": page,
                "pages": pages,
            },
        )

    return app
