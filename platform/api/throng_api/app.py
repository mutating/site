import asyncio
import json
import math
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import IntegrityError

from throng_api import (
    __version__,
    accounts,
    acme,
    agents,
    artifacts,
    avatars,
    computers,
    controller,
    enrollment,
    invitations,
    jobs,
    powercell,
    resources,
)
from throng_api.config import Settings
from throng_api.database import Database
from throng_api.models import utcnow


def create_app(settings: Settings | None = None, *, clock=utcnow):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        settings.storage_path.mkdir(parents=True, exist_ok=True)
        app.state.db = Database(settings.database_url)
        task = None
        stop = asyncio.Event()
        try:
            await app.state.db.check()
            if settings.controller_enabled:
                task = asyncio.create_task(controller.run(app, stop), name="throng-controller")
            yield
        finally:
            if task:
                # Finish the current transaction before disposing connections.
                # Cancelling an in-flight SQLite statement can retain its write lock.
                stop.set()
                await task
            await app.state.db.close()

    app = FastAPI(
        title="Throng API",
        version=__version__,
        lifespan=lifespan,
        description=(
            "Серверный API Throng с очередью и контроллером заданий.\n\n"
            "**Вход:** зарегистрируйтесь через `POST /api/v1/users`, затем отправьте "
            "email или ник и пароль в `POST /api/v1/auth/login`. В **Authorize → AccessToken** "
            "вставьте полученный `access_token`. Регистрация, вход и health доступны без токена.\n\n"
            "**Права:** `read` — чтение, `execute` — команды, изоляты и артефакты, "
            "`manage` — управление учётными записями и ресурсами. Дополнительно проверяются "
            "владелец ресурса и роль в компании (`creator`, `admin`, `user`).\n\n"
            "**Ресурсы:** CPU — millicores (1000 = ядро), память — байты. "
            "`occupied` означает резерв под изоляты и изменяется сервером. "
            "Каталог `/api/v1/resource-types` доступен только системному администратору "
            "с личным токеном; роль назначается CLI `throng-api system-admin <user-uuid>`. "
            "Владельцы и члены компаний читают свои сводки и историю через "
            "`/users/{id}/resources/*` и `/companies/{id}/resources/*`. "
            "История использует интервал `[from,to)` с часовым поясом и "
            "пагинацию `snapshot_revision` / `after_revision`.\n\n"
            "**Исполнение:** загрузите исходный файл, создайте изолят и дождитесь `ready`. "
            "Создание изолята и команды возвращает `202` с ID; статусы и результаты "
            "доступны через `/api/v1/commands/statuses` и `/api/v1/commands/results`. "
            "Внешняя отмена доставленной команды проходит через `cancel_requested` "
            "до подтверждения остановки агентом.\n\n"
            "**Агент:** используйте **Authorize → AgentCredential**, затем "
            "`POST /agent/v1/session`. Для остальных запросов, кроме чтения текущей сессии, "
            "указывайте `X-Agent-Session`. Heartbeat продлевает lease; задания выдаются "
            "исходящим long polling.\n\n"
            "Файлы передаются сырыми байтами (`application/octet-stream`). "
            "Плагин Throng, исполняющий агент, веб-интерфейс и админка реализуются отдельно."
        ),
    )
    app.state.powercell_authority = enrollment.configure_authority(settings)
    app.state.settings = settings
    app.state.clock = clock
    app.state.last_controller_tick = None

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exception):
        # Validation diagnostics include the rejected input. Escape lone
        # surrogates so reporting malformed Unicode cannot itself become a 500.
        return Response(
            json.dumps(
                {
                    "detail": jsonable_encoder(
                        exception.errors(),
                        custom_encoder={
                            float: lambda value: value if math.isfinite(value) else None,
                            bytes: lambda value: value.decode("utf-8", errors="replace"),
                        },
                    )
                },
                ensure_ascii=True,
                allow_nan=False,
            ),
            status_code=422,
            media_type="application/json",
        )

    @app.exception_handler(404)
    async def resource_not_found(request, exception):
        # Lookup and ownership failures must not reveal resource existence
        # through different messages, including checks of a parent company.
        return JSONResponse(
            status_code=404,
            content={"detail": "Resource not found"},
            headers=exception.headers,
        )

    @app.exception_handler(IntegrityError)
    async def constraint_error(request, exception):
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Conflict: duplicate value, referenced resource, or database constraint"
            },
        )

    @app.get("/health/live", tags=["health"])
    async def live():
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready", tags=["health"])
    async def ready():
        try:
            await app.state.db.check()
        except Exception as error:
            raise HTTPException(503, "Database unavailable") from error
        if settings.controller_enabled:
            last = app.state.last_controller_tick
            if last is None or (clock() - last).total_seconds() > max(
                10, settings.controller_interval * 5
            ):
                raise HTTPException(503, "Controller is not making progress")
        return {"status": "ready"}

    for router in (
        avatars.router,
        accounts.public_router,
        accounts.router,
        invitations.router,
        computers.router,
        artifacts.router,
        jobs.router,
        agents.router,
        powercell.router,
        powercell.user_router,
        enrollment.router,
        enrollment.public_router,
        acme.router,
        resources.router,
    ):
        app.include_router(router)
    return app


app = create_app()
