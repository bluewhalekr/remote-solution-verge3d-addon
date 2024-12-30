import asyncio
import json
import os

import websockets
from loguru import logger

# Home Assistant Core API URL 및 WebSocket URL
HA_URL = "http://supervisor/core"
HA_WEBSOCKET_URL = "ws://supervisor/core/websocket"

# 환경 변수에서 Supervisor Token 가져오기
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")

# 애드온 설정 파일에서 옵션 가져오기
with open("/data/options.json", encoding="utf8") as f:
    options = json.load(f)

EXTERNAL_WEBSOCKET_PORT = options.get("websocket_port", 9765)
MONITORED_DOMAINS = ["light", "media_player", "fan", "vacuum"]
TIMEOUT = options.get("timeout", 30)

connected_clients = set()  # 연결된 웹소켓 클라이언트들


async def process_state_change(data):
    """상태 변화 처리 및 연결된 클라이언트에 전송."""
    if not connected_clients:
        logger.info("No connected clients to notify.")
        return

    message = json.dumps(data)
    logger.info(f"Broadcasting state change to {len(connected_clients)} clients.")

    disconnected_clients = []
    for client in connected_clients:
        try:
            await client.send(message)
        except websockets.WebSocketException as e:
            logger.error(f"WebSocket error with client {client.remote_address}: {e}")
            disconnected_clients.append(client)

    # 연결이 끊긴 클라이언트 제거
    for client in disconnected_clients:
        connected_clients.remove(client)


async def get_states(session):
    """Home Assistant 상태 정보 가져오기."""
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


async def monitor_home_assistant():
    """Home Assistant WebSocket 이벤트 모니터링."""
    while True:
        try:
            async with websockets.connect(HA_WEBSOCKET_URL) as ha_ws:
                # Home Assistant 인증
                await ha_ws.send(
                    json.dumps({"type": "auth", "access_token": SUPERVISOR_TOKEN})
                )
                auth_response = await ha_ws.recv()
                logger.info(f"Auth Response: {auth_response}")

                # 상태 변경 이벤트 구독
                await ha_ws.send(
                    json.dumps(
                        {
                            "id": 1,
                            "type": "subscribe_events",
                            "event_type": "state_changed",
                        }
                    )
                )

                # 상태 변경 이벤트 처리
                while True:
                    event_message = await ha_ws.recv()
                    event_data = json.loads(event_message)

                    if event_data.get("event", {}).get("data", {}).get("entity_id"):
                        entity_id = event_data["event"]["data"]["entity_id"]
                        state = event_data["event"]["data"]["new_state"]["state"]
                        if state in ["on", "off"] and any(
                            entity_id.startswith(f"{domain}.")
                            for domain in MONITORED_DOMAINS
                        ):
                            logger.info(
                                f"State change detected: {entity_id} -> {state}"
                            )
                            await process_state_change(
                                {"entity_id": entity_id, "state": state}
                            )
        except websockets.WebSocketException as e:
            logger.error(f"HA WebSocket error: {e}")
            await asyncio.sleep(5)  # 재연결 전 대기
        except Exception as e:
            logger.error(f"Error in monitor_home_assistant: {e}")
            await asyncio.sleep(5)  # 재연결 전 대기


async def websocket_server(websocket, path):
    """외부 클라이언트 웹소켓 연결 처리."""
    logger.info(f"Client connected: {websocket.remote_address}")
    connected_clients.add(websocket)

    try:
        async for message in websocket:
            logger.info(f"Received message from {websocket.remote_address}: {message}")
            # Echo 메시지를 클라이언트로 전송
            await websocket.send(f"Echo: {message}")
    except websockets.WebSocketException as e:
        logger.info(f"Client disconnected: {websocket.remote_address} ({e})")
    finally:
        connected_clients.remove(websocket)


async def start_server():
    """웹소켓 서버와 Home Assistant 모니터링 동시 시작."""
    server = await websockets.serve(
        websocket_server, "0.0.0.0", EXTERNAL_WEBSOCKET_PORT
    )
    logger.info(f"WebSocket server started on ws://0.0.0.0:{EXTERNAL_WEBSOCKET_PORT}")

    await asyncio.gather(monitor_home_assistant(), server.wait_closed())


if __name__ == "__main__":
    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        logger.info("서버 종료 중...")
