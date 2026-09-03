import { canonicalBytes, unsignedManifest } from "./canonical.ts";
import { base64ToBytes, bytesToArrayBuffer, hexToBytes } from "./encoding.ts";

export interface ManifestKeyEnv {
  hubEnv: string;
  manifestPubkeyA?: string;
  manifestPubkeyB?: string;
  manifestTestPubkey?: string;
}

export function keyEnvFromDeno(): ManifestKeyEnv {
  return {
    hubEnv: Deno.env.get("HUB_ENV") ?? "production",
    manifestPubkeyA: Deno.env.get("MANIFEST_PUBKEY_A") ?? undefined,
    manifestPubkeyB: Deno.env.get("MANIFEST_PUBKEY_B") ?? undefined,
    manifestTestPubkey: Deno.env.get("MANIFEST_TEST_PUBKEY") ?? undefined,
  };
}

export async function verifyManifestSignature(
  manifest: Record<string, unknown>,
  env: ManifestKeyEnv,
): Promise<boolean> {
  try {
    const signature = manifest.signature;
    const keyId = manifest.key_id;
    if (typeof signature !== "string" || typeof keyId !== "string") {
      return false;
    }
    if (!signature.startsWith("ed25519:")) {
      return false;
    }
    const candidates = publicKeyCandidates(keyId, env);
    if (candidates.length === 0) {
      return false;
    }
    const signatureBytes = base64ToBytes(signature.slice("ed25519:".length));
    const payload = canonicalBytes(unsignedManifest(manifest));
    for (const publicKeyHex of candidates) {
      if (await verifyEd25519(hexToBytes(publicKeyHex), signatureBytes, payload)) {
        return true;
      }
    }
    return false;
  } catch {
    return false;
  }
}

function publicKeyCandidates(keyId: string, env: ManifestKeyEnv): string[] {
  const result: string[] = [];
  if (keyId === "A" && env.manifestPubkeyA) {
    result.push(env.manifestPubkeyA);
  }
  if (keyId === "B" && env.manifestPubkeyB) {
    result.push(env.manifestPubkeyB);
  }
  if (env.hubEnv === "staging" && env.manifestTestPubkey) {
    result.push(env.manifestTestPubkey);
  }
  return result;
}

async function verifyEd25519(
  publicKey: Uint8Array,
  signature: Uint8Array,
  payload: Uint8Array,
): Promise<boolean> {
  try {
    const key = await crypto.subtle.importKey(
      "raw",
      bytesToArrayBuffer(publicKey),
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    return await crypto.subtle.verify(
      { name: "Ed25519" },
      key,
      bytesToArrayBuffer(signature),
      bytesToArrayBuffer(payload),
    );
  } catch {
    return false;
  }
}
