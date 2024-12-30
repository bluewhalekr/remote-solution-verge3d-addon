import asyncio
import json
import os
from asyncio import Queue
from collections import defaultdict
from datetime import datetime, timedelta

import websockets
from loguru import logger

# 환경 변수에서 Supervisor Token 가져오기
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
HA_WEBSOCKET_URL = "ws://supervisor/core/websocket"

with open("/data/options.json", encoding="utf8") as f:
    options = json.load(f)

EXTERNAL_WEBSOCKET_URL = options.get("external_ws_server_url")

# 모니터링할 엔티티 도메인
MONITORED_DOMAINS = ["light", "switch", "media_player", "fan", "vaccum"]


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


async def monitor_states(queue):
    # 상태 변경 추적을 위한 변수들
    state_changes = defaultdict(list)
    BATCH_WINDOW = timedelta(seconds=0.5)  # 500ms 내의 변경을 하나의 배치로 처리
    last_batch_time = None

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
                    message = await ha_ws.recv()
                    event = json.loads(message)
                    current_time = datetime.now()
                    logger.info(event)
                    if event.get("event", {}).get("event_type") == "state_changed":
                        entity_id = event["event"]["data"]["entity_id"]
                        new_state = event["event"]["data"]["new_state"]

                        if "state" in new_state and new_state["state"] in ["on", "off"]:
                            if any(domain in entity_id for domain in MONITORED_DOMAINS):
                                # 상태 변경 기록
                                state_changes[current_time].append(
                                    (entity_id, new_state)
                                )
                                logger.debug(
                                    f"Added to batch: {entity_id} -> {new_state}"
                                )

                                # 배치 처리 시점 확인
                                if (
                                    last_batch_time is None
                                    or current_time - last_batch_time > BATCH_WINDOW
                                ):
                                    if state_changes:
                                        logger.info(
                                            "====================================="
                                        )
                                        logger.info(
                                            f"Processing batch of {len(state_changes)} state changes:"
                                        )

                                        # 누적된 모든 변경사항 처리
                                        for timestamp, changes in state_changes.items():
                                            for entity_id, state in changes:
                                                logger.info(f"- {entity_id} -> {state}")
                                                await queue.put((entity_id, state))

                                        # 처리 완료된 변경사항 초기화
                                        state_changes.clear()
                                        last_batch_time = current_time

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
