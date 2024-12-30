import asyncio
import json
import os

import websockets
from loguru import logger

# 환경 변수에서 Supervisor Token 가져오기
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
HA_WEBSOCKET_URL = "ws://supervisor/core/websocket"

with open("/data/options.json", encoding="utf8") as f:
    options = json.load(f)

EXTERNAL_WEBSOCKET_URL = options.get("external_ws_server_url")


# 모니터링할 엔티티 도메인
MONITORED_DOMAINS = ["light", "switch", "media_player"]


async def sync_device_states():
    async with websockets.connect(HA_WEBSOCKET_URL) as ha_ws:
        # Home Assistant 인증
        await ha_ws.send(json.dumps({"type": "auth", "access_token": SUPERVISOR_TOKEN}))
        auth_response = await ha_ws.recv()
        logger.info(f"Auth Response:  {auth_response}")

        # 상태 변경 이벤트 구독
        await ha_ws.send(
            json.dumps(
                {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
            )
        )

        # 상태 변경 이벤트 처리
        while True:
            message = await ha_ws.recv()
            event = json.loads(message)

            if event.get("event", {}).get("event_type") == "state_changed":
                entity_id = event["event"]["data"]["entity_id"]
                new_state = event["event"]["data"]["new_state"]

                # 모니터링 대상 필터링
                if any(domain in entity_id for domain in MONITORED_DOMAINS):
                    logger.info("=====================================")
                    logger.info(f"State Changed: {entity_id} -> {new_state}")
                    # 외부 웹소켓 서버로 상태 동기화
                    await send_to_external_server(entity_id, new_state)


async def send_to_external_server(entity_id, state):
    async with websockets.connect(EXTERNAL_WEBSOCKET_URL) as ext_ws:
        payload = {"entity_id": entity_id, "state": state}
        await ext_ws.send(json.dumps(payload))
        response = await ext_ws.recv()
        logger.info(f"External Server Response: {response}")


# 비동기 함수 실행
asyncio.run(sync_device_states())
