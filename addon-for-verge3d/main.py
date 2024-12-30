import asyncio
import json
import os
from asyncio import Queue

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
MONITORED_DOMAINS = ["light", "switch", "media_player", "fan", "vaccum"]
# 타임아웃 설정 (초)
TIMEOUT = options.get("timeout", 30)


async def process_state_changes(queue):
    ext_ws = None
    try:
        ext_ws = await websockets.connect(EXTERNAL_WEBSOCKET_URL)
        while True:
            entity_id, state = await queue.get()
            try:
                payload = {"entity_id": entity_id, "state": state}
                await ext_ws.send(json.dumps(payload))
                response = await ext_ws.recv()
                logger.info(f"External Server Response: {response}")
            except websockets.WebSocketException as e:
                logger.error(f"WebSocket error: {e}")
                # 연결 재시도
                ext_ws = await websockets.connect(EXTERNAL_WEBSOCKET_URL)
            except Exception as e:
                logger.error(f"Error processing state change: {e}")
            finally:
                queue.task_done()
    except Exception as e:
        logger.error(f"Fatal error in process_state_changes: {e}")
    finally:
        if ext_ws and not ext_ws.closed:
            await ext_ws.close()


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


async def monitor_states(queue):
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
                                    await queue.put((entity_id, state))

        except websockets.WebSocketException as e:
            logger.error(f"HA WebSocket error: {e}")
            await asyncio.sleep(5)  # 재연결 전 대기
        except Exception as e:
            logger.error(f"Error in monitor_states: {e}")
            await asyncio.sleep(5)  # 재연결 전 대기


async def main():
    state_queue = Queue()

    # 상태 처리 태스크 시작
    process_task = asyncio.create_task(process_state_changes(state_queue))
    monitor_task = asyncio.create_task(monitor_states(state_queue))

    try:
        # 두 태스크가 완료될 때까지 대기
        await asyncio.gather(process_task, monitor_task)
    except Exception as e:
        logger.error(f"Error in main: {e}")
    finally:
        # 정리 작업
        process_task.cancel()
        monitor_task.cancel()
        try:
            await process_task
            await monitor_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
