import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { LangProvider } from "./lang";
import { ChatPage } from "./pages/ChatPage";
import { HomePage } from "./pages/HomePage";
import { DashboardPage, NotFoundPage, ReceiptPage, TrustPage } from "./pages/InfoPages";
import { ShopPage } from "./pages/ShopPage";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(
  <StrictMode>
    <LangProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/s/:slug" element={<ShopPage />} />
          <Route path="/s/:slug/chat" element={<ChatPage />} />
          <Route path="/s/:slug/trust" element={<TrustPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/r/:code?" element={<ReceiptPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </BrowserRouter>
    </LangProvider>
  </StrictMode>,
);

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // The app works without offline support.
    });
  });
}
