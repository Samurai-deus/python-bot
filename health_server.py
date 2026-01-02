import asyncio
import json
import time
import logging

logger = logging.getLogger(__name__)

class HealthState:
    def __init__(self):
        self.start_time = time.time()
        self.last_analysis_duration = None
        self.safe_mode = False

async def handle_client(reader, writer, state: HealthState):
    request = await reader.read(1024)

    body = json.dumps({
        "status": "ok" if not state.safe_mode else "degraded",
        "uptime": int(time.time() - state.start_time),
        "last_analysis_duration": state.last_analysis_duration,
        "safe_mode": state.safe_mode,
    })

    response = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
        f"{body}"
    )

    writer.write(response.encode())
    await writer.drain()
    writer.close()

async def start_health_server(state: HealthState, host="127.0.0.1", port=8080):
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, state),
        host,
        port
    )

    logger.info(f"🌐 Health server listening on {host}:{port}")

    try:
        async with server:
            await server.serve_forever()
    except asyncio.CancelledError:
        logger.info("🌐 Health server stopped")
        server.close()
        await server.wait_closed()
        raise
