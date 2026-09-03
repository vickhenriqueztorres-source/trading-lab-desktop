import { base64ToBytes, bytesToArrayBuffer, bytesToBase64 } from "./encoding.ts";

export interface JwtClaims {
  client_id: string;
  role: string;
  exp: number;
}

export async function signAnonJwt(
  clientId: string,
  secret: string,
  nowTs: number,
): Promise<string> {
  const header = base64Url(new TextEncoder().encode(JSON.stringify({ alg: "HS256", typ: "JWT" })));
  const payload = base64Url(
    new TextEncoder().encode(
      JSON.stringify({
        role: "anon",
        client_id: clientId,
        exp: nowTs + 365 * 86400,
      }),
    ),
  );
  const signingInput = `${header}.${payload}`;
  const key = await crypto.subtle.importKey(
    "raw",
    bytesToArrayBuffer(new TextEncoder().encode(secret)),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signingInput)),
  );
  return `${signingInput}.${base64Url(signature)}`;
}

export async function verifyAnonJwt(
  token: string,
  secret: string,
  nowTs: number,
): Promise<JwtClaims | null> {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const signingInput = `${parts[0]}.${parts[1]}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const ok = await crypto.subtle.verify(
    "HMAC",
    key,
    bytesToArrayBuffer(base64UrlToBytes(parts[2])),
    new TextEncoder().encode(signingInput),
  );
  if (!ok) return null;
  const claims = JSON.parse(new TextDecoder().decode(base64UrlToBytes(parts[1]))) as Partial<
    JwtClaims
  >;
  if (
    claims.role !== "anon" ||
    typeof claims.client_id !== "string" ||
    !isUuid(claims.client_id) ||
    typeof claims.exp !== "number" ||
    !Number.isSafeInteger(claims.exp) ||
    claims.exp <= nowTs
  ) {
    return null;
  }
  return claims as JwtClaims;
}

export function bearerToken(request: Request): string | null {
  const value = request.headers.get("authorization") ?? "";
  const prefix = "Bearer ";
  return value.startsWith(prefix) ? value.slice(prefix.length) : null;
}

function base64Url(bytes: Uint8Array): string {
  return bytesToBase64(bytes).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function base64UrlToBytes(text: string): Uint8Array {
  const padded = text.replaceAll("-", "+").replaceAll("_", "/").padEnd(
    Math.ceil(text.length / 4) * 4,
    "=",
  );
  return base64ToBytes(padded);
}

export function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}
