# AgentCore Invoke Lambda (Python)

この関数はフロントエンドから受け取った入力（`prompt` または 構造化入力）を Bedrock AgentCore ランタイムへ中継します。
現状は `INVOCATION_MODE=mock` でモック応答を返し、SDKによる本番呼び出しは関数内の TODO に従って実装します。

## 受け付ける入力
- `{"prompt":"..."}`
- `{"s3_uri":"s3://...","target":"...","difficulty":"...","num_questions":5}`

## 環境変数
- `AWS_REGION` … 既定 `us-west-2`
- `AGENTCORE_ARN` … 例: `arn:aws:bedrock-agentcore:us-west-2:975050325676:runtime/agentcore_app5-hzJtlH2rgJ`
- `AGENTCORE_QUALIFIER` … 省略時は DEFAULT エンドポイントを使用（任意）
- `INVOCATION_MODE` … `mock` | `sdk`（既定 `mock`）

## デプロイ例（AWS CLI最小）
以下は最小の直接デプロイ例です。IAMロールは Lambda 基本実行ロール + AgentCore 実行権限を適切に付与してください。

```bash
# パッケージ作成
cd backend/agentcore_invoke
zip -r ../agentcore_invoke.zip .

# 例: 作成
aws lambda create-function \
  --function-name agentcore-invoke \
  --runtime python3.12 \
  --handler app.handler \
  --role arn:aws:iam::<ACCOUNT_ID>:role/<LambdaRoleForAgentCoreInvoke> \
  --zip-file fileb://../agentcore_invoke.zip \
  --environment "Variables={AWS_REGION=us-west-2,AGENTCORE_ARN=arn:aws:bedrock-agentcore:us-west-2:975050325676:runtime/agentcore_app5-hzJtlH2rgJ,INVOCATION_MODE=mock}"

# 例: 更新
aws lambda update-function-code \
  --function-name agentcore-invoke \
  --zip-file fileb://../agentcore_invoke.zip

aws lambda update-function-configuration \
  --function-name agentcore-invoke \
  --environment "Variables={AWS_REGION=us-west-2,AGENTCORE_ARN=arn:aws:bedrock-agentcore:us-west-2:975050325676:runtime/agentcore_app5-hzJtlH2rgJ,INVOCATION_MODE=sdk}"
```

## API公開
- API Gateway HTTP API などで `POST /agentcore/invoke` → この Lambda を統合
- CORS はハンドラー側で `access-control-allow-origin: *` を返しています

## SDK呼び出し（実装済み）
`INVOCATION_MODE=sdk` のとき、`boto3.client('bedrock-agentcore').invoke_agent_runtime` を用いて次のように呼び出します。

```python
client = boto3.client('bedrock-agentcore', region_name=AWS_REGION)
resp = client.invoke_agent_runtime(
  agentRuntimeArn=AGENTCORE_ARN,
  runtimeSessionId='<33+ chars>',                   # 閾値を満たすIDを自動生成（または body.session_id を利用）
  payload=json.dumps(payload, ensure_ascii=False),  # 入力はそのまま JSON 文字列で転送
  # qualifier=AGENTCORE_QUALIFIER,                 # 任意
)
data = json.loads(resp['response'].read())
```

セッションIDについて:
- 33文字以上が必須です。本実装ではミリ秒タイムスタンプ+ランダムHEXから十分長いIDを自動生成します。
- 要求ボディに `session_id` が文字列で含まれている場合はそれを優先利用します。

## 実装メモ
- `invoke_agentcore_via_sdk()` に AgentCore 専用の boto3 クライアント/メソッドを実装してください（現在は `NotImplementedError`）。
- 実装時は `AGENTCORE_ARN` を使って対象ランタイムへ JSON をそのまま渡してください。