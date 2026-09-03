import { bytesToArrayBuffer, bytesToHex } from "./encoding.ts";
import type { MirrorTarget } from "./hub.ts";

export interface R2Config {
  endpoint: string;
  bucket: string;
  accessKeyId: string;
  secretAccessKey: string;
  region: string;
}

export class R2MirrorTarget implements MirrorTarget {
  constructor(private readonly config: R2Config) {}

  async put(
    path: string,
    body: Uint8Array,
    contentType: string,
    cacheControl: string,
  ): Promise<void> {
    const url = new URL(`${trimTrailingSlash(this.config.endpoint)}/${this.config.bucket}/${path}`);
    const now = new Date();
    const amzDate = amzDateStamp(now);
    const dateStamp = amzDate.slice(0, 8);
    const payloadHash = await sha256Hex(body);
    const headers = new Headers({
      "cache-control": `max-age=${cacheControl}`,
      "content-type": contentType,
      host: url.host,
      "x-amz-content-sha256": payloadHash,
      "x-amz-date": amzDate,
    });
    const signedHeaders = [
      "cache-control",
      "content-type",
      "host",
      "x-amz-content-sha256",
      "x-amz-date",
    ].join(";");
    const canonicalHeaders = [
      `cache-control:${headers.get("cache-control")}`,
      `content-type:${headers.get("content-type")}`,
      `host:${headers.get("host")}`,
      `x-amz-content-sha256:${headers.get("x-amz-content-sha256")}`,
      `x-amz-date:${headers.get("x-amz-date")}`,
      "",
    ].join("\n");
    const canonicalRequest = [
      "PUT",
      encodePath(url.pathname),
      "",
      canonicalHeaders,
      signedHeaders,
      payloadHash,
    ].join("\n");
    const credentialScope = `${dateStamp}/${this.config.region}/s3/aws4_request`;
    const stringToSign = [
      "AWS4-HMAC-SHA256",
      amzDate,
      credentialScope,
      await sha256Hex(new TextEncoder().encode(canonicalRequest)),
    ].join("\n");
    const signingKey = await signatureKey(
      this.config.secretAccessKey,
      dateStamp,
      this.config.region,
      "s3",
    );
    const signature = bytesToHex(await hmacBytes(signingKey, stringToSign));
    headers.set(
      "authorization",
      `AWS4-HMAC-SHA256 Credential=${this.config.accessKeyId}/${credentialScope}, SignedHeaders=${signedHeaders}, Signature=${signature}`,
    );
    const response = await fetch(url, { method: "PUT", headers, body: bytesToArrayBuffer(body) });
    if (!response.ok) {
      throw new Error("R2_MIRROR_FAILED");
    }
  }
}

export function r2ConfigFromDeno(): R2Config {
  return {
    endpoint: requiredR2Env("R2_ENDPOINT"),
    bucket: requiredR2Env("R2_BUCKET"),
    accessKeyId: requiredR2Env("R2_ACCESS_KEY_ID"),
    secretAccessKey: requiredR2Env("R2_SECRET_ACCESS_KEY"),
    region: Deno.env.get("R2_REGION") ?? "auto",
  };
}

function requiredR2Env(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error("R2_ENV_MISSING");
  return value;
}

function trimTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

function encodePath(path: string): string {
  return path.split("/").map((part) => encodeURIComponent(part)).join("/");
}

function amzDateStamp(date: Date): string {
  return date.toISOString().replace(/[:-]|\.\d{3}/g, "");
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytesToArrayBuffer(bytes));
  return bytesToHex(new Uint8Array(digest));
}

async function hmacBytes(key: Uint8Array, data: string): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    bytesToArrayBuffer(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(
    await crypto.subtle.sign("HMAC", cryptoKey, new TextEncoder().encode(data)),
  );
}

async function signatureKey(
  secret: string,
  dateStamp: string,
  region: string,
  service: string,
): Promise<Uint8Array> {
  const dateKey = await hmacBytes(new TextEncoder().encode(`AWS4${secret}`), dateStamp);
  const dateRegionKey = await hmacBytes(dateKey, region);
  const dateRegionServiceKey = await hmacBytes(dateRegionKey, service);
  return await hmacBytes(dateRegionServiceKey, "aws4_request");
}
