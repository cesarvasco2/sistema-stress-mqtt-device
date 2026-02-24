from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from amqtt.broker import Broker
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

MQTT_HOST = os.getenv("MQTT_HOST", "0.0.0.0")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "admin")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "admin123")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeviceStats:
    device_id: str
    connected: bool = False
    last_seen: str | None = None
    packets_sent: int = 0
    packets_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    last_topic: str | None = None
    last_payload: str | None = None


@dataclass
class CommandDef:
    id: str
    name: str
    device_id: str
    topic: str
    payload: str
    qos: int = 0
    retained: bool = False
    trigger_type: str = "manual"
    interval_seconds: int | None = None
    condition_topic: str | None = None
    condition_operator: str | None = None
    condition_value: str | None = None
    enabled: bool = True
    created_at: str = field(default_factory=utc_now)


class CommandCreate(BaseModel):
    id: str = Field(..., description="unique command id")
    name: str
    device_id: str
    topic: str
    payload: str
    qos: int = Field(0, ge=0, le=2)
    retained: bool = False
    trigger_type: str = Field("manual", pattern="^(manual|interval|condition)$")
    interval_seconds: int | None = Field(default=None, ge=1)
    condition_topic: str | None = None
    condition_operator: str | None = Field(default=None, pattern="^(==|!=|>|<|>=|<=|contains)$")
    condition_value: str | None = None
    enabled: bool = True


class PublishInput(BaseModel):
    device_id: str
    topic: str
    payload: str
    qos: int = Field(0, ge=0, le=2)
    retain: bool = False


class DeviceRegistry:
    def __init__(self) -> None:
        self.devices: dict[str, DeviceStats] = {}
        self.commands: dict[str, CommandDef] = {}
        self.subscriptions: set[str] = set()
        self.lock = asyncio.Lock()

    async def ensure_device(self, device_id: str) -> DeviceStats:
        if device_id not in self.devices:
            self.devices[device_id] = DeviceStats(device_id=device_id)
        return self.devices[device_id]

    async def get_snapshot(self) -> dict[str, Any]:
        async with self.lock:
            return {
                "devices": [asdict(item) for item in self.devices.values()],
                "commands": [asdict(item) for item in self.commands.values()],
                "subscriptions": sorted(list(self.subscriptions)),
            }


class WsHub:
    def __init__(self) -> None:
        self.connections: list[WebSocket] = []
        self.lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.connections.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self.lock:
            if ws in self.connections:
                self.connections.remove(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self.lock:
            stale: list[WebSocket] = []
            for ws in self.connections:
                try:
                    await ws.send_text(json.dumps(payload, ensure_ascii=False))
                except Exception:
                    stale.append(ws)
            for ws in stale:
                self.connections.remove(ws)


registry = DeviceRegistry()
hub = WsHub()
broker: Broker | None = None
broker_task: asyncio.Task | None = None
scheduler_task: asyncio.Task | None = None
subscriber_task: asyncio.Task | None = None


async def write_password_file() -> Path:
    pwd = DATA_DIR / "passwd"
    pwd.write_text(f"{MQTT_USER}:{MQTT_PASSWORD}\n", encoding="utf-8")
    return pwd


async def create_broker() -> Broker:
    passwd_file = await write_password_file()
    config = {
        "listeners": {"default": {"type": "tcp", "bind": f"{MQTT_HOST}:{MQTT_PORT}"}},
        "sys_interval": 10,
        "topic-check": {"enabled": False},
        "auth": {"allow-anonymous": False, "password-file": str(passwd_file)},
    }
    return Broker(config)


async def mqtt_publish(topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
    from amqtt.client import MQTTClient

    client = MQTTClient()
    uri = f"mqtt://{MQTT_USER}:{MQTT_PASSWORD}@127.0.0.1:{MQTT_PORT}/"
    await client.connect(uri)
    await client.publish(topic, payload.encode("utf-8"), qos=qos, retain=retain)
    await client.disconnect()


async def metrics_subscriber() -> None:
    from amqtt.client import MQTTClient

    uri = f"mqtt://{MQTT_USER}:{MQTT_PASSWORD}@127.0.0.1:{MQTT_PORT}/"
    while True:
        client = MQTTClient()
        try:
            await client.connect(uri)
            await client.subscribe([("#", 0)])
            while True:
                message = await client.deliver_message()
                packet = message.publish_packet
                topic = packet.variable_header.topic_name
                payload_bytes = bytes(packet.payload.data)
                payload = payload_bytes.decode("utf-8", errors="replace")
                segments = topic.split("/")
                device_id = segments[1] if len(segments) > 1 else "unknown"
                async with registry.lock:
                    device = await registry.ensure_device(device_id)
                    device.connected = True
                    device.last_seen = utc_now()
                    device.last_topic = topic
                    device.last_payload = payload
                    device.packets_received += 1
                    device.bytes_received += len(payload_bytes)
                await hub.broadcast({"type": "telemetry", "device_id": device_id, "topic": topic, "payload": payload})
        except asyncio.CancelledError:
            break
        except Exception as exc:
            await hub.broadcast({"type": "warning", "message": f"Subscriber reconectando: {exc}"})
            await asyncio.sleep(2)


def evaluate_condition(operator: str, expected: str, actual: str) -> bool:
    if operator == "contains":
        return expected in actual

    try:
        a_num = float(actual)
        e_num = float(expected)
        mapping = {
            "==": a_num == e_num,
            "!=": a_num != e_num,
            ">": a_num > e_num,
            "<": a_num < e_num,
            ">=": a_num >= e_num,
            "<=": a_num <= e_num,
        }
        return mapping[operator]
    except Exception:
        mapping = {
            "==": actual == expected,
            "!=": actual != expected,
            ">": actual > expected,
            "<": actual < expected,
            ">=": actual >= expected,
            "<=": actual <= expected,
        }
        return mapping[operator]


async def command_scheduler() -> None:
    last_run: dict[str, float] = {}
    while True:
        await asyncio.sleep(1)
        snapshot = await registry.get_snapshot()
        now_ts = datetime.now(timezone.utc).timestamp()
        for command in snapshot["commands"]:
            cmd = CommandDef(**command)
            if not cmd.enabled or cmd.trigger_type == "manual":
                continue

            if cmd.trigger_type == "interval" and cmd.interval_seconds:
                prev = last_run.get(cmd.id, 0.0)
                if now_ts - prev >= cmd.interval_seconds:
                    await mqtt_publish(cmd.topic, cmd.payload, qos=cmd.qos, retain=cmd.retained)
                    async with registry.lock:
                        dev = await registry.ensure_device(cmd.device_id)
                        dev.packets_sent += 1
                        dev.bytes_sent += len(cmd.payload.encode("utf-8"))
                        dev.last_seen = utc_now()
                    last_run[cmd.id] = now_ts
                    await hub.broadcast({"type": "command_fired", "command_id": cmd.id})

            if cmd.trigger_type == "condition" and cmd.condition_topic and cmd.condition_operator and cmd.condition_value is not None:
                async with registry.lock:
                    dev = registry.devices.get(cmd.device_id)
                    if not dev or dev.last_topic != cmd.condition_topic or dev.last_payload is None:
                        continue
                    if evaluate_condition(cmd.condition_operator, cmd.condition_value, dev.last_payload):
                        await mqtt_publish(cmd.topic, cmd.payload, qos=cmd.qos, retain=cmd.retained)
                        dev.packets_sent += 1
                        dev.bytes_sent += len(cmd.payload.encode("utf-8"))
                        dev.last_seen = utc_now()
                        await hub.broadcast({"type": "command_fired", "command_id": cmd.id, "reason": "condition"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    global broker, broker_task, scheduler_task, subscriber_task
    broker = await create_broker()
    broker_task = asyncio.create_task(broker.start())
    await asyncio.sleep(0.6)
    scheduler_task = asyncio.create_task(command_scheduler())
    subscriber_task = asyncio.create_task(metrics_subscriber())
    yield
    for task in [scheduler_task, subscriber_task]:
        if task:
            task.cancel()
    if broker:
        await broker.shutdown()
    if broker_task:
        broker_task.cancel()


app = FastAPI(title="MQTT Stress Platform", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "mqtt_port": MQTT_PORT, "mqtt_user": MQTT_USER})


@app.get("/api/state")
async def get_state():
    return await registry.get_snapshot()


@app.post("/api/subscriptions/{topic:path}")
async def add_subscription(topic: str):
    async with registry.lock:
        registry.subscriptions.add(topic)
    await hub.broadcast({"type": "subscription_added", "topic": topic})
    return {"ok": True, "topic": topic}


@app.post("/api/publish")
async def manual_publish(data: PublishInput):
    await mqtt_publish(data.topic, data.payload, qos=data.qos, retain=data.retain)
    async with registry.lock:
        dev = await registry.ensure_device(data.device_id)
        dev.packets_sent += 1
        dev.bytes_sent += len(data.payload.encode("utf-8"))
        dev.last_seen = utc_now()
    await hub.broadcast({"type": "published", "topic": data.topic, "payload": data.payload})
    return {"ok": True}


@app.post("/api/commands")
async def create_command(data: CommandCreate):
    cmd = CommandDef(**data.model_dump())
    async with registry.lock:
        if cmd.id in registry.commands:
            raise HTTPException(status_code=409, detail="Command ID already exists")
        registry.commands[cmd.id] = cmd
    await hub.broadcast({"type": "command_created", "command": asdict(cmd)})
    return {"ok": True, "command": asdict(cmd)}


@app.post("/api/commands/{command_id}/execute")
async def execute_command(command_id: str):
    async with registry.lock:
        cmd = registry.commands.get(command_id)
        if not cmd:
            raise HTTPException(status_code=404, detail="Command not found")
    await mqtt_publish(cmd.topic, cmd.payload, qos=cmd.qos, retain=cmd.retained)
    async with registry.lock:
        dev = await registry.ensure_device(cmd.device_id)
        dev.packets_sent += 1
        dev.bytes_sent += len(cmd.payload.encode("utf-8"))
        dev.last_seen = utc_now()
    await hub.broadcast({"type": "command_fired", "command_id": command_id, "reason": "manual"})
    return {"ok": True}


@app.websocket("/ws")
async def ws_state(ws: WebSocket):
    await hub.connect(ws)
    try:
        await ws.send_text(json.dumps({"type": "state", **(await registry.get_snapshot())}, ensure_ascii=False))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
