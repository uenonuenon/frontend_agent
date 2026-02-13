import json
import os
import base64
from io import BytesIO

import boto3
from botocore.exceptions import ClientError

# AWS clients
s3_client = boto3.client('s3', region_name=os.environ.get('AWS_REGION'))
br_client = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION'))
agentcore_client = boto3.client('bedrock-agentcore', region_name=os.environ.get('AWS_REGION'))

# Config
MODEL_ID = os.environ.get('BEDROCK_MODEL_ID')
AGENTCORE_RUNTIME_ARN = os.environ.get('AGENTCORE_RUNTIME_ARN', 
    'arn:aws:bedrock-agentcore:us-west-2:975050325676:runtime/agentcore_app5-hzJtlH2rgJ')


def lambda_handler(event, context):
    try:
        body = parse_body(event)
        bucket = body.get('bucket')
        key = body.get('key')
        
        print(f'Received bucket={bucket}, key={key}')
        
        if not bucket or not key:
            return res(400, {'error': 'bucket, key が必要です'})
        
        if not MODEL_ID:
            return res(500, {'error': '環境変数 BEDROCK_MODEL_ID が未設定です'})
        
        # S3: メタデータと本体バイト取得
        try:
            head = s3_client.head_object(Bucket=bucket, Key=key)
            obj = s3_client.get_object(Bucket=bucket, Key=key)
            image_bytes = obj['Body'].read()
            print(f'S3 object retrieved: {len(image_bytes)} bytes')
        except ClientError as e:
            print(f'S3 error: {str(e)}')
            return res(400, {'error': f'S3から本文を取得できませんでした: {str(e)}'})
        
        if not image_bytes:
            return res(400, {'error': 'S3から本文を取得できませんでした'})
        
        # ファイル種別判定
        lower_key = key.lower()
        content_type = head.get('ContentType', '')
        is_pdf = lower_key.endswith('.pdf') or 'pdf' in content_type
        is_png = lower_key.endswith('.png') or 'png' in content_type
        is_jpg = any(lower_key.endswith(ext) for ext in ['.jpg', '.jpeg']) or any(t in content_type for t in ['jpeg', 'jpg'])
        
        # 1) テキスト抽出
        content = [
            {
                'text': '以下の画像/文書から、日本語本文を段落保持で正確に抽出してください。'
            }
        ]
        
        if is_pdf:
            content.append({
                'document': {
                    'format': 'pdf',
                    'name': key.split('/')[-1],
                    'source': {
                        'bytes': image_bytes
                    }
                }
            })
        else:
            fmt = 'png' if is_png else 'jpeg'
            content.append({
                'image': {
                    'format': fmt,
                    'source': {
                        'bytes': image_bytes
                    }
                }
            })
        
        # Converse API で抽出
        ocr_resp = br_client.converse(
            modelId=MODEL_ID,
            messages=[{
                'role': 'user',
                'content': content
            }],
            inferenceConfig={
                'maxTokens': 1800,
                'temperature': 0.0
            }
        )
        
        print(f'Bedrock response keys: {ocr_resp.keys()}')
        print(f'Bedrock output: {ocr_resp.get("output")}')
        
        extracted = ''
        if 'output' in ocr_resp and 'message' in ocr_resp['output'] and 'content' in ocr_resp['output']['message']:
            for block in ocr_resp['output']['message']['content']:
                print(f'Block keys: {block.keys()}')
                if 'text' in block:  # type キーではなく text キーで判定
                    extracted += block.get('text', '')
        
        extracted = extracted.strip()
        print(f'Extracted text length: {len(extracted)}')
        
        if not extracted:
            return res(200, {
                'extracted': '',
                'note': 'テキスト抽出に失敗しました（画像品質やモデル設定を確認）'
            })
        
        # 2) AgentCore 経由での問題生成（構造化パラメータがあれば）
        target = body.get('target')
        difficulty = body.get('difficulty')
        num_questions = body.get('num_questions')
        
        if AGENTCORE_RUNTIME_ARN and target and difficulty and (num_questions is not None):
            try:
                import time
                import random
                
                # セッション ID は33文字以上が必須
                session_id = f"session-{int(time.time()*1000)}-{random.randint(1000000000, 9999999999)}"
                print(f'AgentCore session_id length: {len(session_id)}')
                
                agent_payload = {
                    's3_uri': f's3://{bucket}/{key}',
                    'target': target,
                    'difficulty': difficulty,
                    'num_questions': num_questions
                }
                
                print(f'Calling AgentCore with session_id={session_id}')
                agent_resp = agentcore_client.invoke_agent_runtime(
                    agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
                    runtimeSessionId=session_id,
                    payload=json.dumps(agent_payload)
                )
                
                # レスポンス本体を読む
                agent_result = ''
                if 'response' in agent_resp:
                    response_body = agent_resp['response']
                    if hasattr(response_body, 'read'):
                        agent_result = response_body.read().decode('utf-8')
                    elif isinstance(response_body, bytes):
                        agent_result = response_body.decode('utf-8')
                    elif isinstance(response_body, str):
                        agent_result = response_body
                
                print(f'AgentCore response received: {len(agent_result)} bytes')
                
                # JSON として解析を試みる
                try:
                    parsed_result = json.loads(agent_result)
                except:
                    parsed_result = agent_result
                
                return res(200, {
                    'extractedPreview': extracted[:400],
                    'quiz': parsed_result,
                    'source': 'agentcore'
                })
            except Exception as e:
                print(f'AgentCore invocation failed, falling back to local generation: {str(e)}')
        
        # Fallback: ローカル Bedrock による問題生成
        quiz_prompt = f'''以下の本文から日本語の小テストを5問（四択3＋穴埋め2）で作成してください。
各問に 正答・解説・根拠（本文の該当行） を含め、全体を JSON で返してください。
出力は {{"questions": Question[]}} の形で返してください。
Question: {{"type": "mcq|cloze", "question": string, "choices"?: string[], "answer": string, "explanation": string, "sourceText": string}}
本文:
{extracted}'''
        
        quiz_resp = br_client.converse(
            modelId=MODEL_ID,
            messages=[{
                'role': 'user',
                'content': [{
                    'text': quiz_prompt
                }]
            }],
            inferenceConfig={
                'maxTokens': 1500,
                'temperature': 0.2
            }
        )
        
        quiz_text = ''
        if 'output' in quiz_resp and 'message' in quiz_resp['output'] and 'content' in quiz_resp['output']['message']:
            for block in quiz_resp['output']['message']['content']:
                if 'text' in block:  # type キーではなく text キーで判定
                    quiz_text += block.get('text', '')
        
        quiz_text = quiz_text.strip()
        
        # JSON として安全に解析
        quiz_result = safe_json(quiz_text)
        
        return res(200, {
            'extractedPreview': extracted[:400],
            'quiz': quiz_result
        })
    
    except Exception as e:
        print(f'Error: {str(e)}')
        return res(500, {'error': str(e)})


def res(code, body):
    """HTTP レスポンスを構築"""
    return {
        'statusCode': code,
        'headers': {
            'access-control-allow-origin': '*',
            'content-type': 'application/json'
        },
        'body': json.dumps(body, ensure_ascii=False)
    }


def parse_body(event):
    """Event body を JSON として解析"""
    if isinstance(event, dict) and 'body' in event:
        try:
            return json.loads(event['body'])
        except:
            pass
    return event if isinstance(event, dict) else {}


def safe_json(s):
    """文字列を安全に JSON で解析"""
    try:
        return json.loads(s)
    except:
        return s
