import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { useEffect } from "react";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { WakeBar } from "./components/WakeBar";
import { BASE_PATH } from "./config";
import { LangProvider } from "./lang";
import { ChatPage } from "./pages/ChatPage";
import { HomePage } from "./pages/HomePage";
import { DashboardPage, NotFoundPage, TrustPage } from "./pages/InfoPages";
import { ReceiptPage } from "./pages/ReceiptPage";
import { TrackPage } from "./pages/TrackPage";
import { OwnerPage } from "./pages/OwnerPage";
import { ShopPage } from "./pages/ShopPage";
import { StartPage } from "./pages/StartPage";
import { ServerProvider } from "./server";
import "./styles.css";

/** New page, top of the page; full-screen chats show their own wake-up line in the phone. */
function RouteEffects() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    // A link to a section (/#shops) lands on it; any other new page starts at the top.
    const section = /^#[\w-]+$/.test(hash) ? document.getElementById(hash.slice(1)) : null;
    if (section) section.scrollIntoView();
    else window.scrollTo({ top: 0, behavior: "instant" });
  }, [pathname, hash]);
  return pathname.endsWith("/chat") || pathname === "/start" ? null : <WakeBar />;
}

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(
  <StrictMode>
    <LangProvider>
      <ServerProvider>
        <BrowserRouter basename={BASE_PATH.replace(/\/$/, "")}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/s/:slug" element={<ShopPage />} />
            <Route path="/s/:slug/chat" element={<ChatPage />} />
            <Route path="/s/:slug/trust" element={<TrustPage />} />
            <Route path="/start" element={<StartPage />} />
            <Route path="/owner" element={<OwnerPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/r/:code?" element={<ReceiptPage />} />
            <Route path="/t/:code" element={<TrackPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
          <RouteEffects />
        </BrowserRouter>
      </ServerProvider>
    </LangProvider>
  </StrictMode>,
);

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register(`${BASE_PATH}sw.js`).catch(() => {
      // The app works without offline support.
    });
  });
}
