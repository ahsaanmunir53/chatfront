// Phone-camera shelf counting (feature 69): keep a reviewable tally and never change stock
// until the owner confirms it. The camera is a phone fallback; the Meta glasses SDK is unavailable.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import type { ShelfCount } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";

const FRAME_INTERVAL_MS = 4_000;
const MAX_FRAME_SIDE = 1280;
const MAX_FRAMES_PER_SESSION = 20;

type TallyRow = ShelfCount["rows"][number];
type Notice = { key: Key; count?: number; alert?: boolean };

export function ShelfCameraPanel({
  token,
  lang,
  onSaved,
}: {
  token: string;
  lang: string;
  onSaved: () => void;
}) {
  const { t } = useLang();
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const busy = useRef(false);
  const framesCaptured = useRef(0);
  const tallyRef = useRef<Record<string, TallyRow>>({});
  const [cameraOn, setCameraOn] = useState(false);
  const [opening, setOpening] = useState(false);
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [tally, setTally] = useState<Record<string, TallyRow>>({});
  const [counts, setCounts] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<string[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);

  const speak = useCallback(
    (rows: TallyRow[]) => {
      if (!("speechSynthesis" in window) || typeof SpeechSynthesisUtterance === "undefined") return;
      const utterance = new SpeechSynthesisUtterance(
        t("stock.camera_spoken", {
          products: rows.length,
          pieces: rows.reduce((sum, row) => sum + row.counted, 0),
        }),
      );
      utterance.lang = lang || navigator.language;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    },
    [lang, t],
  );

  const stopCamera = useCallback(() => {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    setCameraOn(false);
    setReading(false);
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }, []);

  useEffect(
    () => () => {
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    },
    [],
  );

  const scan = useCallback(async () => {
    const element = video.current;
    const activeStream = stream.current;
    if (
      busy.current ||
      !activeStream ||
      !element ||
      element.videoWidth < 1 ||
      element.videoHeight < 1
    ) {
      return;
    }

    busy.current = true;
    setReading(true);
    try {
      const scale = Math.min(
        1,
        MAX_FRAME_SIDE / Math.max(element.videoWidth, element.videoHeight),
      );
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(element.videoWidth * scale));
      canvas.height = Math.max(1, Math.round(element.videoHeight * scale));
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Canvas is unavailable");
      context.drawImage(element, 0, 0, canvas.width, canvas.height);
      const frame = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(
          (blob) => (blob ? resolve(blob) : reject(new Error("Could not capture a frame"))),
          "image/jpeg",
          0.82,
        );
      });
      framesCaptured.current += 1;
      const found = await api.countShelf(token, frame, true);
      if (stream.current !== activeStream) return;
      if (found.rows.length === 0) {
        setNotice({ key: "stock.count_nothing" });
        return;
      }

      const next = { ...tallyRef.current };
      const inputUpdates: Record<string, string> = {};
      let changed = false;
      for (const row of found.rows) {
        const old = next[row.variant_id];
        if (!old) {
          next[row.variant_id] = row;
          inputUpdates[row.variant_id] = String(row.counted);
          changed = true;
        } else if (row.counted > old.counted) {
          next[row.variant_id] = { ...old, counted: row.counted, before: old.before ?? row.before };
          inputUpdates[row.variant_id] = String(row.counted);
          changed = true;
        }
      }
      tallyRef.current = next;
      setTally(next);
      if (Object.keys(inputUpdates).length) {
        setCounts((current) => ({ ...current, ...inputUpdates }));
      }
      setProblems((current) => [...new Set([...current, ...found.problems])]);
      setNotice({ key: "stock.camera_updated" });
      if (changed) speak(Object.values(next));
    } catch (error) {
      const modelUnavailable = error instanceof ApiError && error.status === 503;
      setNotice({
        key: modelUnavailable ? "stock.count_error_model" : "stock.count_error",
        alert: true,
      });
    } finally {
      busy.current = false;
      if (stream.current && framesCaptured.current >= MAX_FRAMES_PER_SESSION) {
        stopCamera();
        setNotice({ key: "stock.camera_limit" });
      } else if (stream.current) {
        setReading(false);
      }
    }
  }, [speak, stopCamera, token]);

  useEffect(() => {
    if (!cameraOn) return;
    void scan();
    const timer = window.setInterval(() => void scan(), FRAME_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [cameraOn, scan]);

  async function startCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setNotice({ key: "stock.camera_unsupported", alert: true });
      return;
    }
    setOpening(true);
    framesCaptured.current = 0;
    setNotice(null);
    try {
      const opened = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: MAX_FRAME_SIDE } },
        audio: false,
      });
      stream.current = opened;
      if (video.current) {
        video.current.srcObject = opened;
        await video.current.play();
      }
      setCameraOn(true);
      setNotice({ key: "stock.camera_scanning" });
    } catch {
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
      setCameraOn(false);
      setNotice({ key: "stock.camera_error", alert: true });
    } finally {
      setOpening(false);
    }
  }

  function clearTally() {
    tallyRef.current = {};
    setTally({});
    setCounts({});
    setProblems([]);
    setNotice(null);
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }

  async function save() {
    const rows = Object.values(tallyRef.current);
    if (rows.length === 0) {
      setNotice({ key: "stock.count_nothing", alert: true });
      return;
    }
    const lines = rows.map((row) => ({
      variant_id: row.variant_id,
      counted: Number(counts[row.variant_id]),
    }));
    if (
      lines.some(
        (line) =>
          counts[line.variant_id]?.trim() === "" ||
          !Number.isInteger(line.counted) ||
          line.counted < 0,
      )
    ) {
      setNotice({ key: "stock.count_whole", alert: true });
      return;
    }

    stopCamera();
    setSaving(true);
    try {
      const { changed } = await api.saveCount(token, lines);
      clearTally();
      setNotice({ key: "stock.count_saved", count: changed });
      onSaved();
    } catch {
      setNotice({ key: "stock.count_error", alert: true });
    } finally {
      setSaving(false);
    }
  }

  const rows = Object.values(tally).sort((a, b) => a.name.localeCompare(b.name));

  return (
    <section className="pay-panel shelf-camera" aria-labelledby="shelf-camera-title">
      <h2 id="shelf-camera-title" className="section-title">
        {t("stock.camera_title")}
      </h2>
      <p className="aside">{t("stock.camera_lede")}</p>
      <div className="shelf-camera-actions">
        {!cameraOn ? (
          <button
            type="button"
            className="button button-ink button-small"
            disabled={opening || saving}
            onClick={() => void startCamera()}
          >
            {opening ? t("stock.camera_starting") : t("stock.camera_start")}
          </button>
        ) : (
          <button
            type="button"
            className="button button-ink button-small"
            disabled={saving}
            onClick={stopCamera}
          >
            {t("stock.camera_stop")}
          </button>
        )}
        {rows.length > 0 && (
          <button
            type="button"
            className="button button-small"
            disabled={saving}
            onClick={clearTally}
          >
            {t("stock.camera_clear")}
          </button>
        )}
      </div>
      <video
        ref={video}
        className="shelf-camera-video"
        autoPlay
        playsInline
        muted
        hidden={!cameraOn}
        aria-label={t("stock.camera_title")}
      />
      {reading && cameraOn && <p role="status">{t("stock.camera_scanning")}</p>}
      {notice && (
        <p role={notice.alert ? "alert" : "status"} className="pay-note">
          {t(notice.key, { count: notice.count ?? 0 })}
        </p>
      )}
      {rows.length > 0 && (
        <div className="shelf-count-rows">
          <table>
            <thead>
              <tr>
                <th scope="col">{t("stock.count_col_name")}</th>
                <th scope="col">{t("stock.count_col_books")}</th>
                <th scope="col">{t("stock.count_col_counted")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.variant_id}>
                  <th scope="row" dir="auto">
                    {row.name}
                  </th>
                  <td>{row.before ?? t("console.stock_open")}</td>
                  <td>
                    <input
                      aria-label={t("stock.count_for", { name: row.name })}
                      inputMode="numeric"
                      value={counts[row.variant_id] ?? ""}
                      disabled={saving}
                      onChange={(event) =>
                        setCounts((current) => ({
                          ...current,
                          [row.variant_id]: event.target.value,
                        }))
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {problems.length > 0 && (
            <ul className="pay-note">
              {problems.map((problem) => (
                <li key={problem} dir="auto">
                  {problem}
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            className="button button-ink button-small"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? t("stock.count_reading") : t("stock.camera_confirm")}
          </button>
        </div>
      )}
    </section>
  );
}
