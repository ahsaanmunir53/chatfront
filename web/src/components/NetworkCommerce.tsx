import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, api } from "../api";
import type {
  Member,
  NetworkBranch,
  NetworkOrder,
  NetworkPayment,
  NetworkSettings,
  OwnerMe,
  OwnerProduct,
  WholesaleListing,
} from "../api";
import { formatMoney, toMajor, toMinor } from "../money";
import type { Lang } from "../i18n";
import { useLang } from "../lang";

type Props = {
  me: OwnerMe;
  token: string;
  products: OwnerProduct[];
  canCatalog: boolean;
  canOrders: boolean;
  canTeam: boolean;
};

function messageOf(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function BranchLabel({ branch }: { branch: NetworkBranch }) {
  const { t } = useLang();
  return (
    <span className="network-branch-label">
      {branch.name} ({branch.code}) {branch.is_default ? "— " + t("network.default") : ""}
      {!branch.active ? " — " + t("network.inactive") : ""}
    </span>
  );
}

function OfferCard({
  listing,
  branches,
  me,
  disabled,
  onPlace,
}: {
  listing: WholesaleListing;
  branches: NetworkBranch[];
  me: OwnerMe;
  disabled: boolean;
  onPlace: (listing: WholesaleListing, data: {
    quantity: number;
    initial_offer_unit_price: number;
    budget_unit_price: number;
    branch_id: string | null;
  }) => Promise<void>;
}) {
  const { t } = useLang();
  const [quantity, setQuantity] = useState(String(listing.minimum_quantity));
  const [offer, setOffer] = useState(String(toMajor(listing.unit_price, me.country)));
  const [budget, setBudget] = useState(String(toMajor(listing.unit_price, me.country)));
  const [branchId, setBranchId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const qty = Number(quantity);
    const opening = toMinor(Number(offer), me.country);
    const ceiling = toMinor(Number(budget), me.country);
    if (
      !Number.isInteger(qty) ||
      qty < listing.minimum_quantity ||
      qty > listing.maximum_quantity ||
      !Number.isFinite(opening) ||
      opening <= 0 ||
      !Number.isFinite(ceiling) ||
      ceiling <= 0 ||
      opening > ceiling
    ) {
      setError(t("network.invalid_offer"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onPlace(listing, {
        quantity: qty,
        initial_offer_unit_price: opening,
        budget_unit_price: ceiling,
        branch_id: branchId || null,
      });
    } catch (reason) {
      setError(messageOf(reason, t("network.error")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="network-card">
      <div className="network-card-head">
        <div>
          <h4>{listing.title}</h4>
          <p className="aside">{listing.seller_name} · {listing.seller_slug}</p>
        </div>
        <strong>{formatMoney(listing.unit_price, me.country, me.lang as Lang)}</strong>
      </div>
      {listing.description && <p>{listing.description}</p>}
      <p className="aside">
        {t("network.quantity_range")}: {listing.minimum_quantity}–{listing.maximum_quantity}
      </p>
      <form className="network-form" onSubmit={(event) => void submit(event)}>
        <label>
          <span>{t("network.quantity")}</span>
          <input type="number" min={listing.minimum_quantity} max={listing.maximum_quantity} step="1"
            value={quantity} onChange={(event) => setQuantity(event.target.value)} required />
        </label>
        <label>
          <span>{t("network.opening_offer")} ({me.currency})</span>
          <input type="number" min="0.01" step="any" value={offer}
            onChange={(event) => setOffer(event.target.value)} required />
        </label>
        <label>
          <span>{t("network.private_budget")} ({me.currency})</span>
          <input type="number" min="0.01" step="any" value={budget}
            onChange={(event) => setBudget(event.target.value)} required />
        </label>
        {branches.some((item) => item.active) && (
          <label>
            <span>{t("network.fulfilment_branch")}</span>
            <select value={branchId} onChange={(event) => setBranchId(event.target.value)}>
              <option value="">{t("network.use_default_branch")}</option>
              {branches.filter((item) => item.active).map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
        )}
        {error && <p role="alert" className="network-error">{error}</p>}
        <button type="submit" className="button button-ink button-small" disabled={disabled || busy}>
          {busy ? t("network.working") : t("network.place_order")}
        </button>
      </form>
    </article>
  );
}

function OrderCard({
  order,
  me,
  role,
  busy,
  onApprove,
  onReject,
  onConfirm,
}: {
  order: NetworkOrder;
  me: OwnerMe;
  role: "buyer" | "seller";
  busy: boolean;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onConfirm: (id: string) => void;
}) {
  const { t } = useLang();
  return (
    <article className="network-card">
      <div className="network-card-head">
        <h4>{order.listing_title}</h4>
        <span className="segment">{order.status.replaceAll("_", " ")}</span>
      </div>
      <p className="aside">
        {role === "buyer" ? t("network.seller") + ": " + order.seller_name
          : t("network.buyer") + ": " + order.buyer_name}
      </p>
      <p>{t("network.quantity")}: {order.quantity} · {t("network.rounds")}: {order.rounds}</p>
      {order.agreed_unit_price !== null && (
        <p>
          {t("network.agreed_price")}: {formatMoney(order.agreed_unit_price, me.country, me.lang as Lang)}
          {order.total !== null
            ? " · " + t("network.total") + ": " + formatMoney(order.total, me.country, me.lang as Lang)
            : ""}
        </p>
      )}
      {role === "seller" && order.status === "awaiting_seller_confirmation" && (
        <div className="network-actions">
          <button type="button" className="button button-ink button-small" disabled={busy}
            onClick={() => onApprove(order.id)}>{t("network.approve")}</button>
          <button type="button" className="button button-small" disabled={busy}
            onClick={() => onReject(order.id)}>{t("network.reject")}</button>
        </div>
      )}
      {role === "buyer" && order.status === "awaiting_buyer_confirmation" && (
        <button type="button" className="button button-ink button-small" disabled={busy}
          onClick={() => onConfirm(order.id)}>{t("network.confirm_deal")}</button>
      )}
      {role === "buyer" && order.status === "awaiting_payment" && (
        <p className="pay-note">{t("network.checkout_not_connected")}</p>
      )}
    </article>
  );
}

export function NetworkCommercePanel({
  me, token, products, canCatalog, canOrders, canTeam,
}: Props) {
  const { t } = useLang();
  const [branches, setBranches] = useState<NetworkBranch[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [settings, setSettings] = useState<NetworkSettings | null>(null);
  const [myListings, setMyListings] = useState<WholesaleListing[]>([]);
  const [marketplace, setMarketplace] = useState<WholesaleListing[]>([]);
  const [buyerOrders, setBuyerOrders] = useState<NetworkOrder[]>([]);
  const [sellerOrders, setSellerOrders] = useState<NetworkOrder[]>([]);
  const [payments, setPayments] = useState<NetworkPayment[]>([]);
  const [branchName, setBranchName] = useState("");
  const [branchCode, setBranchCode] = useState("");
  const [branchCity, setBranchCity] = useState("");
  const [staffId, setStaffId] = useState("");
  const [staffBranch, setStaffBranch] = useState("");
  const [productId, setProductId] = useState("");
  const [variantId, setVariantId] = useState("");
  const [listingTitle, setListingTitle] = useState("");
  const [listingDescription, setListingDescription] = useState("");
  const [unitPrice, setUnitPrice] = useState("");
  const [floorPrice, setFloorPrice] = useState("");
  const [minimumQuantity, setMinimumQuantity] = useState("5");
  const [maximumQuantity, setMaximumQuantity] = useState("500");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const activeProducts = products.filter((item) => item.status === "active");
  const selectedProduct = activeProducts.find((item) => item.id === productId) ?? activeProducts[0];
  const activeBranches = branches.filter((item) => item.active);

  const refresh = useCallback(async () => {
    if (canTeam) {
      const [branchList, team] = await Promise.all([api.networkBranches(token), api.team(token)]);
      setBranches(branchList);
      setMembers(team.filter((item) => item.active));
      setStaffId((current) => current || team.find((item) => item.active)?.id || "");
    }
    if (canCatalog) {
      const [networkSettings, own, discover] = await Promise.all([
        api.networkSettings(token), api.myNetworkListings(token), api.networkListings(token),
      ]);
      setSettings(networkSettings);
      setMyListings(own);
      setMarketplace(discover);
    }
    if (canOrders) {
      const [buy, sell, paid] = await Promise.all([
        api.buyerNetworkOrders(token), api.sellerNetworkOrders(token), api.networkPayments(token),
      ]);
      setBuyerOrders(buy);
      setSellerOrders(sell);
      setPayments(paid);
    }
  }, [canCatalog, canOrders, canTeam, token]);

  useEffect(() => {
    let current = true;
    void refresh().catch((reason: unknown) => {
      if (current) setError(messageOf(reason, t("network.error")));
    });
    return () => { current = false; };
  }, [refresh, t]);

  useEffect(() => {
    if (selectedProduct && !selectedProduct.variants.some((variant) => variant.id === variantId)) {
      setProductId(selectedProduct.id);
      setVariantId(selectedProduct.variants[0]?.id ?? "");
      setListingTitle((current) => current || selectedProduct.name);
    }
  }, [selectedProduct, variantId]);

  async function act(work: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
      await refresh();
      setNotice(success);
    } catch (reason) {
      setError(messageOf(reason, t("network.error")));
    } finally {
      setBusy(false);
    }
  }

  async function addBranch(event: FormEvent) {
    event.preventDefault();
    await act(async () => {
      await api.createNetworkBranch(token, {
        name: branchName.trim(), code: branchCode.trim(), city: branchCity.trim() || undefined,
        is_default: branches.length === 0,
      });
      setBranchName(""); setBranchCode(""); setBranchCity("");
    }, t("network.branch_created"));
  }

  async function setDefaultBranch(id: string) {
    await act(() => api.updateNetworkBranch(token, id, { is_default: true }), t("network.saved"));
  }

  async function assignBranch(event: FormEvent) {
    event.preventDefault();
    await act(() => api.assignStaffBranch(token, staffId, staffBranch || null), t("network.saved"));
  }

  async function publishListing(event: FormEvent) {
    event.preventDefault();
    if (!selectedProduct || !variantId) {
      setError(t("network.choose_variant_first"));
      return;
    }
    const price = Number(unitPrice);
    const floor = floorPrice.trim() ? Number(floorPrice) : null;
    const min = Number(minimumQuantity);
    const max = Number(maximumQuantity);
    if (!Number.isFinite(price) || price <= 0 ||
        (floor !== null && (!Number.isFinite(floor) || floor <= 0 || floor > price)) ||
        !Number.isInteger(min) || !Number.isInteger(max) || min < 1 || max < min) {
      setError(t("network.invalid_listing"));
      return;
    }
    await act(async () => {
      await api.createWholesaleListing(token, {
        product_id: selectedProduct.id, variant_id: variantId, branch_id: staffBranch || null,
        title: listingTitle.trim() || selectedProduct.name, description: listingDescription.trim(),
        unit_price: toMinor(price, me.country),
        minimum_unit_price: floor === null ? null : toMinor(floor, me.country),
        minimum_quantity: min, maximum_quantity: max,
      });
      setListingTitle(""); setListingDescription(""); setUnitPrice(""); setFloorPrice("");
    }, t("network.listing_created"));
  }

  async function placeOrder(listing: WholesaleListing, input: {
    quantity: number;
    initial_offer_unit_price: number;
    budget_unit_price: number;
    branch_id: string | null;
  }) {
    await act(() => api.placeWholesaleOrder(token, listing.id, input), t("network.order_created"));
  }

  async function orderAction(id: string, action: "approve" | "reject" | "confirm") {
    const work = action === "approve" ? () => api.approveNetworkOrder(token, id)
      : action === "reject" ? () => api.rejectNetworkOrder(token, id)
      : () => api.confirmNetworkOrder(token, id);
    await act(work, action === "confirm" ? t("network.payment_prepared") : t("network.saved"));
  }

  return (
    <section data-testid="network-commerce-panel" className="pay-panel network-panel" aria-labelledby="network-panel-title">
      <h2 id="network-panel-title" className="section-title">{t("network.title")}</h2>
      <p className="aside">{t("network.intro")}</p>
      {error && <p role="alert" className="network-error">{error}</p>}
      {notice && <p role="status" className="pay-note">{notice}</p>}

      {canTeam && (
        <section className="network-section" aria-labelledby="network-branches-title">
          <h3 id="network-branches-title" className="pay-block-title">{t("network.branches")}</h3>
          <form className="network-form" onSubmit={(event) => void addBranch(event)}>
            <label><span>{t("network.branch_name")}</span><input data-testid="network-branch-name" value={branchName}
              onChange={(event) => setBranchName(event.target.value)} maxLength={120} required /></label>
            <label><span>{t("network.branch_code")}</span><input data-testid="network-branch-code" value={branchCode}
              onChange={(event) => setBranchCode(event.target.value)} pattern="[A-Za-z0-9_-]+"
              maxLength={40} required /></label>
            <label><span>{t("network.city")}</span><input value={branchCity}
              onChange={(event) => setBranchCity(event.target.value)} maxLength={80} /></label>
            <button data-testid="network-create-branch" type="submit" className="button button-ink button-small" disabled={busy}>{t("network.create_branch")}</button>
          </form>
          <ul className="network-list">
            {branches.map((item) => (
              <li key={item.id}>
                <BranchLabel branch={item} />
                {!item.is_default && item.active && (
                  <button type="button" className="action" disabled={busy}
                    onClick={() => void setDefaultBranch(item.id)}>{t("network.make_default")}</button>
                )}
              </li>
            ))}
          </ul>
          {members.length > 0 && branches.length > 0 && (
            <form className="network-form network-assign" onSubmit={(event) => void assignBranch(event)}>
              <h4>{t("network.staff_assignment")}</h4>
              <label><span>{t("network.staff_member")}</span><select value={staffId}
                onChange={(event) => setStaffId(event.target.value)} required>
                {members.map((member) => <option key={member.id} value={member.id}>{member.name} · {member.role}</option>)}
              </select></label>
              <label><span>{t("network.branch")}</span><select value={staffBranch}
                onChange={(event) => setStaffBranch(event.target.value)}>
                <option value="">{t("network.no_branch")}</option>
                {activeBranches.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select></label>
              <button type="submit" className="button button-ink button-small" disabled={busy}>{t("network.assign_branch")}</button>
            </form>
          )}
        </section>
      )}

      {canCatalog && settings && (
        <section className="network-section" aria-labelledby="network-settings-title">
          <h3 id="network-settings-title" className="pay-block-title">{t("network.wholesale")}</h3>
          <label className="network-toggle"><input type="checkbox" checked={settings.wholesale_enabled}
            disabled={busy} onChange={(event) => void act(
              () => api.saveNetworkSettings(token, event.target.checked), t("network.saved"),
            )} /><span>{t("network.publish_shop")}</span></label>
          <p className="aside">{settings.provider_ready ? t("network.provider_configured") : t("network.provider_missing")}</p>

          <h4 className="pay-block-title">{t("network.create_listing")}</h4>
          <form className="network-form" onSubmit={(event) => void publishListing(event)}>
            <label><span>{t("network.product")}</span><select value={selectedProduct?.id ?? ""}
              onChange={(event) => {
                const item = activeProducts.find((candidate) => candidate.id === event.target.value);
                setProductId(event.target.value); setVariantId(item?.variants[0]?.id ?? "");
                if (item) setListingTitle(item.name);
              }} required>
              {activeProducts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
            <label><span>{t("network.variant")}</span><select value={variantId}
              onChange={(event) => setVariantId(event.target.value)} required>
              {(selectedProduct?.variants ?? []).map((variant) => (
                <option key={variant.id} value={variant.id}>{Object.values(variant.options).join(" · ") || selectedProduct?.name}</option>
              ))}
            </select></label>
            <label><span>{t("network.listing_title")}</span><input value={listingTitle}
              onChange={(event) => setListingTitle(event.target.value)} maxLength={160} required /></label>
            <label><span>{t("network.description")}</span><textarea value={listingDescription}
              onChange={(event) => setListingDescription(event.target.value)} maxLength={2000} rows={2} /></label>
            <div className="network-form-3">
              <label><span>{t("network.unit_price")} ({me.currency})</span><input type="number"
                min="0.01" step="any" value={unitPrice} onChange={(event) => setUnitPrice(event.target.value)} required /></label>
              <label><span>{t("network.private_floor")} ({me.currency})</span><input type="number"
                min="0.01" step="any" value={floorPrice} onChange={(event) => setFloorPrice(event.target.value)} /></label>
              <label><span>{t("network.minimum_quantity")}</span><input type="number" min="1"
                step="1" value={minimumQuantity} onChange={(event) => setMinimumQuantity(event.target.value)} required /></label>
              <label><span>{t("network.maximum_quantity")}</span><input type="number" min="1"
                step="1" value={maximumQuantity} onChange={(event) => setMaximumQuantity(event.target.value)} required /></label>
            </div>
            <label><span>{t("network.branch")}</span><select value={staffBranch}
              onChange={(event) => setStaffBranch(event.target.value)}>
              <option value="">{t("network.no_branch")}</option>
              {activeBranches.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
            <button type="submit" className="button button-ink button-small"
              disabled={busy || !activeProducts.length}>{t("network.publish_listing")}</button>
          </form>

          <h4 className="pay-block-title">{t("network.my_listings")}</h4>
          <ul className="network-list">
            {myListings.map((item) => <li key={item.id}>
              <span>{item.title} · {formatMoney(item.unit_price, me.country, me.lang as Lang)}</span>
              <button type="button" className="action" disabled={busy || !item.active}
                onClick={() => void act(() => api.updateWholesaleListing(token, item.id, { active: false }), t("network.saved"))}>
                {t("network.deactivate")}
              </button>
            </li>)}
          </ul>

          <h4 className="pay-block-title">{t("network.marketplace")}</h4>
          {marketplace.length === 0 ? <p className="aside">{t("network.no_listings")}</p> : (
            <div className="network-grid">{marketplace.map((item) => (
              <OfferCard key={item.id} listing={item} branches={branches} me={me}
                disabled={busy || !canOrders} onPlace={placeOrder} />
            ))}</div>
          )}
        </section>
      )}

      {canOrders && (
        <>
          <section className="network-section" aria-labelledby="network-buyer-orders-title">
            <h3 id="network-buyer-orders-title" className="pay-block-title">{t("network.buyer_orders")}</h3>
            {buyerOrders.length === 0 ? <p className="aside">{t("network.no_orders")}</p> : (
              <div className="network-grid">{buyerOrders.map((order) => (
                <OrderCard key={order.id} order={order} me={me} role="buyer" busy={busy}
                  onApprove={(id) => void orderAction(id, "approve")}
                  onReject={(id) => void orderAction(id, "reject")}
                  onConfirm={(id) => void orderAction(id, "confirm")} />
              ))}</div>
            )}
          </section>
          <section className="network-section" aria-labelledby="network-seller-orders-title">
            <h3 id="network-seller-orders-title" className="pay-block-title">{t("network.seller_orders")}</h3>
            {sellerOrders.length === 0 ? <p className="aside">{t("network.no_orders")}</p> : (
              <div className="network-grid">{sellerOrders.map((order) => (
                <OrderCard key={order.id} order={order} me={me} role="seller" busy={busy}
                  onApprove={(id) => void orderAction(id, "approve")}
                  onReject={(id) => void orderAction(id, "reject")}
                  onConfirm={(id) => void orderAction(id, "confirm")} />
              ))}</div>
            )}
          </section>
          <section className="network-section" aria-labelledby="network-payments-title">
            <h3 id="network-payments-title" className="pay-block-title">{t("network.payments")}</h3>
            {payments.length === 0 ? <p className="aside">{t("network.no_payments")}</p> : (
              <div className="network-grid">{payments.map((item: NetworkPayment) => (
                <article className="network-card" key={item.id}>
                  <h4>{formatMoney(item.amount, me.country, me.lang as Lang)}</h4>
                  <p>{t("network.provider")}: {item.provider}</p>
                  <p>{t("network.platform_fee")}: {formatMoney(item.platform_fee, me.country, me.lang as Lang)}</p>
                  <p>{t("network.seller_share")}: {formatMoney(item.seller_amount, me.country, me.lang as Lang)}</p>
                  <p>{t("network.payment_status")}: {item.status} · {t("network.payout_status")}: {item.payout_status}</p>
                  {item.payout_status === "pending" && <p className="pay-note">{t("network.payout_pending")}</p>}
                </article>
              ))}</div>
            )}
          </section>
        </>
      )}
    </section>
  );
}
