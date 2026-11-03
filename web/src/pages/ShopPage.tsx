import { useEffect, useState } from "react";
import { useCart } from "../cart";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { ApiError, api } from "../api";
import type { ChannelLink, Countdown } from "../api";
import { bundledProducts, fromApi, productText } from "../catalog";
import { useCopy } from "../clipboard";
import type { ShopProduct } from "../catalog";
import { initials, templateTone } from "../components/Avatar";
import { ChatIcon, KeyIcon, PinIcon, SendIcon } from "../components/Icons";
import { ShopReviewsSection } from "../components/Reviews";
import { Page, Problem } from "../components/Shell";
import { SizeChartTable } from "../components/SizeChart";
import { demoShop } from "../demo";
import { pick, scriptLang } from "../i18n";
import type { Key, Text3 } from "../i18n";
import { useLang } from "../lang";
import { DEFAULT_COUNTRY, formatMoney } from "../money";
import { saveOwnerToken } from "../owner";
import { useServer } from "../server";
import { readGroup, readRef, rememberVisit } from "../visit";

type Profile = {
  name: string;
  template?: string;
  country: string;
  business_type: Text3;
  city: string | null;
  tagline: Text3;
};

// WhatsApp first: that is where most Pakistani buyers already talk to shops.
const CHANNEL_NAMES: Record<string, string> = {
  whatsapp: "WhatsApp",
  instagram: "Instagram",
  messenger: "Messenger",
};
const CHANNEL_ORDER = Object.keys(CHANNEL_NAMES);

/** The shop's profile: bundled for demo shops so it opens at once, then filled in live. */
export function useShop(slug: string) {
  const { state } = useServer();
  const bundled = demoShop(slug);
  const [live, setLive] = useState<{
    profile: Profile;
    channels: ChannelLink[];
    countdowns: Countdown[];
    demo: boolean;
  }>();
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (state !== "online") return;
    let current = true;
    api.shop(slug).then(
      (shop) =>
        current &&
        setLive({
          profile: shop,
          channels: shop.channels,
          countdowns: shop.countdowns,
          demo: shop.is_demo,
        }),
      (error: unknown) => {
        if (current && error instanceof ApiError && error.status === 404)
          setMissing(true);
      },
    );
    return () => {
      current = false;
    };
  }, [slug, state]);

  const profile: Profile | undefined =
    live?.profile ?? (bundled && { ...bundled, country: DEFAULT_COUNTRY });
  return {
    profile: profile && {
      ...profile,
      template: bundled?.template ?? profile.template,
    },
    channels: live?.channels ?? [],
    countdowns: live?.countdowns ?? [],
    // Only the demo shops themselves can be copied; a visitor's copy cannot be copied again.
    copyable: Boolean(bundled) && live?.demo !== false,
    missing: missing && !bundled,
  };
}

/** Bundled demo products at once; the shop's live catalog once the server answers. */
function useProducts(slug: string): ShopProduct[] | undefined {
  const { state } = useServer();
  const [live, setLive] = useState<ShopProduct[]>();

  useEffect(() => {
    if (state !== "online") return;
    let current = true;
    api.products(slug).then(
      (products) => current && setLive(products.map(fromApi)),
      () => {
        // The bundled list, or the empty state, stays on screen.
      },
    );
    return () => {
      current = false;
    };
  }, [slug, state]);

  return live ?? bundledProducts(slug);
}

/** A flash or live sale on the product (feature 8): the price, what is left and the time. */
function SaleLine({
  product,
  country,
}: {
  product: ShopProduct;
  country: string;
}) {
  const { t, lang } = useLang();
  const sale = product.sale;
  const left = useLeft(sale?.ends_at ?? "1970-01-01T00:00:00Z");
  if (!sale || left <= 0) return null;
  const minutes = Math.max(Math.ceil(left / MINUTE), 1);
  return (
    <p className={`product-sale${sale.left > 0 ? "" : " is-gone"}`}>
      <span className="product-sale-name" dir="auto">
        {sale.live ? t("shop.live_number", { number: sale.number }) : sale.name}
      </span>{" "}
      <strong>{formatMoney(sale.price, country, lang)}</strong>{" "}
      <s>{formatMoney(product.listPrice, country, lang)}</s>{" "}
      <span>
        {sale.left > 0
          ? t("shop.sale_left", { count: sale.left })
          : t("shop.sale_gone")}
      </span>{" "}
      <span className="product-sale-time">
        {minutes < 60
          ? t("shop.minutes_left", { minutes })
          : t("shop.hours_left", { hours: Math.floor(minutes / 60) })}
      </span>
    </p>
  );
}

function ProductCard({
  product,
  slug,
  country,
  template,
}: {
  product: ShopProduct;
  slug: string;
  country: string;
  template: string | undefined;
}) {
  const { lang, t } = useLang();
  const text = productText(product, lang);
  const nameLang = text.lang ?? scriptLang(text.name);
  const price = formatMoney(product.price, country, lang);
  const ask = t("catalog.ask_text", { product: text.name });
  return (
    <li
      id={`p-${product.key}`}
      className={`product ${product.inStock ? "" : "is-sold-out"}`}
    >
      <div className={`product-tile tone-${templateTone(template)}`}>
        {product.imageUrl ? (
          <img src={product.imageUrl} alt="" loading="lazy" />
        ) : (
          <span className="product-monogram" lang={nameLang} aria-hidden="true">
            {initials(text.name)}
          </span>
        )}
        {!product.inStock && (
          <span className="product-stamp">{t("catalog.sold_out")}</span>
        )}
      </div>
      <div className="product-body">
        <h3 className="product-name" lang={nameLang} dir="auto">
          {text.name}
        </h3>
        <p className="product-price">
          {product.priceVaries ? t("catalog.from", { price }) : price}
        </p>
        {product.options.length > 0 && (
          <p className="product-options" dir="auto">
            {product.options.join(", ")}
          </p>
        )}
        <SaleLine product={product} country={country} />
        {product.shipsFrom && (
          <p className="product-preorder">
            {t("shop.preorder", { date: dayLabel(product.shipsFrom, lang) })}
          </p>
        )}
        {product.sizeChart && <SizeChartTable chart={product.sizeChart} />}
        <div className="product-actions">
          {product.inStock && product.variants.length > 0 && (
            <AddToCart slug={slug} product={product} name={text.name} />
          )}
          <Link
            to={`/s/${slug}/chat?${new URLSearchParams({ ask })}`}
            className="product-ask"
          >
            <ChatIcon size={16} />
            {t("catalog.ask")}
          </Link>
          {product.shareUrl && (
            <Share url={product.shareUrl} name={text.name} />
          )}
        </div>
      </div>
    </li>
  );
}

/** The product's link with its preview card (feature 116): the phone's share sheet, or the
 * link copied where there is none. */
function Share({ url, name }: { url: string; name: string }) {
  const { t } = useLang();
  const { copied, copy } = useCopy();
  async function share() {
    if (navigator.share) {
      await navigator.share({ title: name, url }).catch(() => undefined);
      return;
    }
    await copy(url);
  }
  return (
    <>
      <button
        type="button"
        className="product-share"
        aria-label={`${t("catalog.share")}: ${name}`}
        onClick={() => void share()}
      >
        {t("catalog.share")}
      </button>
      {copied && (
        <span className="product-in-cart" role="status">
          {t("catalog.link_copied")}
        </span>
      )}
    </>
  );
}

/** One variant into the cart: picked from the options when there are several. */
function AddToCart({
  slug,
  product,
  name,
}: {
  slug: string;
  product: ShopProduct;
  name: string;
}) {
  const { t } = useLang();
  const cart = useCart(slug);
  const choices = product.variants.filter((variant) => variant.inStock);
  const [chosen, setChosen] = useState(choices[0]?.catalogId ?? "");
  const inCart = cart.lines.find((line) => line.item === chosen)?.quantity ?? 0;
  return (
    <div className="add-to-cart">
      {choices.length > 1 && (
        <select
          aria-label={t("shop.option", { name })}
          value={chosen}
          onChange={(e) => setChosen(e.target.value)}
        >
          {choices.map((variant) => (
            <option key={variant.catalogId} value={variant.catalogId}>
              {variant.label}
            </option>
          ))}
        </select>
      )}
      <button
        type="button"
        className="product-add"
        aria-label={`${t("shop.add_to_cart")}: ${name}`}
        onClick={() => cart.add(chosen)}
      >
        {t("shop.add_to_cart")}
      </button>
      {inCart > 0 && (
        <span className="product-in-cart" role="status">
          {t("shop.in_cart", { count: inCart })}
        </span>
      )}
    </div>
  );
}

/** What a friend's link brought: their code for a first order (feature 20), or the group
 * order this visitor is adding a part to (feature 22). */
function VisitNote({ slug }: { slug: string }) {
  const { t } = useLang();
  const [params] = useSearchParams();
  const [kept, setKept] = useState<{
    ref: string | null;
    group: string | null;
  }>({
    ref: null,
    group: null,
  });
  const wanted = params.toString();
  useEffect(() => {
    rememberVisit(slug, new URLSearchParams(wanted));
    setKept({ ref: readRef(slug), group: readGroup(slug) });
  }, [slug, wanted]);
  if (kept.group) {
    return (
      <p className="visit-note" role="status">
        {t("shop.group_adding")}{" "}
        <Link to={`/s/${slug}/g/${kept.group}`}>{t("shop.group_see")}</Link>
      </p>
    );
  }
  if (kept.ref) {
    return (
      <p className="visit-note" role="status">
        {t("shop.friend_code", { code: kept.ref })}
      </p>
    );
  }
  return null;
}

/** What is in the cart, and the way to the checkout. */
function CartBar({ slug }: { slug: string }) {
  const { t } = useLang();
  const { count } = useCart(slug);
  if (count === 0) return null;
  return (
    <div className="cart-bar" role="region" aria-label={t("checkout.cart")}>
      <span>{t("shop.cart_bar", { count })}</span>
      <Link
        to={`/s/${slug}/checkout`}
        className="button button-saffron button-small"
      >
        {t("shop.checkout")}
      </Link>
    </div>
  );
}

/** The shop's categories, in the order the catalog lists them. */
function categoriesOf(products: ShopProduct[]): string[] {
  return [...new Set(products.map((p) => p.category).filter(Boolean))];
}

function categoryName(category: string): string {
  const words = category.replace(/[_-]/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function Catalog({
  slug,
  profile,
  products,
  category,
}: {
  slug: string;
  profile: Profile;
  products: ShopProduct[] | undefined;
  category: string | null;
}) {
  const { t } = useLang();
  // A link from a private reply to a comment (#p-<code>) lands on that product.
  useEffect(() => {
    const target = window.location.hash.slice(1);
    if (products && target.startsWith("p-")) {
      document.getElementById(target)?.scrollIntoView({ block: "center" });
    }
  }, [products]);
  const shown = products?.filter(
    (p) => category === null || p.category === category,
  );
  return (
    <section
      id="collection"
      className="catalog"
      aria-labelledby="catalog-title"
      aria-busy={!products}
    >
      <h2 id="catalog-title" className="section-title store-title">
        {category === null ? t("shop.products") : categoryName(category)}
      </h2>
      {shown && shown.length > 0 ? (
        <ul className="product-grid">
          {shown.map((product) => (
            <ProductCard
              key={product.key}
              product={product}
              slug={slug}
              country={profile.country}
              template={profile.template}
            />
          ))}
        </ul>
      ) : (
        <div className="catalog-empty">
          <span className="catalog-ghost" aria-hidden="true" />
          <span className="catalog-ghost" aria-hidden="true" />
          <span className="catalog-ghost" aria-hidden="true" />
          <p>{t("catalog.empty")}</p>
          <Link to={`/s/${slug}/chat`} className="button button-ink">
            {t("shop.chat_here")}
          </Link>
        </div>
      )}
    </section>
  );
}

/** The category row under the shop's name: all, then each category. */
function CategoryNav({
  categories,
  category,
  choose,
}: {
  categories: string[];
  category: string | null;
  choose: (category: string | null) => void;
}) {
  const { t } = useLang();
  if (categories.length < 2) return null;
  return (
    <nav className="store-nav" aria-label={t("shop.products")}>
      {[null, ...categories].map((name) => (
        <button
          key={name ?? "all"}
          type="button"
          aria-pressed={category === name}
          onClick={() => choose(name)}
        >
          {name === null ? t("store.all") : categoryName(name)}
        </button>
      ))}
    </nav>
  );
}

/** One picture tile per category, the way a shop window groups its stock. */
function CategoryTiles({
  products,
  categories,
  choose,
}: {
  products: ShopProduct[];
  categories: string[];
  choose: (category: string) => void;
}) {
  const { t } = useLang();
  if (categories.length < 2) return null;
  return (
    <ul className="store-tiles">
      {categories.map((name) => {
        const inside = products.filter((p) => p.category === name);
        const cover = inside.find((p) => p.imageUrl)?.imageUrl;
        return (
          <li key={name}>
            <button
              type="button"
              className="store-tile"
              onClick={() => choose(name)}
            >
              {cover && <img src={cover} alt="" loading="lazy" />}
              <span className="store-tile-text">
                <strong>{categoryName(name)}</strong>
                <span>
                  {t("browse.count", { count: inside.length })} ·{" "}
                  {t("store.view")}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** "Try as the owner": the visitor's own three-day copy of this demo shop, signed in. */
function TryAsOwner({ slug }: { slug: string }) {
  const { t } = useLang();
  const { state } = useServer();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function start() {
    setBusy(true);
    setFailed(false);
    try {
      const { token } = await api.tryAsOwner(slug);
      saveOwnerToken(token);
      navigate("/owner");
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <section className="owner-try" aria-labelledby="owner-try-title">
      <KeyIcon size={28} className="owner-try-icon" />
      <div className="owner-try-text">
        <h2 id="owner-try-title">{t("shop.try_owner_title")}</h2>
        <p>{t("shop.try_owner_body")}</p>
        {failed && <p role="alert">{t("shop.try_owner_failed")}</p>}
      </div>
      <button
        type="button"
        className="button button-saffron"
        disabled={busy || state !== "online"}
        onClick={() => void start()}
      >
        {state === "online" ? t("shop.try_owner") : t("chat.waking")}
      </button>
    </section>
  );
}

/** A day as the reader says it: "Fri 6 Nov". */
export function dayLabel(day: string, lang: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString(lang, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** The time left to an end, kept fresh once a minute. */
function useLeft(endsAt: string): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), MINUTE);
    return () => window.clearInterval(timer);
  }, []);
  return Date.parse(endsAt) - now;
}

function CountdownLine({
  item,
  country,
}: {
  item: Countdown;
  country: string;
}) {
  const { t, lang } = useLang();
  const left = useLeft(item.ends_at);
  if (left <= 0) return null;
  const what =
    item.kind === "sale"
      ? item.percent !== null
        ? t("shop.sale_percent", {
            code: item.code ?? "",
            percent: item.percent,
          })
        : t("shop.sale_amount", {
            code: item.code ?? "",
            amount: formatMoney(item.amount ?? 0, country, lang),
          })
      : t("shop.order_by", {
          event: item.event
            ? t(`season.${item.event}` as Key)
            : (item.name ?? ""),
          date: dayLabel(item.ends_at.slice(0, 10), lang),
        });
  return (
    <li className={`countdown is-${item.kind}`}>
      <span className="countdown-what" dir="auto">
        {what}
        {item.expected && (
          <span className="countdown-note"> {t("shop.moon_note")}</span>
        )}
      </span>
      <strong className="countdown-left">
        {left < DAY
          ? t("shop.hours_left", {
              hours: Math.max(Math.floor(left / HOUR), 1),
            })
          : t("shop.days_left", { days: Math.floor(left / DAY) })}
      </strong>
    </li>
  );
}

/** The next big day's order-by deadline and the shop's sales, with the time left. */
function Countdowns({ list, country }: { list: Countdown[]; country: string }) {
  const { t } = useLang();
  if (list.length === 0) return null;
  return (
    <ul className="countdowns" aria-label={t("shop.countdowns")}>
      {list.map((item) => (
        <CountdownLine
          key={`${item.kind}-${item.code ?? item.day}`}
          item={item}
          country={country}
        />
      ))}
    </ul>
  );
}

/** The shop's cart in its header, with how many pieces are in it. */
function CartLink({ slug }: { slug: string }) {
  const { t } = useLang();
  const { count } = useCart(slug);
  return (
    <Link
      to={`/s/${slug}/checkout`}
      className="store-cart"
      aria-label={t("checkout.cart")}
    >
      <BagIcon />
      {count > 0 && <span className="store-cart-count">{count}</span>}
    </Link>
  );
}

function BagIcon() {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M5 8h14l-1 12H6L5 8Zm4 0V6a3 3 0 0 1 6 0v2"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ShopPage() {
  const { slug = "" } = useParams();
  const { lang, t } = useLang();
  const { profile, channels, countdowns, copyable, missing } = useShop(slug);
  const products = useProducts(slug);
  const [category, setCategory] = useState<string | null>(null);

  if (missing) {
    return (
      <Page>
        <Problem message={t("shop.not_found")} />
      </Page>
    );
  }
  if (!profile) {
    return (
      <Page>
        <div className="profile-skeleton" aria-busy="true" />
      </Page>
    );
  }

  const outside = channels
    .filter((c) => c.url && c.channel in CHANNEL_NAMES)
    .sort(
      (a, b) =>
        CHANNEL_ORDER.indexOf(a.channel) - CHANNEL_ORDER.indexOf(b.channel),
    );
  const categories = categoriesOf(products ?? []);
  const pictures = (products ?? []).filter((p) => p.imageUrl).slice(0, 2);
  function choose(name: string | null) {
    setCategory(name);
    document
      .getElementById("collection")
      ?.scrollIntoView({ behavior: "smooth" });
  }
  return (
    <Page wide>
      <div className={`store tone-${templateTone(profile.template)}`}>
        <Countdowns list={countdowns} country={profile.country} />
        <header className="store-head">
          <Link to={`/s/${slug}/chat`} className="store-head-chat">
            <ChatIcon size={18} />
            <span>{t("shop.chat_here")}</span>
          </Link>
          <div className="store-brand">
            <h1
              className="store-name"
              lang={scriptLang(profile.name)}
              dir="auto"
            >
              {profile.name}
            </h1>
            <p className="store-kind">
              {pick(profile.business_type, lang)}
              {profile.city && (
                <span className="profile-city">
                  <PinIcon size={14} />
                  {profile.city}
                </span>
              )}
            </p>
          </div>
          <CartLink slug={slug} />
        </header>
        <CategoryNav
          categories={categories}
          category={category}
          choose={choose}
        />

        <section className="store-hero">
          <div className="store-hero-text">
            <p className="store-hero-line">{pick(profile.tagline, lang)}</p>
            <div className="store-hero-actions">
              <a
                href="#collection"
                className="store-button"
                onClick={(e) => {
                  e.preventDefault();
                  choose(null);
                }}
              >
                {t("store.shop_now")}
              </a>
              {outside.map((c) => (
                <a
                  key={c.channel}
                  href={c.url ?? undefined}
                  className="store-button store-button-quiet"
                  rel="noopener"
                >
                  <SendIcon size={16} className="flip-rtl" />
                  {t("store.order_on", {
                    channel: CHANNEL_NAMES[c.channel] ?? c.channel,
                  })}
                </a>
              ))}
            </div>
          </div>
          {pictures.length > 0 && (
            <div
              className={`store-hero-pictures is-${pictures.length}`}
              aria-hidden="true"
            >
              {pictures.map((p) => (
                <img key={p.key} src={p.imageUrl ?? undefined} alt="" />
              ))}
            </div>
          )}
        </section>

        <VisitNote slug={slug} />
        <CategoryTiles
          products={products ?? []}
          categories={categories}
          choose={choose}
        />
        <Catalog
          slug={slug}
          profile={profile}
          products={products}
          category={category}
        />
        <ShopReviewsSection slug={slug} />
        <CartBar slug={slug} />

        {copyable && <TryAsOwner slug={slug} />}

        <p className="trust-link">
          <Link to={`/s/${slug}/trust`}>{t("shop.trust_link")}</Link>
        </p>
        <Link to={`/s/${slug}/chat`} className="store-chat-fab">
          <ChatIcon size={18} />
          {t("shop.chat_here")}
        </Link>
      </div>
    </Page>
  );
}
