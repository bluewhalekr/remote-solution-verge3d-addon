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


async def process_state_change(entity_id, state):
    """상태 변화 처리 및 외부 서버로 전송."""
    try:
        async with websockets.connect(EXTERNAL_WEBSOCKET_URL) as ext_ws:
            payload = {"entity_id": entity_id, "state": state}
            logger.info(f"Sending state change: {payload}")
            await ext_ws.send(json.dumps(payload))
            response = await ext_ws.recv()
            logger.info(f"External Server Response: {response}")
    except websockets.WebSocketException as e:
        logger.error(f"WebSocket error: {e}")
    except Exception as e:
        logger.error(f"Error processing state change: {e}")


async def get_states(session):
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


async def monitor_states():
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
                    await ha_ws.recv()
                    async with aiohttp.ClientSession() as session:
                        states = await get_states(session)
                        if states:
                            for state in states:
                                entity_id = state["entity_id"]
                                state = state["state"]
                                if state in ["on", "off"] and any(
                                    entity_id.startswith(f"{domain}.")
                                    for domain in MONITORED_DOMAINS
                                ):
                                    # 상태 변경 기록
                                    logger.info(f"State change: {entity_id} -> {state}")
                                    await process_state_change(entity_id, state)

        except websockets.WebSocketException as e:
            logger.error(f"HA WebSocket error: {e}")
            await asyncio.sleep(5)  # 재연결 전 대기
        except Exception as e:
            logger.error(f"Error in monitor_states: {e}")
            await asyncio.sleep(5)  # 재연결 전 대기


async def main():
    """메인 함수."""
    try:
        await monitor_states()
    except asyncio.CancelledError:
        logger.info("프로그램 종료 요청을 받았습니다.")
    except Exception as e:
        logger.error(f"Unhandled error: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("사용자 요청으로 종료 중...")
