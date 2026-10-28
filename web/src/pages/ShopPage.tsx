import { useEffect, useState } from "react";
import { useCart } from "../cart";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { ChannelLink } from "../api";
import { bundledProducts, fromApi, productText } from "../catalog";
import type { ShopProduct } from "../catalog";
import { Avatar, initials, templateTone } from "../components/Avatar";
import { ChatIcon, KeyIcon, PinIcon, SendIcon } from "../components/Icons";
import { Page, Problem } from "../components/Shell";
import { demoShop } from "../demo";
import { pick, scriptLang } from "../i18n";
import type { Text3 } from "../i18n";
import { useLang } from "../lang";
import { DEFAULT_COUNTRY, formatMoney } from "../money";
import { saveOwnerToken } from "../owner";
import { useServer } from "../server";

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
  const [live, setLive] = useState<{ profile: Profile; channels: ChannelLink[]; demo: boolean }>();
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (state !== "online") return;
    let current = true;
    api.shop(slug).then(
      (shop) => current && setLive({ profile: shop, channels: shop.channels, demo: shop.is_demo }),
      (error: unknown) => {
        if (current && error instanceof ApiError && error.status === 404) setMissing(true);
      },
    );
    return () => {
      current = false;
    };
  }, [slug, state]);

  const profile: Profile | undefined =
    live?.profile ?? (bundled && { ...bundled, country: DEFAULT_COUNTRY });
  return {
    profile: profile && { ...profile, template: bundled?.template ?? profile.template },
    channels: live?.channels ?? [],
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
    <li className={`product ${product.inStock ? "" : "is-sold-out"}`}>
      <div className={`product-tile tone-${templateTone(template)}`}>
        {product.imageUrl ? (
          <img src={product.imageUrl} alt="" loading="lazy" />
        ) : (
          <span className="product-monogram" lang={nameLang} aria-hidden="true">
            {initials(text.name)}
          </span>
        )}
        {!product.inStock && <span className="product-stamp">{t("catalog.sold_out")}</span>}
        <span className="price-tag">
          {product.priceVaries ? t("catalog.from", { price }) : price}
        </span>
      </div>
      <div className="product-body">
        <h3 className="product-name" lang={nameLang} dir="auto">
          {text.name}
        </h3>
        {product.options.length > 0 && (
          <p className="product-options" dir="auto">
            {product.options.join(", ")}
          </p>
        )}
        <div className="product-actions">
          {product.inStock && product.variants.length > 0 && (
            <AddToCart slug={slug} product={product} name={text.name} />
          )}
          <Link to={`/s/${slug}/chat?${new URLSearchParams({ ask })}`} className="product-ask">
            <ChatIcon size={16} />
            {t("catalog.ask")}
          </Link>
        </div>
      </div>
    </li>
  );
}

/** One variant into the cart: picked from the options when there are several. */
function AddToCart({ slug, product, name }: { slug: string; product: ShopProduct; name: string }) {
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

/** What is in the cart, and the way to the checkout. */
function CartBar({ slug }: { slug: string }) {
  const { t } = useLang();
  const { count } = useCart(slug);
  if (count === 0) return null;
  return (
    <div className="cart-bar" role="region" aria-label={t("checkout.cart")}>
      <span>{t("shop.cart_bar", { count })}</span>
      <Link to={`/s/${slug}/checkout`} className="button button-saffron button-small">
        {t("shop.checkout")}
      </Link>
    </div>
  );
}

function Catalog({ slug, profile }: { slug: string; profile: Profile }) {
  const { t } = useLang();
  const products = useProducts(slug);
  return (
    <section className="catalog" aria-labelledby="catalog-title" aria-busy={!products}>
      <h2 id="catalog-title" className="section-title">
        {t("shop.products")}
      </h2>
      {products && products.length > 0 ? (
        <ul className="product-grid">
          {products.map((product) => (
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

export function ShopPage() {
  const { slug = "" } = useParams();
  const { lang, t } = useLang();
  const { profile, channels, copyable, missing } = useShop(slug);

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
    .sort((a, b) => CHANNEL_ORDER.indexOf(a.channel) - CHANNEL_ORDER.indexOf(b.channel));
  return (
    <Page>
      <section className="profile">
        <div className="profile-cover" aria-hidden="true" />
        <div className="profile-body">
          <Avatar name={profile.name} template={profile.template} size="lg" />
          <h1 className="profile-name" lang={scriptLang(profile.name)} dir="auto">
            {profile.name}
          </h1>
          <p className="profile-kind">
            {pick(profile.business_type, lang)}
            {profile.city && (
              <span className="profile-city">
                <PinIcon size={16} />
                {profile.city}
              </span>
            )}
          </p>
          <p className="profile-tagline">{pick(profile.tagline, lang)}</p>
          <div className="profile-actions">
            <Link to={`/s/${slug}/chat`} className="action action-primary">
              <ChatIcon />
              {t("shop.chat_here")}
            </Link>
            {outside.length > 0 && (
              <div className="profile-channels">
                {outside.map((c) => (
                  <a key={c.channel} href={c.url ?? undefined} className="action" rel="noopener">
                    <SendIcon className="flip-rtl" />
                    <span lang="en">{CHANNEL_NAMES[c.channel]}</span>
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      <Catalog slug={slug} profile={profile} />
      <CartBar slug={slug} />

      {copyable && <TryAsOwner slug={slug} />}

      <p className="trust-link">
        <Link to={`/s/${slug}/trust`}>{t("shop.trust_link")}</Link>
      </p>
    </Page>
  );
}
