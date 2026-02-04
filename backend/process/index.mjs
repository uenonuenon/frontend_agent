import { S3Client, GetObjectCommand, HeadObjectCommand } from "@aws-sdk/client-s3";
import { BedrockRuntimeClient, ConverseCommand } from "@aws-sdk/client-bedrock-runtime";

const s3 = new S3Client({ region: process.env.AWS_REGION });
const br = new BedrockRuntimeClient({ region: process.env.AWS_REGION });
const MODEL_ID = process.env.BEDROCK_MODEL_ID; // 例: anthropic.claude-3-7-sonnet-20250219-v1:0 等

export const handler = async (event) => {
  try {
    const body = parseBody(event);
    const { bucket, key } = body;
    if (!bucket || !key) return res(400, { error: "bucket,key が必要です" });
    if (!MODEL_ID) return res(500, { error: "環境変数 BEDROCK_MODEL_ID が未設定です" });

    // S3: メタデータ（ContentType）と本体バイト
    const head = await s3.send(new HeadObjectCommand({ Bucket: bucket, Key: key }));
    const obj  = await s3.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
    const bytes = await obj.Body?.transformToByteArray();
    if (!bytes || !bytes.length) return res(400, { error: "S3から本文を取得できませんでした" });

    // 種別判定（拡張子 or ContentType）
    const lower = key.toLowerCase();
    const ct = String(head.ContentType || "");
    const isPDF = lower.endsWith(".pdf") || ct.includes("pdf");
    const isPNG = lower.endsWith(".png") || ct.includes("png");
    const isJPG = lower.endsWith(".jpg") || lower.endsWith(".jpeg") || ct.includes("jpeg") || ct.includes("jpg");

    // --- Contentブロックを正しい型で構築 ---
    const content = [
      { text: "以下の画像/文書から、日本語本文を段落保持で正確に抽出してください。" }
    ];

    if (isPDF) {
      content.push({
        document: {
          format: "pdf",              // PDF は document ブロック
          name: key.split("/").pop(),
          source: { bytes }           // base64 ではなく bytes を渡す
        }
      });
    } else {
      const fmt = isPNG ? "png" : "jpeg"; // 他に webp/gif なども可
      content.push({
        image: {
          format: fmt,                // 画像は image ブロック
          source: { bytes }
        }
      });
    }

    // 1) 抽出
    const ocrResp = await br.send(new ConverseCommand({
      modelId: MODEL_ID,
      messages: [{ role: "user", content }],
      inferenceConfig: { maxTokens: 1800, temperature: 0.0 }
    }));

    const extracted = ocrResp.output?.message?.content
      ?.map(b => b?.text || "")
      .join("\n")
      .trim();

    if (!extracted) {
      console.warn("No text extracted. bytes=", bytes.length, "ct=", ct);
      return res(200, { extracted: "", note: "テキスト抽出に失敗しました（画像品質やモデル設定を確認）" });
    }

    // 2) 問題生成（抽出テキストから）
    const quizPrompt = [{
      role: "user",
      content: [{
        text:
`以下の本文から日本語の小テストを5問（四択3＋穴埋め2）で作成してください。
各問に 正答・解説・根拠（本文の該当行） を含め、全体を JSON で返してください。
出力は {"questions": Question[]} の形で返してください。
Question: {"type": "mcq|cloze", "question": string, "choices"?: string[], "answer": string, "explanation": string, "sourceText": string}
本文:
${extracted}`
      }]
    }];

    const quizResp = await br.send(new ConverseCommand({
      modelId: MODEL_ID,
      messages: quizPrompt,
      inferenceConfig: { maxTokens: 1500, temperature: 0.2 }
    }));

    const quizText = quizResp.output?.message?.content
      ?.map(b => b?.text || "")
      .join("\n")
      .trim();

    return res(200, { extractedPreview: extracted.slice(0, 400), quiz: safeJson(quizText) });

  } catch (e) {
    console.error(e);
    return res(500, { error: String(e?.message || e) });
  }
};

function res(code, body){
  return {
    statusCode: code,
    headers: { "access-control-allow-origin": "*", "content-type": "application/json" },
    body: JSON.stringify(body)
  };
}

function parseBody(event){
  if (event?.body) {
    try { return JSON.parse(event.body); } catch {}
  }
  return {};
}

function safeJson(s) { try { return JSON.parse(s); } catch { return s; } }
