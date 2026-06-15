import {
  CLIENT_SETTINGS_STORE,
  openStarunDatabase,
  requestResult,
  transactionDone,
} from "./history/db";
import type { ClientSetting } from "./history/types";

const CLIENT_ID_KEY = "anonymous_client_id";
const UUID_V4_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function isValidClientId(value: unknown): value is string {
  return typeof value === "string" && UUID_V4_PATTERN.test(value);
}

function generateClientId(): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi) {
    throw new Error("Secure browser crypto is unavailable.");
  }
  if (typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }

  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

export async function getOrCreateClientId(): Promise<string> {
  const db = await openStarunDatabase();
  const transaction = db.transaction(CLIENT_SETTINGS_STORE, "readwrite");
  const completion = transactionDone(transaction);
  const store = transaction.objectStore(CLIENT_SETTINGS_STORE);
  const existing = await requestResult<ClientSetting | undefined>(
    store.get(CLIENT_ID_KEY),
  );

  if (existing && isValidClientId(existing.value)) {
    await completion;
    return existing.value;
  }

  const value = generateClientId();
  await requestResult(store.put({ key: CLIENT_ID_KEY, value }));
  await completion;
  return value;
}
