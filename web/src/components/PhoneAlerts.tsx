// Alerts on this phone (feature 115): the console asks the browser for permission, subscribes
// with the server's VAPID key and hands the subscription to the shop, which then sends what it
// tells this person as notifications, even with the console closed.
import { useEffect, useState } from "react";
import { api } from "../api";
import { useLang } from "../lang";

type State = "off" | "on" | "blocked" | "failed";

const supported = () =>
  "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

/** The VAPID public key as push managers take it. */
export function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const base64 = (base64url + "=".repeat((4 - (base64url.length % 4)) % 4))
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const raw = atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function current(): Promise<PushSubscription | null> {
  const registration = await navigator.serviceWorker.getRegistration();
  return registration ? registration.pushManager.getSubscription() : null;
}

export function PhoneAlerts({ token }: { token: string }) {
  const { t, lang } = useLang();
  const [key, setKey] = useState<string | null>(null);
  const [state, setState] = useState<State>("off");
  const [tested, setTested] = useState(false);

  useEffect(() => {
    if (!supported()) return;
    let live = true;
    api.pushKey(token).then(
      (found) => live && setKey(found.public_key),
      () => undefined,
    );
    if (Notification.permission === "denied") setState("blocked");
    current().then(
      (found) => live && found && setState("on"),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [token]);

  if (!supported() || !key) return null;

  async function turnOn(publicKey: string) {
    try {
      if ((await Notification.requestPermission()) !== "granted") {
        setState("blocked");
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      const found = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: keyBytes(publicKey),
      });
      await api.pushOn(token, found.toJSON());
      setState("on");
    } catch {
      setState("failed");
    }
  }

  async function turnOff() {
    const found = await current().catch(() => null);
    if (found) {
      await api.pushOff(token, found.endpoint).catch(() => undefined);
      await found.unsubscribe().catch(() => false);
    }
    setState("off");
    setTested(false);
  }

  async function test() {
    await api.pushTest(token, t("alerts.test_text"), lang).catch(() => undefined);
    setTested(true);
  }

  return (
    <section className="phone-alerts" aria-labelledby="phone-alerts-title">
      <h2 id="phone-alerts-title" className="section-title">
        {t("alerts.title")}
      </h2>
      <p className="pay-note">{t("alerts.lede")}</p>
      {state === "on" ? (
        <div className="phone-alerts-actions">
          <p role="status">{t("alerts.on")}</p>
          <button type="button" className="action" onClick={() => void test()}>
            {t("alerts.test")}
          </button>
          <button type="button" className="action" onClick={() => void turnOff()}>
            {t("alerts.turn_off")}
          </button>
          {tested && <p className="pay-note">{t("alerts.test_sent")}</p>}
        </div>
      ) : (
        <div className="phone-alerts-actions">
          <button
            type="button"
            className="button button-ink button-small"
            onClick={() => void turnOn(key)}
          >
            {t("alerts.turn_on")}
          </button>
          {state === "blocked" && <p role="alert">{t("alerts.blocked")}</p>}
          {state === "failed" && <p role="alert">{t("alerts.failed")}</p>}
        </div>
      )}
    </section>
  );
}
