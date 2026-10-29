// The shop's team and its activity log (feature 42): people by role with a sign-in link each,
// removal that ends their access at once, and the audit log checked end to end before it shows.
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import { useCopy } from "../clipboard";
import type { Invite, Member } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { useServer } from "../server";
import { usePolled } from "../usePolled";

const ROLES = ["sales", "packing", "stock", "accounts", "auditor"] as const;
const ACTIVITY_POLL_MS = 10_000;

function LinkCard({ invite }: { invite: Invite }) {
  const { t } = useLang();
  const { copied, copy } = useCopy();
  return (
    <div className="pay-link team-link" role="status">
      <p>{t("console.team_link", { name: invite.member.name })}</p>
      <code dir="ltr">{invite.link}</code>
      <button type="button" className="action" onClick={() => void copy(invite.link)}>
        {copied ? t("console.copied") : t("console.copy")}
      </button>
    </div>
  );
}

function AddMember({ token, onAdded }: { token: string; onAdded: (invite: Invite) => void }) {
  const { t } = useLang();
  const [name, setName] = useState("");
  const [role, setRole] = useState<string>("sales");
  const [phone, setPhone] = useState("");
  const [failed, setFailed] = useState(false);

  async function add(event: FormEvent) {
    event.preventDefault();
    setFailed(false);
    try {
      const invite = await api.addMember(token, {
        name: name.trim(),
        role,
        ...(phone.trim() ? { phone: phone.trim() } : {}),
      });
      setName("");
      setPhone("");
      onAdded(invite);
    } catch {
      setFailed(true);
    }
  }

  return (
    <form className="books-form" onSubmit={(event) => void add(event)}>
      <h3 className="pay-block-title">{t("console.team_add")}</h3>
      <label>
        <span>{t("console.name")}</span>
        <input
          value={name}
          required
          maxLength={120}
          dir="auto"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.team_role")}</span>
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((option) => (
            <option key={option} value={option}>
              {t(`console.role.${option}` as Key)}
            </option>
          ))}
        </select>
      </label>
      <p className="pay-note">{t(`console.role_note.${role}` as Key)}</p>
      <label>
        <span>{t("console.team_phone")}</span>
        <input
          value={phone}
          inputMode="tel"
          maxLength={20}
          dir="ltr"
          onChange={(e) => setPhone(e.target.value)}
        />
      </label>
      <div className="wording-actions">
        <button type="submit" className="button button-ink button-small">
          {t("console.team_add_button")}
        </button>
        {failed && <span role="alert">{t("console.team_failed")}</span>}
      </div>
    </form>
  );
}

export function TeamPanel({
  token,
  onSignedOut,
}: {
  token: string;
  onSignedOut: () => void;
}) {
  const { t } = useLang();
  const { state } = useServer();
  const [team, setTeam] = useState<Member[] | null>(null);
  const [invite, setInvite] = useState<Invite | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutFailed, setSignOutFailed] = useState(false);
  const load = useCallback(() => {
    api.team(token).then(setTeam, () => {
      // Shown again on the next change.
    });
  }, [token]);
  useEffect(() => {
    if (state === "online") load();
  }, [state, load]);

  async function relink(member: Member) {
    setInvite(await api.memberLink(token, member.id).catch(() => null));
  }

  async function remove(member: Member) {
    await api.removeMember(token, member.id).catch(() => undefined);
    setInvite(null);
    load();
  }

  async function signOutAll() {
    if (!window.confirm(t("console.sign_out_all_confirm"))) return;
    setSigningOut(true);
    setSignOutFailed(false);
    try {
      await api.signOutAll(token);
      onSignedOut();
    } catch {
      setSigningOut(false);
      setSignOutFailed(true);
    }
  }

  if (!team) return null;
  return (
    <section className="team-panel" aria-labelledby="team-title">
      <h2 id="team-title" className="section-title">
        {t("console.team_title")}
        <span className="console-count">{team.filter((m) => m.active).length}</span>
      </h2>
      <div className="team-security-actions">
        <button
          type="button"
          className="action"
          disabled={signingOut}
          onClick={() => void signOutAll()}
        >
          {signingOut ? t("console.sign_out_all_busy") : t("console.sign_out_all")}
        </button>
        {signOutFailed && <span role="alert">{t("console.sign_out_all_failed")}</span>}
      </div>
      <div className="pay-columns team-columns">
        <div className="pay-block">
          <ul className="pay-rows">
            {team.map((member) => (
              <li key={member.id} className={member.active ? undefined : "is-removed"}>
                <span dir="auto" className="khata-who">
                  {member.name}
                </span>
                <span className="pay-note">{t(`console.role.${member.role}` as Key)}</span>
                {member.whatsapp && <span className="pay-note">{t("console.team_whatsapp")}</span>}
                {!member.active && <span className="pay-note">{t("console.team_removed")}</span>}
                {member.active && member.role !== "owner" && (
                  <span className="team-actions">
                    <button type="button" className="action" onClick={() => void relink(member)}>
                      {t("console.team_new_link")}
                    </button>
                    <button type="button" className="action" onClick={() => void remove(member)}>
                      {t("console.team_remove")}
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
          {invite && <LinkCard invite={invite} />}
        </div>
        <div className="pay-block">
          <AddMember
            token={token}
            onAdded={(made) => {
              setInvite(made);
              load();
            }}
          />
        </div>
      </div>
    </section>
  );
}

function actionLabel(action: string, t: (key: Key) => string): string {
  const key = `activity.${action.replace(".", "_")}` as Key;
  const label = t(key);
  return label === key ? action : label;
}

export function ActivityPanel({ token }: { token: string }) {
  const { t, lang } = useLang();
  const load = useCallback(() => api.activity(token), [token]);
  const { data: log } = usePolled(load, ACTIVITY_POLL_MS);
  if (!log) return null;
  return (
    <section className="activity-panel" aria-labelledby="activity-title">
      <h2 id="activity-title" className="section-title">
        {t("console.activity_title")}
      </h2>
      <p className={log.intact ? "pay-on" : "pay-off activity-broken"} role="status">
        {log.intact
          ? t("console.activity_intact", { count: log.checked })
          : t("console.activity_broken", { seq: log.broken_at ?? 0 })}
      </p>
      <div className="pay-block">
        <ol className="pay-rows activity">
          {log.entries.map((entry) => (
            <li key={entry.seq}>
              <time dateTime={entry.at}>
                {new Date(entry.at).toLocaleString(lang, {
                  day: "numeric",
                  month: "short",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </time>
              <span>{actionLabel(entry.action, t)}</span>
              <span className="pay-note" dir="auto">
                {entry.actor === "staff" || entry.actor === "agent" || entry.actor === "system"
                  ? t(`console.actor.${entry.actor}` as Key)
                  : entry.actor === "customer"
                    ? t("console.customer_label")
                    : entry.actor}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
