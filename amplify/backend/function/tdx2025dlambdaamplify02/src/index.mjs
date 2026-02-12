import { S3Client, GetObjectCommand, HeadObjectCommand } from "@aws-sdk/client-s3";
import { TextractClient, DetectDocumentTextCommand } from "@aws-sdk/client-textract";
import { BedrockRuntimeClient, ConverseCommand } from "@aws-sdk/client-bedrock-runtime";

const s3 = new S3Client({ region: process.env.AWS_REGION });
const br = new BedrockRuntimeClient({ region: process.env.AWS_REGION });
const tx = new TextractClient({ region: process.env.AWS_REGION });
const MODEL_ID = process.env.BEDROCK_MODEL_ID; // 例: us.anthropic.claude-3-7-sonnet-20250219-v1:0 等（指定をそのまま使用）
const FALLBACK_MODEL_ID = "amazon.nova-micro-v1:0"; // AWS first-party 多モーダル（オンデマンド利用想定）

export const handler = async (event) => {
  try {
    const body = parseBody(event);
    // 簡易ヘルスチェック: Bedrock に最小テキストで疎通確認
    if (body?.action === "check") {
      const id = MODEL_ID;
      try {
        const resp = await br.send(new ConverseCommand({
          modelId: id,
          messages: [{ role: "user", content: [{ text: "ping" }] }],
          inferenceConfig: { maxTokens: 8, temperature: 0 }
        }));
        const out = resp.output?.message?.content?.map(b => b?.text || "").join("\n").trim();
        return res(200, { ok: true, bedrockModelId: id, reply: out });
      } catch (e) {
        return res(500, { ok: false, bedrockModelId: id, error: String(e?.message || e) });
      }
    }

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

    if (!isPDF && !(isPNG || isJPG)) {
      return res(400, { error: "対応形式は PDF/PNG/JPEG のみです" });
    }

    if (!isPDF && bytes.length < 2048) {
      return res(400, { error: "画像が小さすぎます（最低 2KB 程度を推奨）" });
    }

    // --- Contentブロックを正しい型で構築 ---
    const content = [
      { text: "次の画像/文書の日本語テキストのみを抽出してください。余計な説明は不要です。" }
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
    let ocrResp;
    let modelId = MODEL_ID;
    try {
      ocrResp = await br.send(new ConverseCommand({
        modelId,
        messages: [{ role: "user", content }],
        inferenceConfig: { maxTokens: 300, temperature: 0.0 }
      }));
    } catch (err) {
      const msg = String(err?.message || err || "");
      if (/inference profile/i.test(msg) && FALLBACK_MODEL_ID) {
        modelId = FALLBACK_MODEL_ID;
        ocrResp = await br.send(new ConverseCommand({
          modelId,
          messages: [{ role: "user", content }],
          inferenceConfig: { maxTokens: 300, temperature: 0.0 }
        }));
      } else if (/marketplace|access is denied|subscribe/i.test(msg)) {
        // Bedrock マーケットプレース未許可等 → Textract でOCRフォールバック（画像のみ対応）
        if (isPDF) return res(400, { error: "PDFのOCRは現在未対応です（画像でお試しください）" });
        const tr = await tx.send(new DetectDocumentTextCommand({ Document: { Bytes: bytes } }));
        const lines = (tr.Blocks || []).filter(b => b.BlockType === 'LINE').map(b => b.Text).filter(Boolean);
        const extracted = lines.join('\n').trim();
        if (!extracted) return res(200, { extractedPreview: "", quiz: [] });

        // Textractで抽出できたら、そのままクイズ生成へ（モデルはテキスト専用に切り替え）
        modelId = "amazon.titan-text-express-v1";
        const quizPrompt = [{
          role: "user",
          content: [{ text:
`以下の本文から日本語の小テストを5問（四択3＋穴埋め2）で作成してください。
各問に 正答・解説・根拠（本文の該当行） を含め、全体を JSON で返してください。
出力は {"questions": Question[]} の形で返してください。
Question: {"type": "mcq|cloze", "question": string, "choices"?: string[], "answer": string, "explanation": string, "sourceText": string}
本文:
${extracted}`}]
        }];

        const quizResp = await br.send(new ConverseCommand({ modelId, messages: quizPrompt, inferenceConfig: { maxTokens: 1200, temperature: 0.2 } }));
        const quizText = quizResp.output?.message?.content?.map(b => b?.text || "").join("\n").trim();
        return res(200, { extractedPreview: extracted.slice(0, 400), quiz: safeJson(quizText) });

      } else if (/could not process image/i.test(msg) || /unsupported image/i.test(msg)) {
        return res(400, { error: "モデルが画像を処理できませんでした（解像度/形式を確認）" });
      } else {
        throw err;
      }
    }

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

    let quizResp;
    try {
      quizResp = await br.send(new ConverseCommand({
        modelId,
        messages: quizPrompt,
        inferenceConfig: { maxTokens: 1200, temperature: 0.2 }
      }));
    } catch (err) {
      const msg = String(err?.message || err || "");
      if (/inference profile/i.test(msg) && FALLBACK_MODEL_ID && modelId !== FALLBACK_MODEL_ID) {
        modelId = FALLBACK_MODEL_ID;
        quizResp = await br.send(new ConverseCommand({
          modelId,
          messages: quizPrompt,
          inferenceConfig: { maxTokens: 1200, temperature: 0.2 }
        }));
      } else {
        throw err;
      }
    }

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

// sanitizeModelId は廃止（指定されたモデルIDをそのまま使う）
