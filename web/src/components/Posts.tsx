// Comments on the shop's Instagram and Facebook posts (feature 77). A price question gets the
// product in a private message; a post that names no product waits here for the owner to say
// which one it shows, and its waiting comments are answered as soon as they do.
import { useCallback, useState } from "react";
import { api } from "../api";
import type { OwnerProduct, PostComment, SocialPost } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 15000;
const REASONS = new Set([
  "not_a_question",
  "already_answered",
  "too_old",
  "hourly_limit",
  "which_product",
]);

function statusKey(comment: PostComment): Key {
  if (comment.status === "sent") return "console.posts_status_sent";
  if (comment.status === "failed") return "console.posts_status_failed";
  if (comment.reason && REASONS.has(comment.reason)) {
    return `console.posts_status_${comment.reason}` as Key;
  }
  return "console.posts_status_new";
}

function PostRow({
  token,
  post,
  products,
  onChange,
}: {
  token: string;
  post: SocialPost;
  products: OwnerProduct[];
  onChange: () => void;
}) {
  const { t } = useLang();
  const [busy, setBusy] = useState(false);

  async function choose(productId: string) {
    setBusy(true);
    await api.setPostProduct(token, post.id, productId || null).catch(() => undefined);
    setBusy(false);
    onChange();
  }

  return (
    <li className={`post-item ${post.waiting > 0 ? "is-waiting" : ""}`}>
      <p className="post-head">
        <strong>{t(`console.dash_source.${post.channel}` as Key)}</strong>
        {post.permalink && (
          <a href={post.permalink} target="_blank" rel="noopener noreferrer">
            {t("console.posts_open")}
          </a>
        )}
        {post.waiting > 0 && (
          <span className="post-waiting">
            {t("console.posts_waiting", { count: post.waiting })}
          </span>
        )}
        {post.answered > 0 && <span>{t("console.posts_answered", { count: post.answered })}</span>}
      </p>
      {post.caption && (
        <p className="post-caption" dir="auto">
          {post.caption}
        </p>
      )}
      <label className="post-product">
        <span>{t("console.posts_product")}</span>
        <select
          value={post.product_id ?? ""}
          disabled={busy}
          onChange={(e) => void choose(e.target.value)}
        >
          <option value="">{t("console.posts_no_product")}</option>
          {products
            .filter((product) => product.status === "active")
            .map((product) => (
              <option key={product.id} value={product.id}>
                {product.name}
              </option>
            ))}
        </select>
        {post.linked_by === "caption" && (
          <span className="post-found">{t("console.posts_from_caption")}</span>
        )}
      </label>
    </li>
  );
}

export function PostsPanel({ token, products }: { token: string; products: OwnerProduct[] }) {
  const { t } = useLang();
  const load = useCallback(() => api.posts(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const posts = data?.posts ?? [];
  const comments = data?.comments ?? [];

  return (
    <section className="posts" aria-labelledby="posts-title" aria-busy={!data}>
      <h2 id="posts-title" className="section-title">
        {t("console.posts_title")}
      </h2>
      <p className="posts-lede">{t("console.posts_lede")}</p>
      {data && posts.length === 0 && <p className="posts-empty">{t("console.posts_empty")}</p>}
      {posts.length > 0 && (
        <ul className="post-list">
          {posts.map((post) => (
            <PostRow
              key={post.id}
              token={token}
              post={post}
              products={products}
              onChange={refresh}
            />
          ))}
        </ul>
      )}
      {comments.length > 0 && (
        <details className="post-comments">
          <summary>{t("console.posts_recent")}</summary>
          <ul>
            {comments.map((comment) => (
              <li key={comment.id}>
                <span dir="auto">{comment.text}</span>
                <span className={`post-status is-${comment.status}`}>
                  {t(statusKey(comment))}
                  {comment.product_name && comment.status === "sent" && (
                    <span dir="auto"> ({comment.product_name})</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
