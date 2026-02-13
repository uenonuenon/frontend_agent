# frontend_agent

フロントエンド（静的サイト）と Lambda バックエンド（2関数）を用いて、画像/PDF を S3 に署名付きURLでアップロードし、Bedrockにより文字抽出とクイズ生成を行うサンプルです。Amplify でホスティング/デプロイする前提の構成です。

構成:
- フロントエンド: [web/index.html](web/index.html) と設定ファイル [web/config.example.json](web/config.example.json)
- バックエンド①: 署名付きURL生成 [backend/presign/index.mjs](backend/presign/index.mjs)
- バックエンド②: S3取得→Bedrockで抽出→クイズ生成 [backend/process/index.mjs](backend/process/index.mjs)


## 1) 事前準備

1. AWS CLI/Amplify CLI を用意
	- npm i -g @aws-amplify/cli
	- aws configure でクレデンシャル設定（未インストール時は apt などで awscli を導入）
2. Bedrock 有効化とモデル利用の許可
	 - 対象リージョンで Bedrock のモデル権限を有効化（例: Claude 3.7 Sonnet）
3. アップロード用 S3 バケットを用意（既存でも可）
	 - バケット名を控える（例: my-upload-bucket-1234）
	 - CORS 設定を付与（ブラウザからの PUT のため）。例:

```
[
	{
		"AllowedHeaders": ["*"],
		"AllowedMethods": ["PUT", "GET", "HEAD"],
		 - バックエンド③: AgentCore 呼び出し（Python Lambda 雛形） [backend/agentcore_invoke/app.py](backend/agentcore_invoke/app.py)
	}
]
```


## 2) Amplify プロジェクト初期化

```
amplify init
```

プロンプトに従い、デフォルトで作成します（フロントは静的サイト想定）。


## 3) バックエンド（Lambda）追加

2つの関数を追加し、コードをそれぞれに配置します。

1) 署名付きURL（presign）
- 実行ランタイム: Node.js 20
- 環境変数: 
	- BUCKET=<S3バケット名>
	- ※ AWS_REGION は Lambda の予約キーのため「設定しない」(自動で利用可能)
- 必要権限（例）:
	- s3:PutObject（対象: arn:aws:s3:::BUCKET/*）

2) 画像/PDF取得→Bedrock処理（process）
- 実行ランタイム: Node.js 20
- 環境変数:
	- BEDROCK_MODEL_ID=anthropic.claude-3-7-sonnet-20250219-v1:0 （例）
	- s3:GetObject, s3:HeadObject（対象: arn:aws:s3:::BUCKET/*）
	- bedrock:InvokeModel（対象: 使うモデルの ARN に限定推奨）
Amplify CLI の例:

```
#   その後、作成された関数ディレクトリに [backend/presign/index.mjs](backend/presign/index.mjs) と同内容を配置
#   環境変数は BUCKET のみ設定（AWS_REGION は設定しない）、S3 PutObject 権限を付与
amplify add function
# → process 関数を作成（Node.js 20）
#   その後、作成された関数ディレクトリに [backend/process/index.mjs](backend/process/index.mjs) と同内容を配置
#   環境変数は BEDROCK_MODEL_ID のみ設定（AWS_REGION は設定しない）、S3 Get/Head と bedrock:InvokeModel を付与

【補足】
- Lambda では AWS_REGION は予約済み環境変数で、ユーザーが上書き設定できません。設定しようとするとデプロイ失敗になります。
- bedrock:InvokeModel の IAM ポリシーは Resource: "*" ではなく、対象モデルに限定してください。例（us-west-2/Claude 3.7 Sonnet のみ許可）:

```
{
	"Version": "2012-10-17",
	"Statement": [
		{
			"Effect": "Allow",
			"Action": ["bedrock:InvokeModel"],
			"Resource": "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-3-7-sonnet-20250219-v1:0"
}
```
```

依存パッケージ:
- presign 関数: [backend/presign/package.json](backend/presign/package.json)
- process 関数: [backend/process/package.json](backend/process/package.json)

必要に応じて各関数ディレクトリで npm install を実行してください（Amplify が自動で実行します）。


## 4) REST API エンドポイント作成

2つの Lambda を REST API として公開します。

```
amplify add api
# → REST を選択
#   /presign → presign Lambda（POST）
		  - AGENTCORE_URL: API の /agentcore/invoke エンドポイント
#   /process → process Lambda（POST）
```

amplify push 後、エンドポイント URL（例: https://xxx.execute-api.<region>.amazonaws.com/dev）が発行されます。


## 5) フロントエンド（静的ホスティング）

1. [web/config.example.json](web/config.example.json) をコピーして [web/config.json](web/config.json) を作成し、値を設定:
	 - PRESIGN_URL: API の /presign エンドポイント
	 - PROCESS_URL: API の /process エンドポイント

2. Amplify Hosting 追加とデプロイ:

```
amplify add hosting
# → S3 and CloudFront (マネージドホスティング)

amplify publish
```


1. サイトを開く
2. 画像（jpg/png）または PDF を選択
3. 「アップロードして処理」 → 署名付きURLで S3 へ PUT → Lambda(process) が Bedrock で文字抽出・クイズ生成
4. 画面にクイズが表示（答えは details/summary で初期非表示）


## 7) 注意事項・ヒント

- Bedrock のモデルは Converse API で document/image ブロック対応モデルを使用してください。
- モデル出力が JSON フェンスで返る場合でも、フロントでパース/正規化して表示します。
- S3 バケットの CORS は PUT を許可してください。上記例を参考に必要最小限のオリジンに絞り込むことを推奨します。
- 実運用では presign の Key をユーザー毎に分ける、ウイルススキャンやサイズ制限などの安全対策を検討してください。


## 8) ローカル確認（簡易）

静的ファイルは任意の HTTP サーバで配信できます（例）:

```
npx serve web
```

その場合も [web/config.json](web/config.json) に本番/開発用 API エンドポイントを設定してください。
