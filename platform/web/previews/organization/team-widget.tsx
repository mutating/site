import { useId, useState, type ReactNode } from "react";
import { Icon } from "../../src/components/UI";
import { PermissionSummary } from "../../src/components/InvitationUI";
import { SectionTabs } from "../../src/components/SectionTabs";
import { rolePermissions } from "../../src/lib/permissions";
import { initials, type Invitation, type Member, type Role } from "./model";
import { useWorkspace } from "./parts";
import { filterMembers, memberCount, memberDirectoryHref, memberNumber, memberPreviewLimit } from "./team-directory-model";
import type { TeamDesign } from "./team-model";

export type TeamPane = "members" | "invitations";
export type MemberSearchState = { value: string; setValue: (value: string) => void };

const roleKeys = { "Создатель": "creator", "Администратор": "admin", "Участник": "user" } as const;
const roleGroups: readonly { role: Role; label: string }[] = [
  { role: "Создатель", label: "Создатель" },
  { role: "Администратор", label: "Администраторы" },
  { role: "Участник", label: "Участники" },
];

function Avatar({ member }: { member: Member }) {
  return <span className="avatar team-avatar" data-color={member.color} aria-hidden="true">{initials(member.name)}</span>;
}

function MemberName({ member }: { member: Member }) {
  return <span className="team-person-name"><strong>{member.name}</strong><small>@{member.handle}</small></span>;
}

function RoleLabel({ role }: { role: Role }) {
  return <span className="team-role" data-role={roleKeys[role]}>{role}</span>;
}

function MemberPermissions({ member }: { member: Member }) {
  const { open } = useWorkspace();
  return <div className="team-details">
    <PermissionSummary values={rolePermissions(roleKeys[member.role])} />
    {member.role !== "Создатель" && <button type="button" className="button secondary compact" onClick={() => open({ kind: "member", id: member.id })}>Изменить права</button>}
  </div>;
}

export function MemberRows({ members, group = false, table = false, after }: {
  members: readonly Member[]; group?: boolean; table?: boolean; after?: ReactNode;
}) {
  return <div className="team-roster">
    {table && <div className="team-table-head" aria-hidden="true"><span>Имя</span><span>Роль</span></div>}
    {members.map((member) => <details key={member.id} className="team-person" data-role={roleKeys[member.role]}>
      <summary className="team-person-summary" data-member={member.id}>
        <Avatar member={member} />
        <MemberName member={member} />
        {!group && <RoleLabel role={member.role} />}
        <span className="team-disclosure-icon"><Icon name="chevron-down" size={15} /></span>
      </summary>
      <MemberPermissions member={member} />
    </details>)}
    {after}
  </div>;
}

export function MemberSearch({ value, setValue }: MemberSearchState) {
  return <label className="team-search"><Icon name="search" size={16} />
    <input type="search" value={value} onChange={(event) => setValue(event.currentTarget.value)}
      placeholder="Найти участника" aria-label="Найти участника" />
  </label>;
}

function ProfilePicker({ members, layout }: { members: readonly Member[]; layout: "avatars" | "master" }) {
  const [selectedId, setSelected] = useState(members[0]?.id);
  const selected = members.find((member) => member.id === selectedId) ?? members[0];
  return selected && <div className="team-profiles" data-layout={layout}>
    <div className="team-profile-picker" role="group" aria-label="Выбрать участника">
      {members.map((member) => <button key={member.id} type="button" aria-pressed={selected.id === member.id}
        aria-label={member.name} data-member={member.id} onClick={() => setSelected(member.id)}>
        <Avatar member={member} />
        <span>{layout === "avatars" ? member.name.split(" ")[0] : member.name}</span>
      </button>)}
    </div>
    <article className="team-profile" aria-label={`Профиль ${selected.name}`}>
      <header><Avatar member={selected} /><MemberName member={selected} /><RoleLabel role={selected.role} /></header>
      <MemberPermissions member={selected} />
    </article>
  </div>;
}

function TeamRoster({ design, members, search, directoryLink }: {
  design: TeamDesign; members: readonly Member[]; search: MemberSearchState; directoryLink: ReactNode;
}) {
  switch (design.roster) {
    case "avatars":
    case "master":
      return <ProfilePicker members={members} layout={design.roster} />;
    case "groups":
      return <div className="team-groups">{roleGroups.map(({ role, label }) => {
        const group = members.filter((member) => member.role === role);
        return group.length > 0 && <section key={role} aria-label={label} data-group={roleKeys[role]}>
          <h3>{label} <span>{group.length}</span></h3>
          <MemberRows members={group} group />
        </section>;
      })}</div>;
    case "columns": {
      const management = members.filter((member) => member.role !== "Участник");
      const participants = members.filter((member) => member.role === "Участник");
      return <div className="team-groups team-group-columns">
        <section aria-label="Управление"><h3>Управление <span>{management.length}</span></h3><MemberRows members={management} /></section>
        <section aria-label="Участники"><h3>Участники <span>{participants.length}</span></h3><MemberRows members={participants} group /></section>
      </div>;
    }
    case "leader": {
      const creators = members.filter((member) => member.role === "Создатель");
      return <div className="team-leader-layout">
        <div className="team-leader"><MemberRows members={creators} /></div>
        <MemberRows members={members.filter((member) => member.role !== "Создатель")} />
      </div>;
    }
    default: {
      const placement = design.memberLink;
      const rows = members.length ? <MemberRows members={members} table={design.roster === "table"} after={placement === "row" && directoryLink} />
        : <p className="team-empty">Участники не найдены.</p>;
      return <>
        {(design.number === 61 || placement) && <div className={`team-member-search${placement === "search" && directoryLink ? " has-directory-link" : ""}`}>
          <MemberSearch {...search} />{placement === "search" && directoryLink}
        </div>}
        {placement === "summary" && directoryLink}
        {placement === "rail" && directoryLink ? <div className="team-list-with-rail">{directoryLink}{rows}</div> : rows}
        {(placement === "text" || placement === "primary" || placement === "footer" || placement === "card" || (placement === "row" && !members.length)) && directoryLink}
      </>;
    }
  }
}

function MemberListLink({ design, total, matched, shown, search }: {
  design: TeamDesign; total: number; matched: number; shown: number; search: string;
}) {
  const placement = design.memberLink!;
  const searching = !!search.trim();
  const remaining = matched - shown;
  const labels = {
    heading: "Список участников", tabs: "Полный список", search: "Весь список", footer: "Весь список",
    text: searching ? "Смотреть результаты" : `Смотреть всех ${memberNumber(total)}`,
    primary: searching ? "Все результаты" : `Все ${memberCount(total)}`,
    row: remaining > 0 ? `Ещё ${memberCount(remaining)}` : "Полный список",
    card: "Полный список участников", summary: "Открыть список", rail: memberNumber(total),
  };
  const detail = placement === "row" && remaining > 0 ? "Открыть полный список"
    : placement === "card" ? memberCount(matched)
    : placement === "rail" ? "Все участники" : null;
  return <div className="team-directory-action" data-placement={placement}>
    {(placement === "summary" || placement === "footer") && <span className="team-directory-range">Показано {memberNumber(shown)} из {memberNumber(matched)}</span>}
    <a className="team-directory-cta" href={memberDirectoryHref(design.number, search)} data-directory-link>
      {placement === "row" && <span className="team-directory-link-icon"><Icon name="users" size={16} /></span>}
      <span className="team-directory-link-label"><strong>{labels[placement]}</strong>{detail && <small>{detail}</small>}</span>
      <span className="team-directory-arrow"><Icon name="arrow" size={16} /></span>
    </a>
  </div>;
}

function InvitationRecord({ invitation }: { invitation: Invitation }) {
  const { open } = useWorkspace();
  const isOpen = invitation.contact === "Ссылка без адресата" || invitation.contact.startsWith("Открытое приглашение");
  return <details className="team-invite">
    <summary>
      <span className="team-person-name">
        <strong>{invitation.contact}</strong>
        {isOpen && <small>{invitation.role} · {invitation.sent}</small>}
      </span>
      <span className="team-invite-status">Ожидает ответа</span>
      <span className="team-disclosure-icon"><Icon name="chevron-down" size={15} /></span>
    </summary>
    <div className="team-details">
      {!isOpen && <p>{invitation.role} · {invitation.sent}</p>}
      <button type="button" className="text-button danger-text" onClick={() => open({ kind: "invitation", id: invitation.id })}>Отозвать приглашение</button>
    </div>
  </details>;
}

function TeamInvitations() {
  const { workspace: { invitations }, open } = useWorkspace();
  return <div className="team-invitations">
    <button type="button" className="team-invite-action" onClick={() => open({ kind: "invite" })}><Icon name="plus" size={16} />Добавить участника</button>
    {invitations.length > 0 ? <div className="team-invite-list">
      {invitations.map((invitation) => <InvitationRecord key={invitation.id} invitation={invitation} />)}
    </div> : <p className="team-empty">Пока нет приглашений.</p>}
  </div>;
}

function SectionHeading({ title, count }: { title: string; count: number }) {
  return <><span>{title}</span><span className="team-count">{count}</span></>;
}

function TeamSections({ design, pane, select, memberSearch }: {
  design: TeamDesign; pane: TeamPane; select: (pane: TeamPane) => void; memberSearch?: MemberSearchState;
}) {
  const { workspace } = useWorkspace();
  const id = useId();
  const [localSearch, setLocalSearch] = useState("");
  const search = memberSearch ?? { value: localSearch, setValue: setLocalSearch };
  const { navigation } = design;
  const matching = filterMembers(workspace.members, search.value);
  const preview = design.memberLink ? matching.slice(0, memberPreviewLimit) : matching;
  const directoryLink = design.memberLink && workspace.members.length > memberPreviewLimit
    ? <MemberListLink design={design} total={workspace.members.length} matched={matching.length} shown={preview.length} search={search.value} /> : null;
  const members = <TeamRoster design={design} members={preview} search={search} directoryLink={directoryLink} />;
  const invitations = <TeamInvitations />;
  const tabs = <SectionTabs id={id} label="Разделы команды" options={[
    { key: "members", label: `Участники ${memberNumber(workspace.members.length)}` },
    { key: "invitations", label: `Приглашения ${workspace.invitations.length}` },
  ]} active={pane} onChange={select} vertical={navigation === "header" || navigation === "rail"} />;

  let content: ReactNode;
  switch (navigation) {
    case "disclosure":
      content = <div className="team-section-disclosures">
        <details open={pane === "members"}><summary><SectionHeading title="Участники" count={workspace.members.length} /><Icon name="chevron-down" size={15} /></summary>{members}</details>
        <details open={pane === "invitations"}><summary><SectionHeading title="Приглашения" count={workspace.invitations.length} /><Icon name="chevron-down" size={15} /></summary>{invitations}</details>
      </div>;
      break;
    case "parallel":
      content = <div className="team-section-columns">
        <section aria-label="Участники"><h3><SectionHeading title="Участники" count={workspace.members.length} /></h3>{members}</section>
        <section aria-label="Приглашения"><h3><SectionHeading title="Приглашения" count={workspace.invitations.length} /></h3>{invitations}</section>
      </div>;
      break;
    case "drawer":
      content = <>
        <div className="team-invite-drawer" id={`${id}-invitations`} hidden={pane !== "invitations"}>{invitations}</div>
        {members}
      </>;
      break;
    default: {
      const panels = <div className="team-panels">
        <div role="tabpanel" id={`${id}-panel-members`} aria-labelledby={`${id}-tab-members`} hidden={pane !== "members"}>{members}</div>
        <div role="tabpanel" id={`${id}-panel-invitations`} aria-labelledby={`${id}-tab-invitations`} hidden={pane !== "invitations"}>{invitations}</div>
      </div>;
      content = navigation === "rail" ? <div className="team-navigation-rail">{tabs}{panels}</div> : panels;
    }
  }

  return <>
    <header className="team-widget-heading">
      <div className="team-title-row">
        <h2 tabIndex={-1}><span className="team-widget-icon"><Icon name="users" size={19} /></span>Команда</h2>
        {design.memberLink === "heading" && pane === "members" && directoryLink}
        {navigation === "header" && tabs}
        {navigation === "drawer" && <button type="button" className="team-drawer-toggle" aria-expanded={pane === "invitations"}
          aria-controls={`${id}-invitations`} onClick={() => select(pane === "members" ? "invitations" : "members")}>
          <SectionHeading title="Приглашения" count={workspace.invitations.length} /><Icon name="chevron-down" size={14} />
        </button>}
      </div>
      {navigation === "top" && (design.memberLink === "tabs"
        ? <div className="team-tabs-with-link">{tabs}{pane === "members" && directoryLink}</div> : tabs)}
    </header>
    {navigation === "bottom" && <footer className="team-tab-footer">{tabs}</footer>}
    <div className="team-widget-content">{content}</div>
  </>;
}

export function TeamWidget({ design, pane, select, memberSearch }: {
  design: TeamDesign; pane: TeamPane; select: (pane: TeamPane) => void; memberSearch?: MemberSearchState;
}) {
  return <section className="organization-widget team-widget" aria-label="Команда" data-variant={design.number}
    data-roster={design.roster} data-navigation={design.navigation} data-member-link={design.memberLink}>
    <TeamSections design={design} pane={pane} select={select} memberSearch={memberSearch} />
  </section>;
}
