// getPresignedUrl (Node.js 20)
import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

const s3 = new S3Client({ region: process.env.AWS_REGION });
const BUCKET = process.env.BUCKET;

export const handler = async (event) => {
  try {
    const body = parseBody(event);
    const { filename, contentType } = body;

    if (!BUCKET) return res(500, { error: "環境変数 BUCKET が未設定です" });
    if (!filename) return res(400, { error: "filename が必要です" });

    const Key = `uploads/${Date.now()}_${sanitize(filename)}`;
    const cmd = new PutObjectCommand({ Bucket: BUCKET, Key, ContentType: contentType || "application/octet-stream" });
    const url = await getSignedUrl(s3, cmd, { expiresIn: 900 }); // 15分

    return res(200, { url, key: Key, bucket: BUCKET });
  } catch (e) {
    console.error(e);
    return res(500, { error: String(e?.message || e) });
  }
};

function res(code, body){
  return {
    statusCode: code,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "POST,OPTIONS",
      "access-control-allow-headers": "content-type"
    },
    body: JSON.stringify(body)
  };
}

function parseBody(event){
  if (event?.body) {
    try { return JSON.parse(event.body); } catch {}
  }
  return {};
}

function sanitize(name){
  return String(name).replace(/[^\w.\-]/g, "_");
}
