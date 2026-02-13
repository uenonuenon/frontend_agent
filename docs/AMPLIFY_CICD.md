# Amplify API & CI/CD セットアップ

本プロジェクトは Amplify を用いて API（API Gateway + Lambda）とホスティングを管理します。AgentCore 連携のために以下を追加済みです。

- 新Lambda（Python3.12）: amplify/backend/function/tdx2025dagentcoreinvoke
- APIルート: /agentcore/invoke → tdx2025dagentcoreinvoke
- GitHub Actions: .github/workflows/amplify-deploy.yml

## 1. 事前準備
- AWSアカウントの認証情報（Programmatic Access）
- Amplify App（team-provider-info.json の AmplifyAppId を参照）
- Amplify CLI v14 以上

## 2. ローカルからの反映
```bash
amplify status --details
amplify push --yes
```

初回は以下のパラメータ入力が求められます（非対話で渡す例）:

```bash
amplify push --yes -p '{
  "function": {
    "tdx2025dagentcoreinvoke": {
      "parameters": {
        "agentcoreArn": "arn:aws:bedrock-agentcore:us-west-2:975050325676:runtime/agentcore_app5-hzJtlH2rgJ",
        "agentcoreQualifier": "",
        "invocationMode": "sdk"
      }
    }
  }
}'
```

tdx2025dagentcoreinvoke の環境変数（Amplify Parameter）:
- agentcoreArn → AGENTCORE_ARN（必須）
- agentcoreQualifier → AGENTCORE_QUALIFIER（任意）
- invocationMode → INVOCATION_MODE（sdk 推奨、mock も可）

## 3. CI/CD（GitHub Actions）
ワークフロー: .github/workflows/amplify-deploy.yml

必要なGitHub Secrets:
- AWS_ACCESS_KEY_ID
- AWS_SECRET_ACCESS_KEY
- AWS_REGION（例: us-west-2）
- AMPLIFY_APP_ID（team-provider-info.json の AmplifyAppId）
- AMPLIFY_ENV（例: dev）

ブランチ: main, 20260212 への push 時に `amplify pull` → `amplify push` を実行します。

## 4. フロント設定
web/config.json に `AGENTCORE_URL` を追加し、APIの `/agentcore/invoke` エンドポイントを設定してください。

```json
{
  "PRESIGN_URL": "https://<api-id>.execute-api.us-west-2.amazonaws.com/dev/presign",
  "PROCESS_URL": "https://<api-id>.execute-api.us-west-2.amazonaws.com/dev/process",
  "AGENTCORE_URL": "https://<api-id>.execute-api.us-west-2.amazonaws.com/dev/agentcore/invoke"
}
```

## 5. 権限
`amplify/backend/function/tdx2025dagentcoreinvoke/custom-policies.json` では簡易的に `bedrock-agentcore:InvokeAgentRuntime` を `Resource: "*"` で許可しています。運用では対象リソースへスコープを絞ってください。