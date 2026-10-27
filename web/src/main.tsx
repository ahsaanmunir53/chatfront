import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { useEffect } from "react";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { WakeBar } from "./components/WakeBar";
import { BASE_PATH } from "./config";
import { LangProvider } from "./lang";
import { ChatPage } from "./pages/ChatPage";
import { HomePage } from "./pages/HomePage";
import { DashboardPage, NotFoundPage, ReceiptPage, TrustPage } from "./pages/InfoPages";
import { ShopPage } from "./pages/ShopPage";
import { ServerProvider } from "./server";
import "./styles.css";

/** New page, top of the page; the chat shows its own wake-up line inside the phone. */
function RouteEffects() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [pathname]);
  return pathname.endsWith("/chat") ? null : <WakeBar />;
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
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/r/:code?" element={<ReceiptPage />} />
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
