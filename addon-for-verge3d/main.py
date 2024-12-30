import asyncio
import json
import os

import aiohttp
import websockets
from loguru import logger

HA_URL = "http://supervisor/core"
# 환경 변수에서 Supervisor Token 가져오기
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
HA_WEBSOCKET_URL = "ws://supervisor/core/websocket"

with open("/data/options.json", encoding="utf8") as f:
    options = json.load(f)

EXTERNAL_WEBSOCKET_URL = options.get("external_ws_server_url")

# 모니터링할 엔티티 도메인
MONITORED_DOMAINS = ["light", "media_player", "fan", "vaccum"]
# 타임아웃 설정 (초)
TIMEOUT = options.get("timeout", 30)


async def process_state_change(data):
    """상태 변화 처리 및 외부 서버로 전송."""
    try:
        async with websockets.connect(EXTERNAL_WEBSOCKET_URL) as ext_ws:
            logger.info(f"Sending state change: {data}")
            await ext_ws.send(json.dumps(data))
            response = await ext_ws.recv()
            logger.info(f"External Server Response: {response}")
    except websockets.WebSocketException as e:
        logger.error(f"WebSocket error: {e}")
    except Exception as e:
        logger.error(f"Error processing state change: {e}")


async def get_states(session):
    """Home Assistant 상태 가져오기."""
    url = f"{HA_URL}/api/states"
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with session.get(url, headers=headers, timeout=TIMEOUT) as response:
            if response.status == 200:
                states = await response.json()
                logger.info(f"Successfully fetched {len(states)} states")
                return states
            logger.error(f"Failed to get states. Status: {response.status}")
    except Exception as e:
        logger.exception(f"Error fetching states: {str(e)}")

    return None


async def is_monitored_state(entity_id: str, state: str) -> bool:
    return state in ["on", "off"] and any(
        entity_id.startswith(f"{domain}.") for domain in MONITORED_DOMAINS
    )


async def authenticate_websocket(websocket):
    """WebSocket 인증 처리"""
    auth_message = {"type": "auth", "access_token": SUPERVISOR_TOKEN}
    await websocket.send(json.dumps(auth_message))
    auth_response = await websocket.recv()
    logger.info(f"Auth Response: {auth_response}")


async def subscribe_to_states(websocket):
    """상태 변경 구독"""
    subscribe_message = {
        "id": 1,
        "type": "subscribe_events",
        "event_type": "state_changed",
    }
    await websocket.send(json.dumps(subscribe_message))
    logger.info("Subscribed to state changes")


async def collect_device_states(states: list) -> list:
    """모니터링 대상 디바이스의 상태 수집"""
    device_states = []

    for state in states:
        entity_id = state["entity_id"]
        current_state = state["state"]

        if await is_monitored_state(entity_id, current_state):
            logger.info(f"State change: {entity_id} -> {current_state}")
            device_states.append({"entity_id": entity_id, "state": current_state})

    return device_states


async def handle_state_updates(ha_ws):
    """상태 업데이트 처리"""
    while True:
        await ha_ws.recv()
        async with aiohttp.ClientSession() as session:
            states = await get_states(session)
            if not states:
                continue

            device_states = await collect_device_states(states)
            if device_states:
                await process_state_change(device_states)


async def monitor_states():
    """Home Assistant 상태 변경 모니터링."""
    while True:
        try:
            async with websockets.connect(HA_WEBSOCKET_URL) as ha_ws:
                await authenticate_websocket(ha_ws)
                await subscribe_to_states(ha_ws)
                await handle_state_updates(ha_ws)

        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")
            await asyncio.sleep(5)  # # 재연결 전 대기


async def main():
    """메인 함수."""
    try:
        await monitor_states()
    except asyncio.CancelledError:
        logger.info("Monitoring stopped by user")
    except Exception as e:
        logger.error(f"Unhandled error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
