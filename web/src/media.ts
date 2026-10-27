// Photos and voice notes typed into a chat. Photos are shrunk on the phone before upload,
// because shop owners send them over mobile data; voice notes are recorded in the format the
// browser does best, and the server turns any of them into what the model reads.
import { useCallback, useEffect, useRef, useState } from "react";

const MAX_SIDE = 1600;
const KEEP_AS_IS_BYTES = 600 * 1024;
export const MAX_VOICE_SECONDS = 120;

/** The longest side brought down to 1600 px as a JPEG; small JPEGs and failures go as they are. */
export async function shrinkImage(file: File): Promise<Blob> {
  if (file.type === "image/jpeg" && file.size <= KEEP_AS_IS_BYTES) return file;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d")?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, "image/jpeg", 0.85));
    return blob && blob.size < file.size ? blob : file;
  } catch {
    return file;
  }
}

// WebM and Ogg Opus are what Chrome, Firefox and Android record; the server reads both.
const VOICE_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];

export function voiceType(supported: (type: string) => boolean): string | undefined {
  return VOICE_TYPES.find(supported);
}

export type RecorderState =
  | { phase: "idle" }
  | { phase: "recording"; seconds: number }
  | { phase: "blocked" }
  | { phase: "unsupported" };

/** Record one voice note at a time. stop() gives the recording; cancel() throws it away.
 * The page stops it at MAX_VOICE_SECONDS, so a long note is sent rather than lost. */
export function useRecorder() {
  const [state, setState] = useState<RecorderState>({ phase: "idle" });
  const recorder = useRef<MediaRecorder | null>(null);
  const finish = useRef<((blob: Blob | null) => void) | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const release = useCallback(() => {
    window.clearInterval(timer.current);
    recorder.current?.stream.getTracks().forEach((track) => track.stop());
    recorder.current = null;
  }, []);

  useEffect(() => release, [release]);

  const start = useCallback(async () => {
    const type =
      typeof MediaRecorder === "undefined"
        ? undefined
        : voiceType((t) => MediaRecorder.isTypeSupported(t));
    if (!type || !navigator.mediaDevices?.getUserMedia) {
      setState({ phase: "unsupported" });
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setState({ phase: "blocked" });
      return;
    }
    const chunks: Blob[] = [];
    const media = new MediaRecorder(stream, { mimeType: type });
    media.ondataavailable = (event) => chunks.push(event.data);
    media.onstop = () => {
      finish.current?.(chunks.length ? new Blob(chunks, { type }) : null);
      finish.current = null;
    };
    recorder.current = media;
    media.start();
    const began = Date.now();
    setState({ phase: "recording", seconds: 0 });
    timer.current = window.setInterval(() => {
      setState({ phase: "recording", seconds: Math.floor((Date.now() - began) / 1000) });
    }, 250);
  }, []);

  const stop = useCallback(
    () =>
      new Promise<Blob | null>((done) => {
        const media = recorder.current;
        if (!media || media.state === "inactive") {
          done(null);
          return;
        }
        finish.current = done;
        media.stop();
        release();
        setState({ phase: "idle" });
      }),
    [release],
  );

  const cancel = useCallback(() => {
    finish.current = null;
    if (recorder.current?.state === "recording") recorder.current.stop();
    release();
    setState({ phase: "idle" });
  }, [release]);

  const dismiss = useCallback(() => setState({ phase: "idle" }), []);

  return { state, start, stop, cancel, dismiss };
}

export function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
