# Amplify Lambda (Python 3.12)
# Handler: index.handler

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
AGENTCORE_QUALIFIER = os.getenv("AGENTCORE_QUALIFIER", "")
INVOCATION_MODE = os.getenv("INVOCATION_MODE", "sdk").lower()  # mock | sdk
JOBS_BUCKET = os.getenv("JOBS_BUCKET", "")
JOBS_PREFIX = os.getenv("JOBS_PREFIX", "jobs/")


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
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
    ts36 = format(int(time.time()*1000), 'x')
    rand = secrets.token_hex(16)
    return f"{prefix}-{ts36}-{rand}"


def _job_key(job_id: str) -> str:
    p = JOBS_PREFIX or "jobs/"
    if not p.endswith('/'):
        p += '/'
    return f"{p}{job_id}.json"


def _s3_put_json(bucket: str, key: str, data: Dict[str, Any]):
    import boto3
    s3 = boto3.client('s3', region_name=AWS_REGION)
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(data, ensure_ascii=False).encode('utf-8'), ContentType='application/json')


def _s3_get_json(bucket: str, key: str) -> Optional[Dict[str, Any]]:
    import boto3
    s3 = boto3.client('s3', region_name=AWS_REGION)
    try:
        r = s3.get_object(Bucket=bucket, Key=key)
        b = r.get('Body')
        if hasattr(b, 'read'):
            raw = b.read()
            return json.loads(raw)
    except Exception:
        return None
    return None


def invoke_agentcore_via_sdk(payload: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
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
            data = { 'result': raw.decode('utf-8', errors='replace') if isinstance(raw, (bytes, bytearray)) else str(raw) }
    else:
        data = resp
    return data if isinstance(data, dict) else { 'result': data }


def handler(event, context):
    # CORS preflight
    if isinstance(event, dict) and event.get("httpMethod") == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        # Worker invocation
        if isinstance(event, dict) and event.get("type") == "worker":
            job_id = event.get("jobId")
            if not job_id or not JOBS_BUCKET:
                return {"ok": False}
            job_key = _job_key(job_id)
            job = _s3_get_json(JOBS_BUCKET, job_key) or {"status": "PENDING"}
            try:
                payload = job.get("payload") or {}
                sid = payload.get('session_id') if isinstance(payload.get('session_id'), str) else None
                if INVOCATION_MODE == "mock":
                    result = {"result": f"[MOCK] Echo: {payload.get('prompt') or payload.get('s3_uri','')}"}
                else:
                    result = invoke_agentcore_via_sdk(payload, session_id=sid)
                job.update({
                    "status": "SUCCEEDED",
                    "finishedAt": int(time.time()*1000),
                    "result": result
                })
            except Exception as e:
                logger.exception("worker failed")
                job.update({
                    "status": "FAILED",
                    "finishedAt": int(time.time()*1000),
                    "error": str(e)
                })
            _s3_put_json(JOBS_BUCKET, job_key, job)
            return {"ok": True}

        # API Gateway invocation
        method = (event.get("httpMethod") or "").upper() if isinstance(event, dict) else "POST"

        if method == "GET":
            # GET /agentcore?jobId=...
            qs = event.get('queryStringParameters') or {}
            job_id = (qs.get('jobId') if isinstance(qs, dict) else None) or ''
            if not job_id:
                return _resp(400, {"error": "jobId is required"})
            if not JOBS_BUCKET:
                return _resp(500, {"error": "JOBS_BUCKET is not set"})
            job = _s3_get_json(JOBS_BUCKET, _job_key(job_id))
            if not job:
                return _resp(404, {"error": "job not found"})
            return _resp(200, job)

        # POST: start job
        body, _ = _parse_body(event)
        ok, kind = _validate_payload(body)
        if not ok:
            return _resp(400, {"error": kind, "usage": {
                "prompt": {"prompt": "任意の文字列"},
                "structured": {"s3_uri": "s3://bucket/key", "target": "高校生", "difficulty": "普通", "num_questions": 5}
            }})

        if not JOBS_BUCKET:
            return _resp(500, {"error": "JOBS_BUCKET is not set"})

        job_id = _new_session_id(prefix="job")
        job_doc = {
            "jobId": job_id,
            "status": "PENDING",
            "createdAt": int(time.time()*1000),
            "payload": body
        }
        _s3_put_json(JOBS_BUCKET, _job_key(job_id), job_doc)

        # async self invoke
        import boto3
        lam = boto3.client('lambda', region_name=AWS_REGION)
        lam.invoke(
            FunctionName=os.getenv('AWS_LAMBDA_FUNCTION_NAME'),
            InvocationType='Event',
            Payload=json.dumps({"type": "worker", "jobId": job_id}).encode('utf-8')
        )

        return _resp(202, {"jobId": job_id, "status": "PENDING"})

    except Exception as e:
        logger.exception("handler failed")
        return _resp(500, {"error": str(e)})
