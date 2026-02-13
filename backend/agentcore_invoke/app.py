import json
import os
import logging
import secrets
import time
from typing import Any, Dict, Tuple, Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-west-2"))
AGENTCORE_ARN = os.getenv("AGENTCORE_ARN", "")
AGENTCORE_QUALIFIER = os.getenv("AGENTCORE_QUALIFIER", "")  # 省略時は DEFAULT が使われる
INVOCATION_MODE = os.getenv("INVOCATION_MODE", "mock").lower()  # mock | sdk


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "POST,OPTIONS",
            "access-control-allow-headers": "content-type",
            "content-type": "application/json",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def _parse_body(event: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    body_raw = event.get("body") if isinstance(event, dict) else None
    if body_raw and isinstance(body_raw, str):
        try:
            body = json.loads(body_raw)
            return body, "json"
        except Exception:
            pass
    # API GWのlambda-proxyで body が Base64 のケースに対応（今回は未使用）
    return {}, "unknown"


def _validate_payload(p: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(p, dict):
        return False, "payload must be a JSON object"
    if isinstance(p.get("prompt"), str) and p["prompt"].strip():
        return True, "prompt"
    required = ["s3_uri", "target", "difficulty", "num_questions"]
    if all(k in p for k in required):
        return True, "structured"
    return False, "invalid payload"


def _new_session_id(prefix: str = "sess") -> str:
    # 33+ chars: prefix + timestamp base36 + 32 hex = >= 39
    ts36 = format(int(time.time()*1000), 'x')  # hex millis
    rand = secrets.token_hex(16)  # 32 chars
    return f"{prefix}-{ts36}-{rand}"


def invoke_agentcore_via_sdk(payload: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
    """
    TODO: AgentCore の正式な SDK 呼び出しを実装してください。
    - 期待値: `payload` をそのまま AgentCore ランタイムに渡し、戻り値(JSON)を返す
    - 参考: `AGENTCORE_ARN` を使用

    例（疑似コード）:
        import boto3
        client = boto3.client('bedrock-agentcore-runtime', region_name=AWS_REGION)
        resp = client.invoke(runtimeArn=AGENTCORE_ARN, payload=json.dumps(payload).encode('utf-8'))
        return json.loads(resp['body'])
    """
    import boto3

    client = boto3.client('bedrock-agentcore', region_name=AWS_REGION)

    runtime_session_id = session_id or payload.get('session_id') or _new_session_id()

    req = {
        'agentRuntimeArn': AGENTCORE_ARN,
        'runtimeSessionId': runtime_session_id,
        'payload': json.dumps(payload, ensure_ascii=False)
    }
    if AGENTCORE_QUALIFIER:
        req['qualifier'] = AGENTCORE_QUALIFIER

    resp = client.invoke_agent_runtime(**req)
    body_stream = resp.get('response')
    if hasattr(body_stream, 'read'):
        raw = body_stream.read()
        try:
            data = json.loads(raw)
        except Exception:
            # 念のため生文字列で返す
            data = { 'result': raw.decode('utf-8', errors='replace') if isinstance(raw, (bytes, bytearray)) else str(raw) }
    else:
        data = resp

    # 返却は辞書に統一
    return data if isinstance(data, dict) else { 'result': data }


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        body, _ = _parse_body(event)
        ok, kind = _validate_payload(body)
        if not ok:
            return _resp(400, {"error": kind, "usage": {
                "prompt": {"prompt": "任意の文字列"},
                "structured": {"s3_uri": "s3://bucket/key", "target": "高校生", "difficulty": "普通", "num_questions": 5}
            }})

        if INVOCATION_MODE == "mock":
            logger.info("[MOCK] forwarding to AgentCore: %s", body)
            # ダミー応答（UI確認用）
            if kind == "prompt":
                return _resp(200, {"result": f"[MOCK] Echo: {body['prompt']}"})
            else:
                return _resp(200, {"result": f"[MOCK] structured accepted for {body.get('s3_uri')}", "echo": body})

        if INVOCATION_MODE == "sdk":
            if not AGENTCORE_ARN:
                return _resp(500, {"error": "AGENTCORE_ARN is not set"})
            try:
                # 明示セッションIDがあれば使用
                sid = body.get('session_id') if isinstance(body.get('session_id'), str) else None
                result = invoke_agentcore_via_sdk(body, session_id=sid)
                # 期待: AgentCore 側は {"result": "..."} or {"error": "..."}
                return _resp(200, result if isinstance(result, dict) else {"result": result})
            except NotImplementedError as nie:
                logger.exception("AgentCore SDK invocation is not implemented")
                return _resp(500, {"error": str(nie)})
            except Exception as e:
                logger.exception("AgentCore invocation failed")
                return _resp(500, {"error": f"AgentCore invoke failed: {e}"})

        return _resp(500, {"error": f"Unsupported INVOCATION_MODE: {INVOCATION_MODE}"})

    except Exception as e:
        logger.exception("handler failed")
        return _resp(500, {"error": str(e)})
