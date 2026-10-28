// The owner console: the owner's own assistant on the left, the shop's products on the right.
// Products are added and changed through the chat (a photo and a price, "kurta is now 2900"),
// and only after the owner taps Confirm; the list shows the result.
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api";
import type { LowStock, OwnerMe, OwnerProduct } from "../api";
import { BooksPanel, LowStockList, useBooks } from "../components/Books";
import { CatalogSyncPanel } from "../components/CatalogSync";
import { ChatView } from "../components/ChatView";
import { DashboardPanel } from "../components/Dashboard";
import { FraudReportsPanel, FraudReviewPanel } from "../components/Fraud";
import { ImportsPanel } from "../components/Imports";
import { InboxPanel } from "../components/Inbox";
import { OrdersPanel, useOrders } from "../components/Orders";
import { PaymentReviews, PaymentsPanel, usePayments } from "../components/Payments";
import { AnswersPanel } from "../components/Answers";
import { OffersPanel } from "../components/Offers";
import { RefundsPanel, ReturnsPanel } from "../components/Returns";
import { ReviewsPanel } from "../components/Reviews";
import { ShelfCountPanel } from "../components/ShelfCount";
import { SizeChartsPanel } from "../components/SizeCharts";
import { WaitingPanel } from "../components/Waiting";
import { PostsPanel } from "../components/Posts";
import { Approvals, CustomersPanel, SettingsPanel, useApprovals } from "../components/Shield";
import { Alerts, StockPanel, useNotices } from "../components/Stock";
import { ActivityPanel, TeamPanel } from "../components/Team";
import { Page } from "../components/Shell";
import { languageOf, scriptLang } from "../i18n";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { clearOwnerToken, readOwnerToken, saveOwnerToken, tokenFromHash } from "../owner";
import { useServer } from "../server";

const SUGGESTIONS = ["console.suggest_1", "console.suggest_2", "console.suggest_3"] as const;
const STATUS: Record<OwnerProduct["status"], Key> = {
  draft: "console.status_draft",
  active: "console.status_active",
  archived: "console.status_archived",
};

/** The session from the link's '#t=…' (then out of the address bar), else the stored one. */
function takeToken(): string | null {
  const fresh = tokenFromHash(window.location.hash);
  if (fresh) {
    saveOwnerToken(fresh);
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    return fresh;
  }
  return readOwnerToken();
}

function SignedOut({ expired }: { expired: boolean }) {
  const { t } = useLang();
  return (
    <Page>
      <section className="plain-hero console-signed-out">
        <h1 className="title">{t("console.signed_out_title")}</h1>
        {expired && (
          <p className="console-expired" role="alert">
            {t("console.expired")}
          </p>
        )}
        <p className="lede">{t("console.signed_out_body")}</p>
        <div className="console-ways">
          <Link to="/start" className="button button-saffron">
            {t("home.make_shop")}
          </Link>
          <Link to="/#shops" className="button button-ink">
            {t("home.demo_shops")}
          </Link>
        </div>
      </section>
    </Page>
  );
}

function WordingEditor({
  product,
  me,
  token,
  onSaved,
}: {
  product: OwnerProduct;
  me: OwnerMe;
  token: string;
  onSaved: () => void;
}) {
  const { t } = useLang();
  const [lang, setLang] = useState(me.languages[0] ?? "ur");
  const current = product.translations[lang];
  const [name, setName] = useState(current?.name ?? "");
  const [description, setDescription] = useState(current?.description ?? "");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");

  function choose(next: string) {
    setLang(next);
    setName(product.translations[next]?.name ?? "");
    setDescription(product.translations[next]?.description ?? "");
    setState("idle");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setState("saving");
    try {
      await api.saveWording(token, product.id, lang, { name, description });
      setState("saved");
      onSaved();
    } catch {
      setState("failed");
    }
  }

  const dir = languageOf(lang).dir === "rtl" ? "rtl" : "ltr";
  return (
    <form className="wording" onSubmit={(event) => void save(event)}>
      <div className="wording-langs" role="radiogroup" aria-label={t("nav.language")}>
        {me.languages.map((tag) => (
          <button
            key={tag}
            type="button"
            role="radio"
            aria-checked={tag === lang}
            onClick={() => choose(tag)}
            lang={tag}
          >
            {languageOf(tag).native}
          </button>
        ))}
      </div>
      <p className="wording-by">
        {current?.by === "owner"
          ? t("console.by_owner")
          : current?.name
            ? t("console.by_model")
            : t("console.not_written")}
      </p>
      <label>
        <span>{t("console.name")}</span>
        <input
          value={name}
          lang={lang}
          dir={dir}
          required
          maxLength={160}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.description")}</span>
        <textarea
          value={description}
          lang={lang}
          dir={dir}
          rows={3}
          maxLength={2000}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("console.save")}
        </button>
        {state === "saved" && <span role="status">{t("console.saved")}</span>}
        {state === "failed" && <span role="alert">{t("console.save_failed")}</span>}
      </div>
    </form>
  );
}

function ProductRow({
  product,
  me,
  token,
  onSaved,
}: {
  product: OwnerProduct;
  me: OwnerMe;
  token: string;
  onSaved: () => void;
}) {
  const { lang, t } = useLang();
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  return (
    <li className={`khata-entry is-${product.status}`}>
      <div className="khata-head">
        <span className="khata-name" lang={scriptLang(product.name)} dir="auto">
          {product.name}
        </span>
        <span className="khata-price">{money(product.price)}</span>
      </div>
      <div className="khata-facts">
        <span className="khata-code">{product.code}</span>
        <span className={`khata-status status-${product.status}`}>{t(STATUS[product.status])}</span>
        {product.floor_price !== null && (
          <span>{t("console.lowest", { price: money(product.floor_price) })}</span>
        )}
      </div>
      <ul className="khata-variants">
        {product.variants.map((variant) => (
          <li key={variant.id}>
            <span dir="auto">
              {Object.values(variant.options).join(" ") || t("console.one_kind")}
            </span>
            <span>
              {variant.stock === null
                ? t("console.stock_open")
                : t("owner.in_stock", { count: variant.stock })}
            </span>
          </li>
        ))}
      </ul>
      {me.languages.length > 0 && (
        <details className="khata-wording">
          <summary>{t("console.wording")}</summary>
          <WordingEditor product={product} me={me} token={token} onSaved={onSaved} />
        </details>
      )}
    </li>
  );
}

function Products({
  me,
  token,
  products,
  low,
  onSaved,
}: {
  me: OwnerMe;
  token: string;
  products: OwnerProduct[] | null;
  low: LowStock[];
  onSaved: () => void;
}) {
  const { t } = useLang();
  return (
    <section className="console-products" aria-labelledby="console-products-title">
      <h2 id="console-products-title" className="section-title">
        {t("shop.products")}
        {products && <span className="console-count">{products.length}</span>}
      </h2>
      <LowStockList low={low} />
      {products && products.length === 0 && <p className="aside">{t("console.no_products")}</p>}
      <ul className="khata" aria-busy={!products}>
        {products?.map((product) => (
          <ProductRow key={product.id} product={product} me={me} token={token} onSaved={onSaved} />
        ))}
      </ul>
    </section>
  );
}

/** The signed-in console: what waits for an answer, the day at a glance, the assistant and
 * products, orders, stock, payments and the books, then customers, the team, settings and the
 * activity log; each person sees the parts their role allows (feature 42). */
function Console({
  me,
  token,
  onSignOut,
  onSessionEnded,
}: {
  me: OwnerMe;
  token: string;
  onSignOut: () => void;
  onSessionEnded: () => void;
}) {
  const { t } = useLang();
  const [products, setProducts] = useState<OwnerProduct[] | null>(null);
  const [replies, setReplies] = useState(0);
  const [saves, setSaves] = useState(0);
  const [panel, setPanel] = useState<"chat" | "products">("chat");
  const can = (right: string) => me.rights.includes(right);
  const payments = usePayments(token, can("payments"));
  const orders = useOrders(token, can("orders"));
  const books = useBooks(token, can("books"));
  const approvals = useApprovals(token);
  const notices = useNotices(token);
  const saved = useCallback(() => setSaves((n) => n + 1), []);
  const refreshBooks = books.refresh;
  const stockChanged = useCallback(() => {
    saved();
    refreshBooks();
  }, [saved, refreshBooks]);

  // Each reply may have added or changed a product: read the list again.
  useEffect(() => {
    let current = true;
    api.ownerProducts(token).then(
      (list) => current && setProducts(list),
      () => {
        // The last list stays on screen; the next reply tries again.
      },
    );
    return () => {
      current = false;
    };
  }, [token, replies, saves]);

  return (
    <Page>
      <header className="console-head">
        <div>
          <p className="console-kicker">{t("console.title")}</p>
          <h1 className="console-shop" lang={scriptLang(me.shop_name)} dir="auto">
            {me.shop_name}
          </h1>
        </div>
        <div className="console-actions">
          <Link to={`/s/${me.slug}`} className="action">
            {t("console.open_shop")}
          </Link>
          <button type="button" className="action" onClick={onSignOut}>
            {t("console.sign_out")}
          </button>
        </div>
      </header>
      {me.is_demo && <p className="console-demo">{t("console.demo_note")}</p>}
      <Approvals token={token} list={approvals.list} onChange={approvals.refresh} />
      <Alerts me={me} token={token} list={notices.list} onChange={notices.refresh} />
      {can("chats") && <InboxPanel me={me} token={token} />}
      {can("payments") && (
        <PaymentReviews me={me} token={token} data={payments.data} onChange={payments.refresh} />
      )}
      <DashboardPanel me={me} token={token} />
      <div className="console-switch" role="tablist" aria-label={t("console.title")}>
        {(["chat", "products"] as const).map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={panel === name}
            onClick={() => setPanel(name)}
          >
            {name === "chat" ? t("console.assistant") : t("shop.products")}
          </button>
        ))}
      </div>
      <div className={`console-grid shows-${panel}`}>
        <ChatView
          key={me.slug}
          slug={me.slug}
          variant="panel"
          label={t("console.assistant")}
          title={t("console.assistant")}
          note={t("console.chat_note")}
          empty={t("console.chat_empty")}
          suggestions={SUGGESTIONS.map((key) => t(key))}
          ownerToken={token}
          onSignedOut={onSessionEnded}
          onReplies={setReplies}
        />
        <Products me={me} token={token} products={products} low={books.low} onSaved={saved} />
      </div>
      {can("catalog") && <ImportsPanel me={me} token={token} onAdded={saved} />}
      {can("orders") && (
        <OrdersPanel me={me} token={token} orders={orders.orders} onChange={orders.refresh} />
      )}
      {can("stock") && (
        <StockPanel me={me} token={token} products={products} onChange={stockChanged} />
      )}
      {can("stock") && <ShelfCountPanel token={token} onSaved={stockChanged} />}
      {can("orders") && <ReturnsPanel token={token} canShip={can("ship")} />}
      {can("orders") && <WaitingPanel token={token} />}
      {can("payments") && (
        <PaymentsPanel me={me} token={token} data={payments.data} onChange={payments.refresh} />
      )}
      {can("payments") && <RefundsPanel me={me} token={token} />}
      {can("books") && (
        <BooksPanel me={me} token={token} books={books.books} onChange={books.refresh} />
      )}
      {can("chats") && <PostsPanel token={token} products={products ?? []} />}
      {can("catalog") && <CatalogSyncPanel token={token} />}
      {can("catalog") && <SizeChartsPanel token={token} />}
      {can("orders") && <CustomersPanel me={me} token={token} />}
      {can("orders") && <ReviewsPanel token={token} canReply={can("chats")} />}
      {can("trust") && <FraudReportsPanel token={token} />}
      {me.is_platform && can("trust") && <FraudReviewPanel token={token} />}
      {can("team") && <TeamPanel token={token} />}
      {can("settings") && <OffersPanel me={me} token={token} products={products ?? []} />}
      {can("settings") && <AnswersPanel token={token} />}
      {can("settings") && <SettingsPanel token={token} />}
      {can("audit") && <ActivityPanel token={token} />}
    </Page>
  );
}

export function OwnerPage() {
  const { t } = useLang();
  const { state } = useServer();
  const [token, setToken] = useState<string | null>(takeToken);
  const [expired, setExpired] = useState(false);
  const [me, setMe] = useState<OwnerMe | null>(null);

  const signOut = useCallback((ended: boolean) => {
    clearOwnerToken();
    setToken(null);
    setMe(null);
    setExpired(ended);
  }, []);
  const sessionEnded = useCallback(() => signOut(true), [signOut]);

  useEffect(() => {
    if (!token || state !== "online") return;
    let current = true;
    api.me(token).then(
      (info) => current && setMe(info),
      (error: unknown) => {
        if (current && error instanceof ApiError && error.status === 401) sessionEnded();
      },
    );
    return () => {
      current = false;
    };
  }, [token, state, sessionEnded]);

  if (!token) return <SignedOut expired={expired} />;
  if (!me) {
    return (
      <Page>
        <div
          className="profile-skeleton console-skeleton"
          aria-busy="true"
          aria-label={t("console.title")}
        />
      </Page>
    );
  }
  return (
    <Console me={me} token={token} onSignOut={() => signOut(false)} onSessionEnded={sessionEnded} />
  );
}
