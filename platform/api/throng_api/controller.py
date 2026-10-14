import asyncio
import logging

from throng_api.execution import tick
from throng_api.powercell import prune_protocol

logger = logging.getLogger(__name__)


async def run_once(app):
    async with app.state.db.write() as session:
        await tick(session, app.state.clock(), app.state.settings)
        await prune_protocol(session, app.state.clock())
    app.state.last_controller_tick = app.state.clock()


async def run(app, stop: asyncio.Event):
    while not stop.is_set():
        try:
            await run_once(app)
        except Exception:
            logger.exception("Controller tick failed; durable work will be retried")
        try:
            await asyncio.wait_for(stop.wait(), timeout=app.state.settings.controller_interval)
        except TimeoutError:
            pass
